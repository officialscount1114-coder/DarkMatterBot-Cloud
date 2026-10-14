"""
Tests for ScenarioEngine and SourceUpdater.
"""

import pytest
from core.scenario_engine import ScenarioEngine, ScenarioStep, ScenarioSession
from core.source_updater import SourceUpdater, PUBLIC_SOURCE_ENDPOINTS


def test_scenario_step_initialization():
    step = ScenarioStep(url="https://example.com/about", dwell_time_seconds=4.5)
    assert step.url == "https://example.com/about"
    assert step.dwell_time_seconds == 4.5
    assert not step.success


def test_scenario_engine_link_extraction():
    engine = ScenarioEngine(target_url="https://example.com")
    html_sample = """
    <html>
        <body>
            <a href="/about">About Us</a>
            <a href="/pricing">Pricing</a>
            <a href="https://external.com/page">External</a>
            <a href="/image.png">Logo</a>
            <a href="javascript:void(0)">Click</a>
        </body>
    </html>
    """
    links = engine._extract_internal_links(html_sample, "https://example.com")
    assert "https://example.com/about" in links
    assert "https://example.com/pricing" in links
    assert "https://external.com/page" not in links
    assert "https://example.com/image.png" not in links


def test_source_updater_endpoints():
    updater = SourceUpdater()
    assert len(PUBLIC_SOURCE_ENDPOINTS) > 5
    assert updater.sources_file is not None
