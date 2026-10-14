"""
Tests for Web Dashboard and Health Check API endpoints.
"""

import time
import pytest
from core.web_dashboard import (
    format_uptime,
    get_cluster_stats,
    get_health_status,
    render_dashboard,
)


def test_format_uptime():
    assert format_uptime(45) == "45s"
    assert format_uptime(125) == "2m 5s"
    assert "1h 1m" in format_uptime(3665)
    assert "1d 1h" in format_uptime(90000)


def test_get_health_status():
    start_time = time.time() - 10
    health = get_health_status(agents={}, controllers={}, start_time=start_time)

    assert health["status"] == "ok"
    assert health["service"] == "darkmatter-relay"
    assert health["version"] == "3.8.0"
    assert health["agents_connected"] == 0
    assert health["uptime_seconds"] >= 9.0


def test_get_cluster_stats():
    start_time = time.time() - 5
    stats = get_cluster_stats(agents={}, controllers={}, start_time=start_time)

    assert stats["status"] == "online"
    assert "system" in stats
    assert "cluster" in stats
    assert stats["cluster"]["agents_count"] == 0


def test_render_dashboard():
    start_time = time.time() - 20
    html = render_dashboard(
        agents={},
        controllers={},
        start_time=start_time,
        host="0.0.0.0",
        port=8765,
    )

    assert "<!DOCTYPE html>" in html
    assert "MR ADS" in html
    assert "Dashboard" in html
