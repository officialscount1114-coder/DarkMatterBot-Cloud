"""Tor support: use the free Tor network as the IP source, and rotate the exit IP.

How the rotation works
----------------------
Tor exposes a SOCKS5 port (default 9050) for traffic and a control port (default
9051). Sending ``SIGNAL NEWNYM`` on the control port tells Tor to use a fresh
circuit, which means a new exit IP for the following connections. That is the
same mechanism the collected `ip-changer` / `auto-change-tor-ip` scripts use -
this module implements it natively (raw sockets, no ``stem``, no privoxy) so it
works on Windows as well as Linux/macOS, and so the traffic engine can drive it
directly.

Notes that matter in practice:
  * Tor rate-limits NEWNYM: asking again too soon simply returns the same exit,
    so after every request we wait until the exit IP really changed (or time out)
    before handing control back to the engine.
  * Exit relays are widely blocked by ad networks, so Tor is a fallback for when
    there is no proxy list at all - not a replacement for good proxies.
"""

import contextlib
import ipaddress
import logging
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Services that echo back the caller's IP address (used to confirm a rotation).
IP_ECHO_URLS = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://ifconfig.me/ip",
)

#: Tor ignores NEWNYM if it was asked again sooner than this (seconds).
MIN_ROTATE_INTERVAL = 10

#: Official "tor expert bundle" build - the tor daemon only (no browser UI), which
#: is all MR ADS needs. Published by the Tor Project on dist.torproject.org.
TOR_BUNDLE_VERSION = "15.0.24"
TOR_BUNDLE_BASE = (
    "https://dist.torproject.org/torbrowser/{version}/"
    "tor-expert-bundle-{platform}-{version}.tar.gz"
)


def tor_bundle_platform() -> str:
    """The bundle name for this machine, e.g. ``windows-x86_64``.

    Returns "" when the platform/architecture has no published bundle.
    """
    system = platform.system().lower()
    machine = (platform.machine() or "").lower()

    if system == "windows":
        return "windows-i686" if machine in ("x86", "i386", "i686") else "windows-x86_64"
    if system == "darwin":
        return "macos-aarch64" if machine in ("arm64", "aarch64") else "macos-x86_64"
    if system == "linux":
        if machine in ("aarch64", "arm64"):
            return "linux-aarch64"
        if machine in ("x86", "i386", "i686"):
            return "linux-i686"
        return "linux-x86_64"
    return ""


#: Platform names the Tor Project actually publishes a bundle for.
KNOWN_TOR_BUNDLE_PLATFORMS = (
    "windows-x86_64",
    "windows-i686",
    "macos-aarch64",
    "macos-x86_64",
    "linux-x86_64",
    "linux-i686",
    "linux-aarch64",
)


def tor_bundle_url(platform_name: str = "", version: str = TOR_BUNDLE_VERSION) -> str:
    """Download URL of the official tor expert bundle for a platform.

    Returns "" for a platform the Tor Project does not publish, so the caller
    never builds a URL that would just 404.
    """
    platform_name = platform_name or tor_bundle_platform()
    if platform_name not in KNOWN_TOR_BUNDLE_PLATFORMS:
        return ""
    return TOR_BUNDLE_BASE.format(version=version, platform=platform_name)


def quote_for_control(value: str) -> str:
    """Escape a value the way Tor's control protocol expects (CString).

    The control port takes quoted strings with ``\\`` and ``"`` backslash-escaped;
    sending an unescaped quote silently breaks authentication.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % escaped


def parse_protocolinfo(reply: str) -> dict:
    """Pull the auth methods / cookie file / version out of a PROTOCOLINFO reply.

    Args:
        reply: the raw multi-line reply, e.g.
            ``250-PROTOCOLINFO 1\\n250-AUTH METHODS=COOKIE COOKIEFILE="/x/cookie"\\n250 OK``

    Returns:
        ``{"methods": [...], "cookiefile": "...", "version": "..."}``
    """
    info: dict = {"methods": [], "cookiefile": "", "version": ""}
    for raw_line in (reply or "").splitlines():
        line = raw_line.strip()
        if len(line) > 4 and line[3] in "-+ ":
            line = line[4:]
        for part in line.split():
            if part.startswith("METHODS="):
                info["methods"] = part.split("=", 1)[1].split(",")
            elif part.startswith("COOKIEFILE="):
                info["cookiefile"] = part.split("=", 1)[1].strip('"')
            # PROTOCOLINFO reports it as: VERSION Tor="0.4.8.10"
            elif part.startswith("Tor="):
                info["version"] = part.split("=", 1)[1].strip('"')
            elif part.startswith("version="):
                info["version"] = part.split("=", 1)[1].strip('"')
    return info


def is_valid_port(port: object) -> bool:
    """True when ``port`` is a usable TCP port number."""
    try:
        value = int(port)
    except (TypeError, ValueError):
        return False
    return 1 <= value <= 65535


def looks_like_ip(text: str) -> bool:
    """True when the text is a bare IPv4/IPv6 address."""
    try:
        ipaddress.ip_address((text or "").strip())
        return True
    except ValueError:
        return False


@dataclass
class TorSettings:
    """User-facing Tor options (persisted in settings.json)."""

    enabled: bool = False
    socks_host: str = "127.0.0.1"
    socks_port: int = 9050
    control_host: str = "127.0.0.1"
    control_port: int = 9051
    password: str = ""
    cookie_auth: bool = True
    auto_start: bool = True
    tor_exe: str = ""
    data_dir: str = ""
    #: Stop the campaign after this many IP rotations (0 = keep rotating).
    max_ips: int = 0
    #: Never ask Tor for a new circuit more often than this (Tor rate-limits it).
    min_rotate_seconds: int = MIN_ROTATE_INTERVAL

    @property
    def socks_url(self) -> str:
        """The proxy URL the traffic engines should use."""
        return f"socks5://{self.socks_host}:{self.socks_port}"


@dataclass
class TorStatus:
    """Current state of the local Tor, for the UI and the logs."""

    running: bool = False
    authenticated: bool = False
    version: str = ""
    exit_ip: str = ""
    #: Where that exit IP sits ("DE", "Frankfurt am Main"), so the log can show it.
    exit_country: str = ""
    exit_ip_error: str = ""
    message: str = ""
    managed: bool = False  # started by this app
    rotations: int = 0
    last_rotation: float = field(default=0.0)


def exit_ip_geo(ip: str) -> tuple[str, str]:
    """``(country, city)`` for an IP, from the GeoLite2 database shipped with the app.

    Used both for the log lines and to label the Tor proxy entry, so every
    per-request line can name the country the click left through. No internet
    lookup is involved.
    """
    if not looks_like_ip(ip):
        return "", ""
    try:
        from .proxy_manager import lookup_geoip

        info = lookup_geoip(ip) or {}
    except Exception:  # geoip database/package unavailable
        return "", ""
    # Prefer the readable country name; fall back to the code when the database
    # only has one (common for Tor exit relays).
    country = str(info.get("country") or info.get("countryCode") or "").strip()
    city = str(info.get("city") or "").strip()
    return country, city


def exit_ip_country(ip: str) -> str:
    """Country of an IP as one string, e.g. ``"DE · Frankfurt am Main"``.

    Returns "" when it cannot be resolved.
    """
    country, city = exit_ip_geo(ip)
    return " · ".join(part for part in (country, city) if part)


def find_tor_in(directory: str) -> str:
    """Locate the tor binary inside an extracted bundle/app directory."""
    root = Path(directory)
    if not root.exists():
        return ""
    names = ("tor.exe", "tor") if os.name == "nt" else ("tor", "tor.exe")
    for name in names:
        for path in root.rglob(name):
            if path.is_file():
                return str(path)
    return ""


def download_tor_bundle(
    dest_dir: str, platform_name: str = "", log=None, timeout: int = 120
) -> str:
    """Download and unpack the official tor daemon. Returns the tor binary path.

    The bundle comes from the Tor Project's own distribution host over HTTPS. It
    is *not* GPG-verified here, so the SHA-256 of the downloaded file is reported
    for the record; anyone who wants a signed download can fetch the bundle (or
    Tor Browser) by hand and point ``tor_exe`` at it instead.

    Args:
        dest_dir: folder to unpack into (created if needed)
        platform_name: override for the bundle name (defaults to this machine)
        log: optional ``callable(str)`` for progress messages
        timeout: socket timeout for the download

    Returns:
        Path to the tor binary, or "" on failure.
    """
    import tarfile
    import urllib.error
    import urllib.request

    def say(message: str) -> None:
        if log:
            log(message)
        logging.info(message)

    platform_name = platform_name or tor_bundle_platform()
    if not platform_name:
        say(f"No official Tor build for {platform.system()}/{platform.machine()}.")
        return ""

    url = tor_bundle_url(platform_name)
    target = Path(dest_dir)
    target.mkdir(parents=True, exist_ok=True)
    archive = target / Path(url).name

    # Already installed? Return it instead of downloading again. Re-unpacking over
    # a tor.exe that Tor is currently running fails with "Permission denied" (the
    # file is in use) and the operator is left staring at an error about a Tor
    # that is in fact installed and working.
    existing = find_tor_in(str(target / "tor-expert-bundle")) or find_tor_in(str(target))
    if existing:
        say(f"Tor is already installed, nothing to download: {existing}")
        return existing

    say(f"Downloading Tor ({platform_name}) from the Tor Project ...")
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "MR-ADS/3.8 (+tor setup)"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response, open(
            archive, "wb"
        ) as handle:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                handle.write(chunk)
    except (urllib.error.URLError, OSError) as exc:
        say(f"Tor download failed: {exc}")
        return ""

    try:
        import hashlib

        digest = hashlib.sha256()
        with open(archive, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        say(f"Downloaded {archive.name} ({archive.stat().st_size // 1024} KB), "
            f"sha256 {digest.hexdigest()[:16]}...")
    except OSError:
        pass

    say("Unpacking ...")
    extract_dir = target / "tor-expert-bundle"
    try:
        with tarfile.open(archive, "r:gz") as tar:
            try:
                tar.extractall(extract_dir, filter="data")  # Python 3.12+
            except TypeError:  # older Python: no filter argument
                tar.extractall(extract_dir)
    except (tarfile.TarError, OSError) as exc:
        # A tor.exe that is already running cannot be overwritten, so unpacking
        # fails with "Permission denied" even though a perfectly good install is
        # sitting right there. Use it instead of reporting a failure.
        installed = find_tor_in(str(extract_dir))
        if installed:
            say(
                "Tor files are in use (Tor is running), so the fresh copy was not "
                f"unpacked - using the installed one: {installed}"
            )
            return installed
        say(f"Could not unpack the Tor bundle: {exc}")
        return ""

    tor_exe = find_tor_in(str(extract_dir))
    if not tor_exe:
        say("The bundle did not contain a tor binary.")
        return ""

    say(f"Tor ready: {tor_exe}")
    return tor_exe


@dataclass
class ControlProbe:
    """What the control port looks like right now."""

    reachable: bool = False        # something answered on the port
    authenticated: bool = False    # and we were allowed to drive it
    version: str = ""
    methods: list = field(default_factory=list)
    error: str = ""


class TorError(Exception):
    """Raised when Tor is not reachable or a control command fails."""


class TorController:
    """Tiny client for Tor's control port (PROTOCOLINFO/AUTHENTICATE/SIGNAL)."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9051,
        password: str = "",
        cookie_auth: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.cookie_auth = cookie_auth
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buffer = ""
        self.version = ""
        #: Auth methods Tor advertised, so a failure can say what it wanted.
        self.advertised_methods: list = []
        #: Cookie file Tor pointed at, when it uses cookie authentication.
        self.cookie_file: str = ""

    # ---------- connection ----------
    def connect(self) -> None:
        """Open the control connection and authenticate."""
        if not is_valid_port(self.port):
            raise TorError(f"invalid control port: {self.port!r}")
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
            self._sock.settimeout(self.timeout)
        except OSError as exc:
            raise TorError(
                f"cannot reach Tor's control port {self.host}:{self.port} ({exc})"
            ) from exc

        try:
            info = parse_protocolinfo(self.command("PROTOCOLINFO 1"))
            self.version = info.get("version", "")
            self.advertised_methods = list(info.get("methods") or [])
            self.cookie_file = info.get("cookiefile") or ""
            self._authenticate(info)
        except TorError:
            self.close()
            raise

    def _authenticate(self, info: dict) -> None:
        """Authenticate using the method Tor advertises."""
        methods = info.get("methods") or []
        errors = []

        # 1. A configured password wins when Tor offers password auth.
        if self.password and ("HASHEDPASSWORD" in methods or not methods):
            try:
                self.command("AUTHENTICATE %s" % quote_for_control(self.password))
                return
            except TorError as exc:
                errors.append(str(exc))

        # 2. Cookie auth (Tor Browser's default).
        cookie_file = info.get("cookiefile") or ""
        if self.cookie_auth and cookie_file and os.path.exists(cookie_file):
            try:
                with open(cookie_file, "rb") as handle:
                    cookie = handle.read().strip()
                self.command("AUTHENTICATE %s" % cookie.hex())
                return
            except (OSError, TorError) as exc:
                errors.append(str(exc))

        # 3. Null auth (ControlPort without authentication).
        if "NULL" in methods or not methods:
            try:
                self.command("AUTHENTICATE")
                return
            except TorError as exc:
                errors.append(str(exc))

        detail = "; ".join(errors) if errors else "no supported auth method"
        raise TorError(f"Tor authentication failed ({detail})")

    def close(self) -> None:
        """Close the control connection."""
        if self._sock is not None:
            with contextlib.suppress(Exception):
                self._sock.close()
            self._sock = None

    # ---------- protocol ----------
    def command(self, text: str) -> str:
        """Send one control command and return the full reply."""
        if self._sock is None:
            raise TorError("not connected to Tor")
        try:
            self._sock.sendall((text + "\r\n").encode("utf-8"))
        except OSError as exc:
            raise TorError(f"control connection lost ({exc})") from exc
        return self._read_reply()

    def _read_reply(self) -> str:
        """Read a (possibly multi-line) control reply."""
        lines: list[str] = []
        while True:
            while "\r\n" in self._buffer:
                line, self._buffer = self._buffer.split("\r\n", 1)
                lines.append(line)
                if len(line) >= 4 and line[3] == " ":
                    reply = "\n".join(lines)
                    self._check(reply)
                    return reply
            try:
                chunk = self._sock.recv(4096)
            except OSError as exc:
                raise TorError(f"control read failed ({exc})") from exc
            if not chunk:
                raise TorError("Tor closed the control connection")
            self._buffer += chunk.decode("utf-8", errors="replace")

    @staticmethod
    def _check(reply: str) -> None:
        """Raise when the reply is an error status."""
        for line in reply.splitlines():
            code = line[:3]
            if code and code[0] in "45":
                raise TorError(line.strip())

    # ---------- commands ----------
    def signal_newnym(self) -> None:
        """Ask Tor for a fresh circuit (a new exit IP for new connections)."""
        self.command("SIGNAL NEWNYM")

    def get_version(self) -> str:
        """Tor's version string, straight from the control port."""
        reply = self.command("GETINFO version")
        for line in reply.splitlines():
            if "version=" in line:
                self.version = line.split("version=", 1)[1].strip()
        return self.version


class TorManager:
    """Runs (or attaches to) Tor and rotates the exit IP for the traffic engine."""

    def __init__(self, settings: TorSettings | None = None) -> None:
        self.settings = settings or TorSettings()
        self.status = TorStatus()
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._last_rotation = 0.0

    # ---------- discovery ----------
    @staticmethod
    def find_tor_executable(explicit: str = "") -> str:
        """Locate a tor binary (explicit path, PATH, or a Tor Browser install)."""
        if explicit and os.path.exists(explicit):
            return explicit

        if not explicit:
            found = shutil.which("tor")
            if found:
                return found

        candidates: list[str] = []
        home = Path.home()
        if platform.system() == "Windows":
            candidates += [
                str(home / "Desktop/Tor Browser/Browser/TorBrowser/Tor/tor.exe"),
                str(home / "Desktop/Tor Browser/Browser/TorBrowser/Tor/tor.exe"),
                str(home / "Downloads/tor/tor.exe"),
                r"C:\Tor\tor.exe",
                r"C:\Program Files\Tor\tor.exe",
                r"C:\Program Files (x86)\Tor\tor.exe",
            ]
            # Bundles downloaded by this app, and the runtime folders other tools
            # in this family drop tor into (e.g. "%LOCALAPPDATA%\<app>\runtime\tor").
            candidates.append(str(home / ".mr_ads_tor/tor-expert-bundle/tor/tor.exe"))
            for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")):
                if not base:
                    continue
                try:
                    for found in Path(base).glob("*/runtime/tor/tor.exe"):
                        candidates.append(str(found))
                    for found in Path(base).glob("*/tor/tor.exe"):
                        candidates.append(str(found))
                except OSError:
                    continue
            try:
                for found in (home / ".mr_ads_tor").rglob("tor.exe"):
                    candidates.append(str(found))
            except OSError:
                pass
        elif platform.system() == "Darwin":
            candidates += [
                str(home / "Applications/Tor Browser.app/Contents/Resources/TorBrowser/Tor/tor"),
                "/Applications/Tor Browser.app/Contents/Resources/TorBrowser/Tor/tor",
                "/usr/local/bin/tor",
                "/opt/homebrew/bin/tor",
            ]
        else:
            candidates += ["/usr/bin/tor", "/usr/local/bin/tor", "/usr/sbin/tor"]

        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate
        return ""

    def start(self) -> TorStatus:
        """Start Tor if needed and connect to its control port."""
        with self._lock:
            settings = self.settings

            # Already running and usable? Just attach.
            probe = self._probe_control()
            if probe.authenticated:
                self.status.running = True
                self.status.message = "connected to a running Tor"
                self._refresh_exit_ip()
                return self.status

            # A Tor is already there but will not let us drive it: launching a
            # second one would only fight over the same ports, so explain instead.
            if probe.reachable:
                methods = ", ".join(probe.methods) or "unknown"
                self.status.message = (
                    f"A Tor is already running on {settings.control_host}:"
                    f"{settings.control_port}, but the control port refused us "
                    f"(it wants: {methods}). Either set the Tor control password in "
                    f"Settings, or point MR ADS at a Tor that uses cookie auth "
                    f"(press GET TOR to run our own)."
                )
                logging.info("Tor control port reachable but auth failed: %s (%s)",
                             probe.error, methods)
                return self.status

            # SOCKS up but no control port: traffic would work, rotation would not.
            if self._port_listening(settings.socks_host, settings.socks_port):
                self.status.message = (
                    f"Tor's SOCKS port {settings.socks_host}:{settings.socks_port} is "
                    f"open, but there is no control port on "
                    f"{settings.control_host}:{settings.control_port}. MR ADS needs the "
                    f"control port to change the IP; press GET TOR to run our own Tor."
                )
                return self.status

            if not settings.auto_start:
                self.status.message = (
                    "Tor is not running. Press START TOR to start it (or enable "
                    "auto-start)."
                )
                return self.status

            tor_exe = self.find_tor_executable(settings.tor_exe)
            if not tor_exe:
                self.status.message = (
                    "Tor was not found on this computer. Press GET TOR to download the "
                    "official Tor daemon (about 21 MB, no browser needed), or install "
                    "Tor Browser / the Tor Expert Bundle yourself."
                )
                return self.status

            data_dir = settings.data_dir or str(
                Path.home() / ".mr_ads_tor"
            )
            try:
                self._launch(tor_exe, data_dir)
            except Exception as exc:
                self.status.message = f"could not start Tor: {exc}"
                return self.status

            if self._wait_for_control(timeout=60):
                self.status.running = True
                self.status.managed = True
                self.status.message = "Tor started by MR ADS"
                self._refresh_exit_ip()
            else:
                tail = self._tail_log()
                self.status.message = (
                    "Tor started but its control port did not answer in time"
                    + (f" (tor log: {tail})" if tail else "")
                )
                logging.warning("Tor did not come up: %s", tail)
            return self.status

    def _launch(self, tor_exe: str, data_dir: str) -> None:
        """Write a torrc and start the Tor process."""
        settings = self.settings
        data_path = Path(data_dir)
        data_path.mkdir(parents=True, exist_ok=True)
        torrc = data_path / "torrc"
        self._log_file = data_path / "tor.log"
        # Tor's torrc parser is picky about paths: a backslash inside a quoted value
        # is an escape sequence (so "C:\Users\..." is a config error), and a
        # "C:/..." value is treated as a relative path - which made its own log
        # file undefinable. The reliable form is plain relative names, because tor
        # is started with the data directory as its working directory.
        torrc.write_text(
            "\n".join(
                [
                    f"SocksPort {settings.socks_port}",
                    f"ControlPort {settings.control_port}",
                    "CookieAuthentication 1"
                    if not settings.password
                    else "CookieAuthentication 0",
                    "CookieAuthFile control_auth_cookie",
                    "DataDirectory .",
                    # Allow the exit to change quickly (defaults are much longer).
                    "MaxCircuitDirtiness 60",
                    "NewCircuitPeriod 30",
                    # Log to a file, never to a pipe: an unread pipe fills up and
                    # Tor then blocks before it ever opens its ports.
                    "Log notice file tor.log",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            [tor_exe, "-f", str(torrc)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(data_path),
            creationflags=creation,
        )
        logging.info("Started Tor: %s -f %s", tor_exe, torrc)

    def _tail_log(self, lines: int = 5) -> str:
        """Last lines of tor's own log, for error messages."""
        try:
            with open(self._log_file, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read().strip().splitlines()
            return " | ".join(content[-lines:])
        except (OSError, AttributeError):
            return ""

    def _wait_for_control(self, timeout: int = 60) -> bool:
        """Wait until the control port authenticates."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._probe_control().authenticated:
                return True
            time.sleep(1.0)
        return False

    def _probe_control(self) -> "ControlProbe":
        """Inspect the control port: reachable? authenticated? which auth methods?"""
        probe = ControlProbe()
        controller = TorController(
            host=self.settings.control_host,
            port=self.settings.control_port,
            password=self.settings.password,
            cookie_auth=self.settings.cookie_auth,
        )
        try:
            controller.connect()
            probe.reachable = True
            probe.authenticated = True
            probe.version = controller.version
            self.status.authenticated = True
            self.status.version = controller.version
        except TorError as exc:
            message = str(exc)
            # A connection error means "nothing is listening"; anything else (an
            # auth rejection, a protocol error) means a Tor *is* there but we may
            # not drive it - very different situations for the user.
            probe.reachable = not any(
                marker in message
                for marker in ("cannot reach", "control connection lost", "control read failed")
            )
            probe.error = message
            probe.methods = list(getattr(controller, "advertised_methods", []) or [])
            self.status.authenticated = False
        finally:
            controller.close()
        return probe

    def _port_listening(self, host: str, port: int) -> bool:
        """True when something accepts TCP connections on host:port."""
        try:
            with socket.create_connection((host, int(port)), timeout=3):
                return True
        except OSError:
            return False

    def stop(self) -> None:
        """Stop the Tor process this app started (never a foreign one)."""
        with self._lock:
            if self._process is not None and self.status.managed:
                with contextlib.suppress(Exception):
                    self._process.terminate()
                with contextlib.suppress(Exception):
                    self._process.wait(timeout=10)
                self._process = None
                self.status.running = False
                self.status.managed = False
                self.status.message = "Tor stopped"

    # ---------- rotation ----------
    def current_exit_ip(self, attempts: int = 3, timeout: int = 25) -> str:
        """The IP the world sees when traffic goes through Tor.

        Tor needs a while to build a circuit after it starts, so this first checks
        that its SOCKS port is actually open and then retries: asking once, too
        early, was why the exit IP showed up as "unknown".
        """
        try:
            from curl_cffi import requests as curl_requests
        except ImportError:  # pragma: no cover
            return ""

        if not self._port_listening(self.settings.socks_host, self.settings.socks_port):
            self.status.exit_ip_error = (
                f"Tor's SOCKS port {self.settings.socks_host}:"
                f"{self.settings.socks_port} is not open - Tor may still be starting."
            )
            logging.info(self.status.exit_ip_error)
            return ""

        proxy = self.settings.socks_url
        last_error = ""
        for attempt in range(max(1, attempts)):
            for url in IP_ECHO_URLS:
                try:
                    response = curl_requests.get(
                        url,
                        proxies={"http": proxy, "https": proxy},
                        timeout=timeout,
                        impersonate="chrome",
                    )
                    text = (response.text or "").strip()
                    if looks_like_ip(text):
                        self.status.exit_ip_error = ""
                        return text
                    last_error = f"{url} answered with {text[:40]!r}"
                except Exception as exc:
                    last_error = f"{url}: {exc}"
            if attempt < attempts - 1:
                time.sleep(3)

        self.status.exit_ip_error = (
            "could not read the exit IP through Tor "
            f"({last_error[:120]}) - Tor may still be starting or blocking these requests."
        )
        logging.info("Tor exit IP lookup failed: %s", last_error)
        return ""

    def _refresh_exit_ip(self) -> str:
        """Update the cached exit IP and its country (never raises)."""
        ip = self.current_exit_ip()
        if ip:
            self.status.exit_ip = ip
            self.status.exit_country = exit_ip_country(ip)
        return ip

    def wait_for_exit_ip(self, timeout: float = 90.0) -> str:
        """Wait until the exit IP can be read (Tor bootstraps asynchronously)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            ip = self.current_exit_ip(attempts=1, timeout=15)
            if ip:
                self.status.exit_ip = ip
                self.status.exit_country = exit_ip_country(ip)
                return ip
            time.sleep(3)
        return ""

    def rotate(self, force: bool = False) -> str:
        """Ask Tor for a new circuit and confirm the exit IP really changed.

        Tor rate-limits NEWNYM, so rotating sooner than
        ``settings.min_rotate_seconds`` is not allowed unless ``force`` is set;
        after the signal the exit IP is polled until it differs (or we give up),
        which is what makes a "rotate every N clicks" rule trustworthy.

        Returns:
            The new exit IP, or "" when it could not be confirmed.
        """
        with self._lock:
            now = time.time()
            wait = self.settings.min_rotate_seconds - (now - self._last_rotation)
            if not force and self._last_rotation and wait > 0:
                logging.info("Tor rotation skipped: %.0fs until Tor accepts a new circuit", wait)
                return self.status.exit_ip

            controller = TorController(
                host=self.settings.control_host,
                port=self.settings.control_port,
                password=self.settings.password,
                cookie_auth=self.settings.cookie_auth,
            )
            try:
                controller.connect()
                controller.signal_newnym()
            except TorError as exc:
                self.status.message = f"rotation failed: {exc}"
                logging.warning("Tor rotation failed: %s", exc)
                return ""
            finally:
                controller.close()

            self._last_rotation = time.time()
            self.status.rotations += 1
            self.status.last_rotation = self._last_rotation
            return self._confirm_new_exit(previous=self.status.exit_ip)

    def _confirm_new_exit(self, previous: str, timeout: float = 45.0) -> str:
        """Poll the exit IP until it differs from ``previous``."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            ip = self.current_exit_ip(attempts=1, timeout=20)
            if ip and ip != previous:
                self.status.exit_ip = ip
                self.status.exit_country = exit_ip_country(ip)
                return ip
            time.sleep(2.0)
        logging.info("Tor exit IP did not change within %.0fs", timeout)
        return self.status.exit_ip

    def exit_label(self) -> str:
        """The exit IP with its country, e.g. ``1.2.3.4 (DE · Frankfurt)``.

        Falls back to an explanation when the IP could not be read, so the log
        never just says "unknown" without saying why.
        """
        if self.status.exit_ip:
            return (
                f"{self.status.exit_ip} ({self.status.exit_country})"
                if self.status.exit_country
                else self.status.exit_ip
            )
        if self.status.exit_ip_error:
            return f"unknown - {self.status.exit_ip_error}"
        return "unknown"

    def describe(self) -> str:
        """Short human-readable state line for the activity log."""
        if not self.status.running:
            return self.status.message or "Tor is not running"
        parts = [f"Tor running (v{self.status.version or '?'})"]
        parts.append(f"exit IP {self.exit_label()}")
        if self.status.rotations:
            parts.append(f"{self.status.rotations} rotations")
        return ", ".join(parts)
