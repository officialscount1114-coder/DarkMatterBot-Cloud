"""A campaign must not stop while there is still another IP it could use.

Reported from the field: "5000 / 10000 / unlimited set, but it stops after 100,
90, 150 and says finished." The engine stopped the campaign after ten consecutive
Cloudflare error pages - pages that Cloudflare hands out *per address*. With Tor
(or a proxy pool) the next address is one rotation away, so giving up there ends a
campaign that was only being refused per IP.
"""

from unittest.mock import MagicMock, patch

from core.browser_engine import PlaywrightTrafficEngine
from core.constants import MAX_CONSECUTIVE_ERROR_PAGES
from core.models import ProxyConfig


def make_engine(logs, proxies=None):
    config = MagicMock()
    config.browser.get_executable_path.return_value = None
    config.block_direct = True
    # plain numbers: the engine compares these while it is being constructed
    config.target_url = "https://deshilink.my/VmTZnigp"
    config.total_visits = 0
    config.clicks_per_ip = 1
    config.min_duration = 15
    config.max_duration = 20
    config.threads = 5
    config.browser.max_contexts = 5
    config.browser.fingerprint_rotation_enabled = False
    with patch("core.browser_engine.playwright_available", True):
        with patch("core.browser_engine.async_playwright"):
            engine = PlaywrightTrafficEngine(config, proxies or [])
    engine.on_log = logs.append
    return engine


def _proxy(host, port=8080):
    return ProxyConfig(host=host, port=port, protocol="http")


class TestCanChangeIp:
    def test_a_rotating_source_can_always_change_ip(self):
        engine = make_engine([])
        engine.rotate_hook = lambda: None
        assert engine._can_change_ip is True

    def test_a_pool_can_change_ip(self):
        engine = make_engine([], [_proxy("1.1.1.1"), _proxy("2.2.2.2")])
        assert engine._can_change_ip is True

    def test_a_single_fixed_proxy_cannot(self):
        engine = make_engine([], [_proxy("1.1.1.1")])
        assert engine._can_change_ip is False


class TestCloudflareRunDoesNotEndTheCampaign:
    def test_rotates_and_carries_on_when_another_ip_exists(self):
        logs = []
        engine = make_engine(logs)
        engine.rotate_hook = lambda: None           # Tor-style rotating source
        engine.running = True

        for _ in range(MAX_CONSECUTIVE_ERROR_PAGES):
            engine._note_error_page("Error 1015 rate limited")

        assert engine.running is True, "the campaign must keep running"
        assert engine._pending_rotation is True, "the engine must ask for a new IP"
        assert engine._consecutive_error_pages == 0, "the run counter resets"
        assert any("rotating to another IP" in m for m in logs)

    def test_stops_only_when_there_is_nowhere_else_to_go(self):
        logs = []
        engine = make_engine(logs, [_proxy("1.1.1.1")])   # one fixed address
        engine.running = True

        for _ in range(MAX_CONSECUTIVE_ERROR_PAGES):
            engine._note_error_page("Error 526 origin SSL")

        assert engine.running is False
        assert any("no other IP to move to" in m for m in logs)

    def test_a_run_of_refusals_also_rotates_instead_of_stopping(self):
        # The field log showed exactly this: "Stopping: 14 visits in a row were
        # refused by the site (the site refuses proxy/VPN IPs)" and the campaign
        # ended at 17 attempts with Unlimited selected.
        logs = []
        engine = make_engine(logs)
        engine.rotate_hook = lambda: None
        engine.running = True

        for _ in range(MAX_CONSECUTIVE_ERROR_PAGES):
            engine._note_block_page("the site refuses proxy/VPN IPs")

        assert engine.running is True, "refusals must not end a campaign with IPs left"
        assert engine._pending_rotation is True
        assert engine._consecutive_error_pages == 0
        # A refusal burns the address, so every one of them moves the campaign on
        # instead of waiting for a run of ten.
        assert engine._refusals == MAX_CONSECUTIVE_ERROR_PAGES
        assert any("moving to another IP" in m for m in logs)
        assert not any("no other IP to move to" in m for m in logs)

    def test_refusals_still_stop_when_there_is_no_other_ip(self):
        logs = []
        engine = make_engine(logs, [_proxy("1.1.1.1")])
        engine.running = True
        for _ in range(MAX_CONSECUTIVE_ERROR_PAGES):
            engine._note_block_page("the site refuses proxy/VPN IPs")
        assert engine.running is False
        assert any("no other IP to move to" in m for m in logs)

    def test_a_shorter_run_is_only_reported(self):
        logs = []
        engine = make_engine(logs, [_proxy("1.1.1.1")])
        engine.running = True
        for _ in range(MAX_CONSECUTIVE_ERROR_PAGES - 1):
            engine._note_error_page("Error 526 origin SSL")
        assert engine.running is True
        assert engine._pending_rotation is False
