"""Bulk proxy list parser.

Feed it a paste blob, a file, or any iterable of lines and it yields normalised
proxy records. Built for very large lists (hundreds of thousands of lines and
up), so everything is streamed and batched - nothing is held as one big string
list and no intermediate list is built per line.

Accepted formats (any mix, one per line; commas/whitespace/semicolons also split):

    ip:port
    1.2.3.4:8080:user:pass
    user:pass@ip:port
    http://ip:port
    socks5://user:pass@1.2.3.4:1080
    1.2.3.4:8080           # comments after # or //
    [2001:db8::1]:8080     (IPv6 in brackets)

Anything unparseable is counted as invalid rather than silently kept, so the
import summary is trustworthy.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Optional

IPV4_RE = re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$")
IPV6_RE = re.compile(r"^[0-9A-Fa-f:]{3,45}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$")
ALL_DIGITS_DOTS_RE = re.compile(r"^[0-9.]+$")
SPLIT_RE = re.compile(r"[\s,;|]+")
IPV6_PORT_RE = re.compile(r"^\[([0-9A-Fa-f:]+)\]:(\d{1,5})$")

PROTOCOLS = {
    "http": "http",
    "https": "https",
    "socks4": "socks4",
    "socks4a": "socks4",
    "socks5": "socks5",
    "socks5h": "socks5",
    "socks": "socks5",
}

DEFAULT_PROTOCOL = "http"


@dataclass
class ImportStats:
    """Running totals for one import."""

    read: int = 0
    added: int = 0
    duplicates: int = 0
    invalid: int = 0
    cancelled: bool = False
    invalid_examples: list = field(default_factory=list)

    def as_line(self) -> str:
        parts = [
            f"{self.added:,} added",
            f"{self.duplicates:,} duplicates",
            f"{self.invalid:,} invalid",
            f"{self.read:,} lines",
        ]
        if self.cancelled:
            parts.append("cancelled")
        return " · ".join(parts)


def normalize_protocol(token: str) -> Optional[str]:
    """Map a protocol token ("SOCKS5h", "socks") to a canonical name."""
    return PROTOCOLS.get((token or "").strip().lower())


def _valid_host(host: str) -> bool:
    if not host:
        return False
    if IPV4_RE.match(host) or IPV6_RE.match(host):
        return True
    if "." not in host:
        return False  # a bare word is not a host
    # digits and dots only means someone meant an IP: if it is not a valid one,
    # reject it ("999.999.1.1" must not slip through as a "hostname")
    if ALL_DIGITS_DOTS_RE.match(host):
        return False
    return bool(HOST_RE.match(host))


def _valid_port(port: str) -> bool:
    return port.isdigit() and 1 <= int(port) <= 65535


def parse_entry(raw: str, default_protocol: str = DEFAULT_PROTOCOL) -> Optional[dict]:
    """Parse one line into a proxy record, or None when it is not a proxy.

    Returns keys: ip, port, type, user, password, key (dedupe key), raw.
    """
    if not raw:
        return None
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    # strip trailing comments, but keep "://" intact
    for marker in (" #", "\t#", " //"):
        pos = line.find(marker)
        if pos > 0:
            line = line[:pos].strip()
    if not line:
        return None

    protocol = None
    if "://" in line:
        head, line = line.split("://", 1)
        protocol = normalize_protocol(head)
        if protocol is None:
            return None
        line = line.strip().strip("/")
    if not line:
        return None

    user = password = ""
    if "@" in line:
        creds, line = line.rsplit("@", 1)
        if ":" in creds:
            user, password = creds.split(":", 1)
        else:
            user = creds
        user, password = user.strip(), password.strip()

    line = line.strip().strip("/")
    host = port = ""

    ipv6 = IPV6_PORT_RE.match(line)
    if ipv6:
        host, port = ipv6.group(1), ipv6.group(2)
    else:
        parts = line.split(":")
        parts = [p for p in parts if p != ""]
        if len(parts) == 2:
            host, port = parts
        elif len(parts) >= 4:  # host:port:user:pass
            host, port, extra_user, extra_pass = parts[0], parts[1], parts[2], parts[3]
            user = user or extra_user
            password = password or extra_pass
        else:
            return None

    host, port = host.strip(), port.strip()
    if not _valid_host(host) or not _valid_port(port):
        return None

    protocol = protocol or normalize_protocol(default_protocol) or DEFAULT_PROTOCOL
    return {
        "ip": host,
        "port": port,
        "type": protocol,
        "user": user,
        "password": password,
        "key": f"{host}:{port}",
        "raw": raw.strip(),
    }


def iter_lines_from_text(text: str, chunk_size: int = 1_000_000) -> Iterator[str]:
    """Yield lines from a (possibly huge) paste blob without copying it whole."""
    if not text:
        return
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            nl = text.rfind("\n", start, end)
            if nl != -1:
                end = nl + 1
        block = text[start:end]
        for piece in SPLIT_RE.split(block):
            if piece:
                yield piece
        start = end


def iter_lines_from_file(path: str, chunk_size: int = 1_000_000) -> Iterator[str]:
    """Stream a proxy file line by line, tolerating non-UTF8 encodings."""
    encodings = ["utf-8", "utf-8-sig", "latin-1"]
    last_error: Optional[Exception] = None
    for encoding in encodings:
        try:
            with open(path, "r", encoding=encoding, errors="strict") as handle:
                buffer = ""
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    buffer += chunk
                    *complete, buffer = buffer.split("\n")
                    for line in complete:
                        for piece in SPLIT_RE.split(line):
                            if piece:
                                yield piece
                for piece in SPLIT_RE.split(buffer):
                    if piece:
                        yield piece
            return
        except UnicodeDecodeError as exc:  # try the next encoding
            last_error = exc
            continue
        except OSError:
            raise
    if last_error:
        raise last_error


def count_lines(path: str) -> int:
    """Cheap line count for a progress bar (falls back to 0 on error)."""
    try:
        size = os.path.getsize(path)
        if size == 0:
            return 0
        with open(path, "rb") as handle:
            sample = handle.read(1024 * 1024)
            if not sample:
                return 0
            newlines = sample.count(b"\n") or 1
        return max(1, int(size / max(1, len(sample)) * newlines))
    except OSError:
        return 0


def parse_lines(
    lines: Iterable[str],
    default_protocol: str = DEFAULT_PROTOCOL,
    seen_keys: Optional[set] = None,
    skip_duplicates: bool = True,
    max_items: int = 0,
    cancel: Optional[object] = None,
    on_batch: Optional[Callable[[list, ImportStats], None]] = None,
    batch_size: int = 2000,
) -> ImportStats:
    """Parse ``lines`` into batches, reporting progress through ``on_batch``.

    Args:
        lines: Any iterable of raw text (file stream, text chunks, list).
        default_protocol: Protocol used when a line has none.
        seen_keys: Set of "host:port" already known; updated as we go, so a
            second import skips what the first one added.
        skip_duplicates: When False, duplicates are added again.
        max_items: Stop after this many *added* proxies (0 = unlimited).
        cancel: Optional object with an ``is_set()`` method (threading.Event).
        on_batch: Called with (records, stats) for every batch - used to move
            rows into the UI without blocking the parse.

    Returns:
        ImportStats with read/added/duplicates/invalid totals.
    """
    stats = ImportStats()
    if seen_keys is None:
        seen_keys = set()

    batch: list = []
    for raw in lines:
        if cancel is not None and cancel.is_set():
            stats.cancelled = True
            break

        stats.read += 1
        record = parse_entry(raw, default_protocol)
        if record is None:
            stats.invalid += 1
            if len(stats.invalid_examples) < 5:
                stats.invalid_examples.append(raw.strip()[:60])
            continue

        if record["key"] in seen_keys:
            stats.duplicates += 1
            if skip_duplicates:
                continue
        seen_keys.add(record["key"])

        batch.append(record)
        stats.added += 1

        if len(batch) >= batch_size:
            if on_batch:
                on_batch(batch, stats)
            batch = []
        if max_items and stats.added >= max_items:
            break

    if batch and on_batch:
        on_batch(batch, stats)
    if cancel is not None and cancel.is_set():
        stats.cancelled = True
    return stats


def to_proxy_config(raw: str, default_protocol: str = DEFAULT_PROTOCOL):
    """Build a ProxyConfig from any supported format, keeping the credentials.

    ``ProxyConfig`` is what the tester and the campaign engines actually use. The
    credentials used to be dropped here (and an authenticated line even failed to
    split), so a provider account - "ip:port:user:pass" or "user:pass@ip:port" -
    answered 407 and every proxy looked dead.
    """
    record = parse_entry(raw, default_protocol)
    if record is None:
        return None
    try:
        from .models import ProxyConfig
    except Exception:
        return None
    return ProxyConfig(
        host=record["ip"],
        port=int(record["port"]),
        protocol=record["type"],
        username=record.get("user") or None,
        password=record.get("password") or None,
    )
