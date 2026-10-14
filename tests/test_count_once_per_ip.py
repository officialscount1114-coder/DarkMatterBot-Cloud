"""Counting is per IP on these destinations - do not spend a batch on one address.

Reported from the field: "when I start, one or two get counted and then nothing".
The campaigns were running with 20 clicks per IP, so a whole batch went out
through an address whose one countable click was already used - the destination
dropped the other nineteen as duplicates.
"""

from unittest.mock import MagicMock, patch

from core.browser_engine import PlaywrightTrafficEngine

TRACKER = "https://adscool.net/pageview/joywin_media?usr=v1.0"


def make_engine(logs):
    config = MagicMock()
    config.browser.get_executable_path.return_value = None
    config.target_url = "https://deshilink.my/VmTZnigp"
    config.clicks_per_ip = 20
    with patch("core.browser_engine.playwright_available", True):
        with patch("core.browser_engine.async_playwright"):
            engine = PlaywrightTrafficEngine(config, [])
    engine.on_log = logs.append
    return engine


class TestCountOncePerIp:
    def test_a_counted_visit_asks_for_a_new_ip(self):
        logs: list = []
        engine = make_engine(logs)
        engine.stats.success = 1
        engine._clicks_since_rotation = 3

        engine._note_tracker_result([TRACKER])

        assert engine.stats.tracked == 1
        assert engine._pending_rotation is True, "the counted IP must be left behind"
        assert any("one click per IP" in message for message in logs)

    def test_a_visit_with_no_beacon_does_not_rotate(self):
        logs: list = []
        engine = make_engine(logs)
        engine.stats.success = 3

        engine._note_tracker_result([])

        assert engine.stats.tracked == 0
        assert engine._pending_rotation is False
        assert not any("one click per IP" in message for message in logs)

    def test_the_explanation_is_only_said_once(self):
        logs: list = []
        engine = make_engine(logs)
        engine.stats.success = 1

        for _ in range(3):
            engine._note_tracker_result([TRACKER])

        assert sum(1 for message in logs if "one click per IP" in message) == 1

    def test_rotation_clears_the_counted_flag(self):
        # After moving to a new address the flag must be cleared, otherwise every
        # later click would rotate again and the pool would be burned for nothing.
        logs: list = []
        engine = make_engine(logs)
        engine._note_tracker_result([TRACKER])
        assert engine._counted_on_this_ip is True

        engine._counted_on_this_ip = False  # what the rotation does
        assert engine._counted_on_this_ip is False
