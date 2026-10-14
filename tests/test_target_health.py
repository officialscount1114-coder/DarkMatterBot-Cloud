"""Pre-flight target health check.

A Cloudflare error page (526 etc.) is produced by Cloudflare's edge when the
origin is broken, so it cannot be bypassed from the client. The tool's job is to
say so clearly and refuse to burn proxies on a target nobody can visit.
"""

from core.target_health import (
    CLOUDFLARE_ERROR_STATUSES,
    TargetHealth,
    check_target_health,
    classify,
    format_report,
)

CF_526_HTML = """<html><head><title>auction.org | 526: Invalid SSL certificate</title>
</head><body><div class="cf-error-details"><h1>Invalid SSL certificate</h1>
<p>Cloudflare cannot validate the SSL certificate on the origin web server.</p>
<div>Cloudflare Ray ID: <strong>a48f1fb61e221715</strong></div></div></body></html>"""

REAL_PAGE_HTML = """<html><head><title>Offer</title></head>
<body><h1>Welcome</h1><p>Body text of the real landing page.</p></body></html>"""


class TestClassification:
    def test_526_is_reported_as_origin_ssl_problem(self):
        health = classify(526, CF_526_HTML, "auction.org | 526: Invalid SSL certificate")
        assert health.ok is False
        assert health.is_cloudflare_error is True
        assert "526" in health.reason
        # the operator must be told exactly what to change
        assert "Full (strict)" in health.advice
        assert "Origin" in health.advice

    def test_every_cloudflare_status_has_a_reason_and_fix(self):
        for status in CLOUDFLARE_ERROR_STATUSES:
            health = classify(status, "", "")
            assert health.ok is False, status
            assert health.is_cloudflare_error is True, status
            assert health.reason, status
            assert health.advice, status

    def test_error_page_with_a_success_status_is_still_a_problem(self):
        """Some Cloudflare error pages answer with 200."""
        health = classify(200, CF_526_HTML, "auction.org | 526: Invalid SSL certificate")
        assert health.ok is False
        assert health.is_cloudflare_error is True

    def test_healthy_page_is_ok(self):
        health = classify(200, REAL_PAGE_HTML, "Offer", "https://example.com/offer")
        assert health.ok is True
        assert health.status == 200

    def test_timeout_is_reported_without_calling_it_a_cloudflare_error(self):
        health = classify(None, error="timed out")
        assert health.ok is False
        assert health.is_cloudflare_error is False
        assert "No response" in health.reason

    def test_404_points_at_the_link(self):
        health = classify(404, "<html><title>Not found</title></html>")
        assert health.ok is False
        assert "404" in health.reason

    def test_summary_lines(self):
        broken = classify(526, CF_526_HTML, "x | 526: Invalid SSL certificate")
        assert broken.summary().startswith("TARGET PROBLEM")
        assert classify(200, REAL_PAGE_HTML, "Offer").summary().startswith("Target OK")

    def test_report_includes_the_fix_steps(self):
        report = format_report(classify(526, CF_526_HTML, "x | 526: invalid ssl certificate"))
        assert "526" in report
        assert "What to do" in report


class TestProbe:
    def test_unreachable_host_does_not_raise(self):
        import asyncio

        health = asyncio.run(
            check_target_health("http://127.0.0.1:1/never", timeout=3)
        )
        assert isinstance(health, TargetHealth)
        assert health.ok is False
