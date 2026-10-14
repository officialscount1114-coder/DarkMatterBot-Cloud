"""Why a visit failed, in words the operator can act on.

"Attempts 400 / Delivered 0 / Failed 400" gives nothing to change. These are the
errors actually seen in the field, turned into a reason.
"""

import pytest

from core.browser_engine import classify_failure_reason


@pytest.mark.parametrize(
    "message, expected",
    [
        ("page.goto: Timeout 120000ms exceeded", "timed out - source too slow for the page"),
        (
            "page.goto: net::err_aborted, maybe frame was detached?",
            "page load aborted - the destination dropped it",
        ),
        ("Recv failure: Connection was reset", "the destination reset the connection"),
        (
            "status 403 cloudflare",
            "stopped by the target's protection page",
        ),
        (
            "Failed to perform, curl: (7) CONNECT tunnel failed, response 407",
            "the IP source refused the connection",
        ),
        ("net::ERR_NAME_NOT_RESOLVED", "DNS lookup failed"),
        ("net::ERR_CONNECTION_CLOSED", "the destination closed the connection"),
        ("no response from the IP source", "no response from the IP source"),
        ("", "unknown"),
    ],
)
def test_reasons(message, expected):
    assert classify_failure_reason(message) == expected


def test_a_plain_status_failure_keeps_the_status():
    assert classify_failure_reason("status 404") == "status 404"
    assert classify_failure_reason("status 500") == "status 500"
