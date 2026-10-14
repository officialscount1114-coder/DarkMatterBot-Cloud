"""
Tests for Multi-Link Campaigns and Strict IP Rotation (4-5 clicks per link per IP).
"""

import pytest
from core.engine import AsyncTrafficEngine
from core.models import ProxyConfig, TrafficConfig


def test_multi_link_click_distribution():
    links = [
        "https://example.com/link-1",
        "https://example.com/link-2",
        "https://example.com/link-3",
        "https://example.com/link-4",
    ]
    config = TrafficConfig(
        target_url=links[0],
        target_urls=links,
        max_threads=1,
        total_visits=100,
        min_duration=1,
        max_duration=1,
        clicks_per_link_per_ip=4,
    )
    proxies = [
        ProxyConfig(host="10.0.0.1", port=8080),
        ProxyConfig(host="10.0.0.2", port=8080),
    ]

    engine = AsyncTrafficEngine(config=config, proxies=proxies)

    assert len(engine.target_urls) == 4
    assert engine._clicks_per_link == 4

    # Simulate 4 clicks on Link 1
    for _ in range(4):
        url = engine._get_next_target_url()
        assert url == "https://example.com/link-1"

    # Next 4 clicks should be on Link 2
    for _ in range(4):
        url = engine._get_next_target_url()
        assert url == "https://example.com/link-2"

    # Next 4 clicks should be on Link 3
    for _ in range(4):
        url = engine._get_next_target_url()
        assert url == "https://example.com/link-3"

    # Next 4 clicks should be on Link 4
    for _ in range(4):
        url = engine._get_next_target_url()
        assert url == "https://example.com/link-4"

    # After Link 4 finishes, it loops back to Link 1 for the next cycle
    url = engine._get_next_target_url()
    assert url == "https://example.com/link-1"


@pytest.mark.asyncio
async def test_dynamic_proxy_update():
    config = TrafficConfig(
        target_url="https://example.com",
        max_threads=1,
        total_visits=10,
        min_duration=1,
        max_duration=1,
    )
    initial_proxies = [ProxyConfig(host="1.1.1.1", port=8080)]
    engine = AsyncTrafficEngine(config=config, proxies=initial_proxies)

    assert len(engine.proxies) == 1

    # Inject new proxies dynamically
    new_batch = [
        ProxyConfig(host="1.1.1.1", port=8080),  # Duplicate, should be ignored
        ProxyConfig(host="2.2.2.2", port=8080),
        ProxyConfig(host="3.3.3.3", port=8080),
    ]
    await engine.update_proxies(new_batch)

    assert len(engine.proxies) == 3
