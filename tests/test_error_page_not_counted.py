"""A Cloudflare error page must never be counted as a delivered visit.

The app used to report "100% delivered" while every visit was landing on a
Cloudflare error page (526 - invalid origin certificate): the error page carries
Cloudflare text and plenty of body text, so the challenge-bypass check treated it
as a resolved challenge. The destination never served a page, so nothing was ever
counted on its side - which is exactly the "the tool shows it but it does not
count" report.
"""

import pytest

from core.constants import (
    MAX_CONSECUTIVE_ERROR_PAGES,
    cloudflare_error_reason,
)
from core.engine import AsyncTrafficEngine
from core.models import ProxyConfig, TrafficConfig


# Captured verbatim from https://auction.org/4/<hash> while the origin
# certificate was broken (Cloudflare error 526).
CF_526_HTML = """<!DOCTYPE html><html><head>
<title>auction.org | 526: Invalid SSL certificate</title></head>
<body>
<div class="cf-error-details cf-error-526">
  <h1>Invalid SSL certificate</h1>
  <p>Error code 526</p>
  <p>Cloudflare cannot validate the SSL certificate on the origin web server.
  The certificate may be expired, self-signed, or may not cover the requested
  domain name.</p>
  <h2>What happened?</h2><h2>What can I do?</h2>
  <p>Visit cloudflare.com for more information.</p>
  <div>Cloudflare Ray ID: <strong>a48f1fb61e221715</strong></div>
  <a href="https://www.cloudflare.com/5xx-error-landing">Cloudflare</a>
</div></body></html>"""

REAL_PAGE_HTML = """<!DOCTYPE html><html><head>
<title>Innovation Toronto</title></head>
<body><h1>Innovation Toronto</h1>
<p>Real scientific discoveries, followed into the worlds they might make.</p>
<script src="/gtag/js"></script><img src="/cover.png">
</body></html>"""


class TestErrorPageDetection:
    def test_526_page_is_detected(self):
        reason = cloudflare_error_reason(CF_526_HTML, "auction.org | 526: Invalid SSL certificate")
        assert reason is not None
        assert "526" in reason or "error page" in reason

    def test_real_page_is_not_flagged(self):
        assert cloudflare_error_reason(REAL_PAGE_HTML, "Innovation Toronto") is None

    def test_detected_from_title_alone(self):
        """Cloudflare error titles are "<domain> | <code>: ..."."""
        assert cloudflare_error_reason("<html></html>", "example.com | 521: Web server is down")

    def test_empty_content_is_not_flagged(self):
        assert cloudflare_error_reason("", "") is None


def make_engine() -> AsyncTrafficEngine:
    return AsyncTrafficEngine(
        config=TrafficConfig(
            target_url="https://auction.org/4/hash",
            max_threads=2,
            total_visits=0,
            min_duration=0,
            max_duration=0,
        ),
        proxies=[],
    )


class TestCampaignReaction:
    def test_error_page_counts_as_failed_and_stops_the_campaign(self):
        engine = make_engine()
        engine.running = True
        logs = []
        engine.on_log = logs.append

        for _ in range(MAX_CONSECUTIVE_ERROR_PAGES):
            engine._note_error_page("Cloudflare 526: invalid origin SSL certificate")

        assert engine.stats.success == 0
        assert engine.running is False, "a broken target must stop the campaign"
        assert any("TARGET IS NOT SERVING A PAGE" in line for line in logs)
        assert any("Stopping the campaign" in line for line in logs)

    def test_the_explanation_is_logged_only_once(self):
        engine = make_engine()
        engine.running = True
        logs = []
        engine.on_log = logs.append

        for _ in range(4):
            engine._note_error_page("Cloudflare 526: invalid origin SSL certificate")

        warnings = [line for line in logs if "TARGET IS NOT SERVING A PAGE" in line]
        assert len(warnings) == 1

    def test_a_delivered_visit_resets_the_run(self):
        engine = make_engine()
        engine.running = True
        engine.on_log = lambda _message: None

        for _ in range(MAX_CONSECUTIVE_ERROR_PAGES - 1):
            engine._note_error_page("Cloudflare error page")
        engine._consecutive_error_pages = 0  # as a delivered visit does

        engine._note_error_page("Cloudflare error page")
        assert engine.running is True, "an isolated error must not end the campaign"
