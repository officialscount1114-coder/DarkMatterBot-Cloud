"""
Tests for ProxyAutoPipeline and MR ADS proxy sources.
"""

import pytest
from core.models import ProxyConfig
from core.proxy_auto_pipeline import (
    MR_ADS_API_URL,
    MR_ADS_RAW_SOURCES,
    ProxyAutoPipeline,
)


def test_mr_ads_sources_definition():
    assert "Live (API)" in MR_ADS_RAW_SOURCES
    assert MR_ADS_RAW_SOURCES["Live (API)"] == MR_ADS_API_URL
    assert "TheSpeedX HTTP" in MR_ADS_RAW_SOURCES
    assert "monosans HTTP" in MR_ADS_RAW_SOURCES
    assert "proxifly HTTP" in MR_ADS_RAW_SOURCES
    assert len(MR_ADS_RAW_SOURCES) >= 14


def test_proxy_auto_pipeline_init(tmp_path):
    storage_file = tmp_path / "proxies.json"
    transferred = []

    def on_transfer(proxies):
        transferred.extend(proxies)

    pipeline = ProxyAutoPipeline(
        max_rows=10000,
        storage_file=storage_file,
        on_campaign_transfer=on_transfer,
    )

    assert pipeline.max_rows == 10000
    assert pipeline.storage_file == storage_file

    # Test saving and loading
    test_proxies = [
        ProxyConfig(host="1.2.3.4", port=8080, protocol="http"),
        ProxyConfig(host="5.6.7.8", port=3128, protocol="socks5"),
    ]
    pipeline.live_proxies = test_proxies
    pipeline.save_live_to_disk()

    assert storage_file.exists()

    # Load in new instance
    new_pipeline = ProxyAutoPipeline(storage_file=storage_file)
    assert len(new_pipeline.live_proxies) == 2
    assert new_pipeline.live_proxies[0].host == "1.2.3.4"
