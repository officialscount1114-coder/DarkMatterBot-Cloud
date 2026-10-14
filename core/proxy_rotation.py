"""Proxy rotation: hand out the pool in a shuffled, non-repeating order.

Two things this guarantees that plain ``random.choice`` does not:

* **every proxy gets used** - requests walk a shuffled cycle, so a pool of mixed
  types (http / https / socks4 / socks5), providers and countries is spread
  evenly instead of one fast proxy taking all the traffic;
* **the order is random** - each new cycle is reshuffled, and the interleave
  helper below can also alternate deliberately between types/countries.

Usage::

    rotator = ProxyRotator()
    proxy = rotator.next_from(available)
"""

from __future__ import annotations

import random
from typing import Iterable, Optional


def _identity(proxy) -> str:
    """A stable key for a proxy object (ProxyConfig or a plain string)."""
    host = getattr(proxy, "host", None)
    port = getattr(proxy, "port", None)
    if host is not None:
        return f"{host}:{port}"
    return str(proxy)


def _kind(proxy) -> str:
    for attr in ("type", "protocol", "scheme"):
        value = getattr(proxy, attr, None)
        if value:
            return str(value).lower()
    text = str(proxy).lower()
    for proto in ("socks5", "socks4", "https", "http"):
        if proto in text:
            return proto
    return "other"


def interleave(proxies: Iterable) -> list:
    """Order a pool so consecutive picks differ in type where possible.

    Sorting by type and then round-robin interleaving keeps a http/socks mix from
    being used in blocks - the "random provider / random country" feel the user
    wants comes from shuffling inside each type bucket first.
    """
    buckets: dict = {}
    for proxy in proxies:
        buckets.setdefault(_kind(proxy), []).append(proxy)
    for bucket in buckets.values():
        random.shuffle(bucket)

    ordered: list = []
    keys = list(buckets)
    random.shuffle(keys)
    while any(buckets.values()):
        for key in keys:
            if buckets[key]:
                ordered.append(buckets[key].pop())
    return ordered


class ProxyRotator:
    """Shuffled round-robin over the proxies currently usable.

    ``next_from`` is called with the candidates that are free right now; the
    rotator remembers which of them it already handed out this cycle so nothing
    is reused until the whole set has had a turn.
    """

    def __init__(self, interleave_types: bool = True):
        self._cycle: list = []
        self._position = 0
        self._used: set = set()
        self._interleave_types = interleave_types
        self.served = 0

    def _refill(self, candidates: list) -> None:
        pool = list(candidates)
        if self._interleave_types:
            pool = interleave(pool)
        else:
            random.shuffle(pool)
        self._cycle = pool
        self._position = 0

    def next_from(self, candidates: Iterable) -> Optional[object]:
        """Return the next proxy to use, or None when there is nothing to use."""
        candidates = list(candidates)
        if not candidates:
            return None

        live_keys = {_identity(p) for p in candidates}
        # Drop entries that are no longer candidates (removed / in use elsewhere)
        self._cycle = [p for p in self._cycle if _identity(p) in live_keys]
        if self._position >= len(self._cycle) or not self._cycle:
            self._refill(candidates)

        proxy = self._cycle[self._position]
        self._position += 1
        self._used.add(_identity(proxy))
        self.served += 1
        return proxy

    def cycle_size(self) -> int:
        """How many proxies the current cycle holds."""
        return len(self._cycle)

    def reset(self) -> None:
        """Forget the current cycle (call when the pool changes)."""
        self._cycle = []
        self._position = 0
        self._used.clear()

    def coverage(self) -> float:
        """Share of the current cycle that has been used (0..1)."""
        if not self._cycle:
            return 0.0
        return min(1.0, self._position / len(self._cycle))
