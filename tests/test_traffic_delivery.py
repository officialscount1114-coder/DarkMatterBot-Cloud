"""Regression tests for the traffic-delivery fixes.

Covers the defects behind "the app says 100 traffic done but only 10-15 actually
arrive":

* a campaign target counts DELIVERED visits, not attempts;
* the master hands its proxy pool to the slaves (and the slave reads the flat
  payload the master really sends, so it no longer answers "No target URL");
* the slave accepts both proxy wire formats;
* direct traffic is refused when the proxy pool is empty.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.engine import AsyncTrafficEngine
from core.master_server import MasterServer
from core.models import ProxyConfig, TrafficConfig
from core.slave_controller import SlaveController, _coerce_proxies, _proxy_payload


def make_engine(**overrides) -> AsyncTrafficEngine:
    config = TrafficConfig(
        target_url="https://example.test/promo",
        max_threads=4,
        total_visits=overrides.pop("total_visits", 100),
        min_duration=1,
        max_duration=2,
        **overrides,
    )
    return AsyncTrafficEngine(config=config, proxies=[])


# ---------------------------------------------------------------------------
# Campaign target = delivered visits, not attempts
# ---------------------------------------------------------------------------


class TestCampaignTarget:
    def test_attempts_alone_do_not_finish_the_campaign(self):
        """100 attempts with 15 delivered must keep running.

        This is the exact bug: the campaign used to declare itself complete after
        100 attempts, so a campaign that asked for 100 visits quietly delivered
        whatever happened to succeed.
        """
        engine = make_engine(total_visits=100)
        engine.stats.total_requests = 100
        engine.stats.success = 15

        assert engine._finished() is False

    def test_campaign_finishes_once_target_is_delivered(self):
        engine = make_engine(total_visits=100)
        engine.stats.total_requests = 140
        engine.stats.success = 100

        assert engine._finished() is True

    def test_zero_target_means_run_until_stopped(self):
        engine = make_engine(total_visits=0)
        engine.stats.total_requests = 5000
        engine.stats.success = 0

        assert engine._finished() is False

    def test_unlimited_retries_are_bounded(self):
        """A hopeless proxy pool must not spin forever."""
        engine = make_engine(total_visits=10)
        engine.stats.total_requests = 10 * 10
        engine.stats.success = 0

        assert engine._finished() is True

    def test_delivery_rate_and_aliases(self):
        engine = make_engine()
        engine.stats.total_requests = 40
        engine.stats.success = 10

        assert engine.stats.attempts == 40
        assert engine.stats.delivered == 10
        assert engine.stats.delivery_rate == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Proxy health bookkeeping
# ---------------------------------------------------------------------------


class TestProxyRetirement:
    def test_proxy_is_retired_after_repeated_failures(self):
        engine = make_engine()
        proxy = ProxyConfig(host="10.0.0.1", port=8080)
        engine.proxies = [proxy]
        engine._initial_proxy_count = 1

        for _ in range(4):
            engine._note_proxy_result(proxy, delivered=False)
        assert len(engine.proxies) == 1, "must not retire before the threshold"

        engine._note_proxy_result(proxy, delivered=False)
        assert engine.proxies == []
        assert engine.running is False, "an empty pool must stop the campaign"

    def test_a_delivered_visit_resets_the_failure_streak(self):
        engine = make_engine()
        proxy = ProxyConfig(host="10.0.0.2", port=8080)
        engine.proxies = [proxy]
        engine._initial_proxy_count = 1

        for _ in range(4):
            engine._note_proxy_result(proxy, delivered=False)
        engine._note_proxy_result(proxy, delivered=True)
        for _ in range(4):
            engine._note_proxy_result(proxy, delivered=False)

        assert engine.proxies == [proxy]


# ---------------------------------------------------------------------------
# Direct-traffic safety
# ---------------------------------------------------------------------------


class TestBlockDirectTraffic:
    @pytest.mark.asyncio
    async def test_refuses_to_run_without_proxy_when_blocked(self):
        logs = []
        engine = make_engine(total_visits=0, block_direct=True)
        engine.on_log = logs.append

        await engine.run()

        assert engine.stats.total_requests == 0
        assert any("direct traffic is disabled" in message for message in logs)


# ---------------------------------------------------------------------------
# Master -> slave wiring
# ---------------------------------------------------------------------------


class TestMasterSlaveWiring:
    def test_start_traffic_forwards_the_proxy_pool(self):
        master = MasterServer(secret_key="x" * 40)
        master.set_active_proxies(
            [{"host": "1.2.3.4", "port": 8080, "protocol": "http"}]
        )

        captured = {}
        master.broadcast_command = lambda command_type, params: captured.update(params) or 1

        count = master.start_traffic_on_slaves(
            target_url="https://example.test/x", threads=7
        )

        assert count == 1
        assert captured["proxies"] == [
            {"host": "1.2.3.4", "port": 8080, "protocol": "http"}
        ]
        assert captured["block_direct"] is True
        assert captured["target_url"] == "https://example.test/x"

    @pytest.mark.asyncio
    async def test_slave_reads_the_flat_payload_the_master_sends(self):
        """The master sends target_url at the top level, not under "config"."""
        controller = SlaveController(
            master_host="127.0.0.1",
            master_port=8765,
            secret_key="test_secret_key_at_least_32_characters_long",
            slave_name="test-slave",
            settings={"stats_interval": 1.0, "resource_interval": 10.0},
        )
        controller._send_log = AsyncMock()

        started = []

        async def capture(config):
            started.append(config)

        controller._run_traffic_operation = capture

        await controller._handle_start_traffic(
            {
                "target_url": "https://example.test/promo",
                "threads": 25,
                "duration": 0,
                "min_view_time": 3,
                "max_view_time": 9,
                "block_direct": False,
                "proxies": [{"host": "1.2.3.4", "port": 8080, "protocol": "http"}],
            }
        )
        await asyncio.sleep(0)

        assert started, "traffic operation must start"
        config = started[0]
        assert config.target_url == "https://example.test/promo"
        assert config.max_threads == 25
        assert len(controller._proxies) == 1

    @pytest.mark.asyncio
    async def test_slave_refuses_direct_traffic_without_proxies(self):
        controller = SlaveController(
            master_host="127.0.0.1",
            master_port=8765,
            secret_key="test_secret_key_at_least_32_characters_long",
            slave_name="test-slave",
            settings={"stats_interval": 1.0, "resource_interval": 10.0},
        )
        controller._send_log = AsyncMock()
        controller._proxies = []
        controller._run_traffic_operation = AsyncMock()

        await controller._handle_start_traffic(
            {"target_url": "https://example.test/promo", "block_direct": True}
        )

        controller._run_traffic_operation.assert_not_called()


class TestProxyWireFormats:
    def test_accepts_dict_and_string_forms(self):
        parsed = _coerce_proxies(
            [
                {"host": "1.2.3.4", "port": 8080, "protocol": "http"},
                "5.6.7.8:1080",
                "socks5://9.9.9.9:1080",
                "user:pass@10.10.10.10:3128",
                "garbage",
            ]
        )

        assert [(p.host, p.port, p.protocol) for p in parsed] == [
            ("1.2.3.4", 8080, "http"),
            ("5.6.7.8", 1080, "http"),
            ("9.9.9.9", 1080, "socks5"),
            ("10.10.10.10", 3128, "http"),
        ]
        assert parsed[3].username == "user"
        assert parsed[3].password == "pass"

    def test_round_trips_through_the_payload_format(self):
        proxies = [
            ProxyConfig(host="1.2.3.4", port=8080),
            ProxyConfig(host="5.6.7.8", port=1080, protocol="socks5", username="u", password="p"),
        ]

        assert _coerce_proxies(_proxy_payload(proxies)) == proxies

    def test_unreadable_entries_are_skipped_not_fatal(self):
        assert _coerce_proxies([None, 42, "", {"port": 80}]) == []
