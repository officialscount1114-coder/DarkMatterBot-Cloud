"""Campaign size selector + "N clicks per IP, then switch IP" behaviour.

The destination counts clicks per IP/session, so a fresh IP for every single
click produced one-off hits that never showed up in the count. Clicks are now
grouped: one IP serves a whole batch, then the engine rotates to the next IP.
"""

import pytest

from core.config_builder import TrafficConfigBuilder
from core.engine import AsyncTrafficEngine
from core.models import ProxyConfig, TrafficConfig


def make_proxies(count: int = 3) -> list[ProxyConfig]:
    return [
        ProxyConfig(host=f"10.0.0.{i + 1}", port=8000 + i, protocol="http")
        for i in range(count)
    ]


class TestConfigBuilder:
    def test_visit_target_is_read_from_settings(self):
        config = TrafficConfigBuilder.from_settings(
            {"total_visits": 500, "clicks_per_ip": 20}, "https://example.com"
        )
        assert config.total_visits == 500

    def test_zero_or_missing_visit_target_means_unlimited(self):
        assert TrafficConfigBuilder.from_settings({}, "https://x.test").total_visits == 0

    def test_any_amount_works_not_just_presets(self):
        """The amount box allows any number, not only the preset list."""
        for amount in (1, 7, 999, 1234, 25000):
            config = TrafficConfigBuilder.from_settings(
                {"total_visits": amount, "clicks_per_ip": 20}, "https://x.test"
            )
            assert config.total_visits == amount, amount
        assert (
            TrafficConfigBuilder.from_settings(
                {"total_visits": "0"}, "https://x.test"
            ).total_visits
            == 0
        )

    def test_clicks_per_ip_is_read_from_settings(self):
        config = TrafficConfigBuilder.from_settings(
            {"clicks_per_ip": 25}, "https://example.com"
        )
        assert config.clicks_per_ip == 25

    def test_clicks_per_ip_defaults_when_missing(self):
        assert TrafficConfigBuilder.from_settings({}, "https://x.test").clicks_per_ip == 20


class TestIpBatching:
    def make_engine(self, clicks_per_ip: int) -> AsyncTrafficEngine:
        engine = AsyncTrafficEngine(
            config=TrafficConfig(
                target_url="https://example.com",
                max_threads=1,
                total_visits=0,
                min_duration=0,
                max_duration=0,
                clicks_per_ip=clicks_per_ip,
            ),
            proxies=make_proxies(3),
        )
        engine.on_log = lambda _message: None
        return engine

    @pytest.mark.asyncio
    async def test_one_ip_serves_the_whole_batch(self):
        engine = self.make_engine(clicks_per_ip=20)

        used = [(await engine._acquire_proxy()) for _ in range(20)]

        first = (used[0].host, used[0].port)
        assert all((p.host, p.port) == first for p in used), (
            "all 20 clicks must go out through the same IP"
        )
        assert engine._sticky_uses == 20

    @pytest.mark.asyncio
    async def test_engine_switches_ip_after_the_batch(self):
        engine = self.make_engine(clicks_per_ip=5)

        used = [(await engine._acquire_proxy()) for _ in range(6)]

        first = (used[0].host, used[0].port)
        assert all((p.host, p.port) == first for p in used[:5])
        assert (used[5].host, used[5].port) != first, "click 6 must use a new IP"
        assert engine._sticky_uses == 1

    @pytest.mark.asyncio
    async def test_ip_rotation_is_logged(self):
        engine = self.make_engine(clicks_per_ip=3)
        logs: list[str] = []
        engine.on_log = logs.append

        for _ in range(4):
            await engine._acquire_proxy()

        assert any("IP rotated" in line for line in logs)
        # the position inside the batch is visible on every click
        assert any("/3]" in line for line in logs)

    @pytest.mark.asyncio
    async def test_every_click_mode_still_rotates(self):
        engine = self.make_engine(clicks_per_ip=1)

        used = [(await engine._acquire_proxy()) for _ in range(4)]

        assert len({(p.host, p.port) for p in used}) > 1 or len(engine.proxies) == 1

    @pytest.mark.asyncio
    async def test_a_retired_ip_is_replaced_immediately(self):
        engine = self.make_engine(clicks_per_ip=20)
        first = await engine._acquire_proxy()

        # the IP dies while it is the active one
        engine.proxies = [
            p for p in engine.proxies if (p.host, p.port) != (first.host, first.port)
        ]

        nxt = await engine._acquire_proxy()
        assert (nxt.host, nxt.port) != (first.host, first.port)
