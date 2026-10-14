"""Refusal pages must not be counted as delivered visits.

Campaign targets refuse datacenter/proxy visitors with a perfectly normal HTTP
200 page ("Anonymous Proxy detected."). Counting that as a delivery is why the
tool's numbers grew while the site's own analytics recorded nothing.
"""

from core.constants import CLICK_HOP_MARKERS, block_page_reason, cloudflare_error_reason

REAL_PAGE = """
<html><head><title>Join Joywin88 Today - Claim Your Welcome Bonus Now!</title></head>
<body><form><input name="username"><input name="password"></form></body></html>
"""


class TestVisibleTextOnly:
    """The detector is fed the page's *visible* text, not the raw HTML.

    Offer pages ship scripts and template strings containing phrases like "proxy
    detected" or "access denied". Matching the raw HTML turned ordinary, perfectly
    good visits into "the site refuses proxy/VPN IPs" failures - which is what the
    operator saw as "failed ashtese" while the page itself was fine.
    """

    def test_the_engine_reads_the_page_body_not_the_scripts(self):
        import inspect

        from core import browser_engine as module

        source = inspect.getsource(module.PlaywrightTrafficEngine)
        assert "_visible_page_text(page)" in source, (
            "the block-page check must use the visible page text"
        )


class TestBlockPageReason:
    def test_anonymous_proxy_page_is_detected(self):
        reason = block_page_reason("Anonymous Proxy detected.", "")
        assert reason
        assert "proxy" in reason.lower()

    def test_proxy_message_in_the_title_is_detected(self):
        assert block_page_reason("", "Anonymous Proxy detected.")

    def test_other_refusals_are_detected(self):
        for body in (
            "<h1>Access Denied</h1>",
            "Your IP has been blocked",
            "Unusual traffic from your computer network",
            "Please enable JavaScript and cookies to continue",
        ):
            assert block_page_reason(body, ""), body

    def test_a_real_page_is_not_flagged(self):
        assert block_page_reason(REAL_PAGE, "Join Joywin88 Today") is None

    def test_empty_content_is_not_flagged(self):
        assert block_page_reason("", "") is None

    def test_cloudflare_error_detection_still_works(self):
        assert cloudflare_error_reason("<div class='cf-error-details'>", "")


class TestClickHops:
    def test_the_monetisation_hop_of_this_funnel_is_known(self):
        """The paid hop between the short link and the offer."""
        assert any("profitableratecpmnetwork" in hop for hop in CLICK_HOP_MARKERS)

    def test_markers_are_lowercase_for_host_matching(self):
        assert all(hop == hop.lower() for hop in CLICK_HOP_MARKERS)
