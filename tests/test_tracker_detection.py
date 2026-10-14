"""Recognising the destination's own counting beacon.

"The page loaded" and "the site recorded the visit" are different things. Ad
networks and analytics only count once their beacon fires, which is why traffic
can look healthy in the tool while the operator's clicks/analytics stay at zero.
These are the real beacons measured on the operator's own campaign target.
"""

import pytest

from core.constants import is_tracker_url

# Taken from a live visit to the campaign target (deshilink.my -> ad network).
REAL_BEACONS = [
    "https://adscool.net/pageview/joywin_media?usr=v1.0%3A14955290625&scr=1366x768",
    "https://www.facebook.com/tr/?id=1085804280795884&ev=PageView&dl=https%3A%2F%2Fjoywin88.live",
    "https://www.google-analytics.com/g/collect?v=2&tid=G-XXXX",
    "https://www.googletagmanager.com/gtag/js?id=G-XXXX",
    "https://connect.facebook.net/signals/config/1085804280795884?v=2.9.419",
    "https://cdn.example.com/beacon/ping",
]


class TestTrackerDetection:
    @pytest.mark.parametrize("url", REAL_BEACONS)
    def test_real_beacons_are_recognised(self, url):
        assert is_tracker_url(url), url

    def test_plain_page_assets_are_not_trackers(self):
        for url in (
            "https://joywin88.live/bd/en/new-register-entry/account",
            "https://img.j9615y.com/jy/h5/9696.1dbba6320d48a115.js",
            "https://fonts.gstatic.com/s/roboto/v30/KFOmCnqEu92Fr1Mu4mxK.woff2",
            "https://example.com/images/logo.png",
        ):
            assert not is_tracker_url(url), url

    def test_empty_url_is_safe(self):
        assert is_tracker_url("") is False
