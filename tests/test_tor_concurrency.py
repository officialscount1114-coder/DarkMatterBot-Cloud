"""Tor campaigns must run several visits in parallel.

One proxy entry used to mean one browser context. For a proxy *list* that is
right, but Tor's local SOCKS port is an entry point to a pool of exit IPs, so
capping at one context left a Tor campaign running a single visit at a time
("1 ACTIVE") while the machine sat idle.
"""

import pytest

from core.browser_engine import PlaywrightTrafficEngine
from core.models import ProxyConfig
from tests.fixtures.mock_responses import build_traffic_config


def make_engine(proxies, max_contexts=5, with_hook=False):
    config = build_traffic_config()
    config.browser.max_contexts = max_contexts
    engine = PlaywrightTrafficEngine(config, proxies)
    if with_hook:
        async def hook():  # pragma: no cover - only presence matters here
            return None

        engine.rotate_hook = hook
    return engine


def proxy(host, port=9050):
    return ProxyConfig(host=host, port=port, protocol="socks5")


class TestContextCount:
    def test_one_context_per_proxy_entry_for_a_proxy_list(self):
        engine = make_engine([proxy("10.0.0.1", 8000), proxy("10.0.0.2", 8001)])
        assert engine._desired_context_count() == 2

    def test_local_socks_entry_is_not_capped(self):
        """Tor's SOCKS port is one entry but many exit IPs."""
        engine = make_engine([proxy("127.0.0.1", 9050)])
        assert engine._desired_context_count() == 5

    def test_localhost_and_ipv6_loopback_count_too(self):
        for host in ("localhost", "::1"):
            engine = make_engine([proxy(host, 9050)])
            assert engine._desired_context_count() == 5, host

    def test_rotation_hook_means_the_ip_changes(self):
        engine = make_engine([proxy("203.0.113.9", 8080)], with_hook=True)
        assert engine._desired_context_count() == 5

    def test_never_returns_less_than_one(self):
        engine = make_engine([])
        assert engine._desired_context_count() >= 1
