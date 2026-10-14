"""Checking IP sources against the real campaign target.

Measured on the operator's own campaign (a monetised ad-network link):

* direct connection -> handed over to the destination's landing page, 9 tracking
  beacons fired -> counted;
* Tor exit           -> the target reset/timed out every visit -> not counted;
* the saved proxies  -> HTTP 407 (no credentials) -> never delivered anything.

These tests pin the classification that turns those measurements into advice.
"""

import asyncio

import core.source_check as source_check
from core.source_check import (
    BLOCKED,
    COUNTED,
    DEAD,
    DECOY,
    NEEDS_AUTH,
    SourceVerdict,
    build_proxy_url,
    classify_error,
    classify_response,
    summarize,
    surviving_urls,
    usable_proxies,
)

TARGET = "https://deshilink.my/VmTZnigp"
OFFER = "https://www.profitableratecpmnetwork.com/h2950vpgn?key=abc"
LANDING = "https://joywin88.live/bd/en/new-register-entry/account"


class TestClassifyResponse:
    def test_verdict_carries_the_proxy_it_came_from(self):
        # Regression: the proxy was dropped when the response was classified, so
        # every verdict looked like the direct baseline and a working source was
        # never recognised - the campaign stopped even though the source worked.
        v = classify_response(TARGET, 200, "<html>ok</html>", OFFER,
                              proxy="socks5://127.0.0.1:9050")
        assert v.proxy == "socks5://127.0.0.1:9050"
        assert usable_proxies([v]) == ["socks5://127.0.0.1:9050"]

    def test_handover_to_another_domain_is_counted(self):
        v = classify_response(TARGET, 200, "<html>ok</html>", OFFER)
        assert v.verdict == COUNTED and v.usable

    def test_plain_site_without_handover_is_fine(self):
        # No redirect is normal for an ordinary site; only a real handover makes
        # "stayed put" suspicious.
        v = classify_response("https://shop.example/x", 200, "<html>ok</html>",
                              "https://shop.example/x")
        assert v.verdict == COUNTED and v.usable

    def test_staying_put_when_handover_expected_is_a_decoy(self):
        v = classify_response(TARGET, 200, "<html>ok</html>", TARGET,
                              handoff_expected=True)
        assert v.verdict == DECOY and v.decoy and not v.usable

    def test_challenge_page_is_not_counted(self):
        v = classify_response(TARGET, 200, "<title>Just a moment...</title>", TARGET)
        assert v.verdict == BLOCKED

    def test_refusal_status_is_blocked(self):
        for status in (403, 429, 503):
            assert classify_response(TARGET, status, "no", TARGET).verdict == BLOCKED

    def test_no_response_is_dead(self):
        v = classify_response(TARGET, None, error="timed out")
        assert v.verdict == DEAD and not v.usable


class TestClassifyError:
    def test_407_is_reported_as_needing_credentials(self):
        # This is exactly what the operator's saved proxies answered.
        v = classify_error(
            "http://64.137.96.74:6641", "http://64.137.96.74:6641",
            "Failed to perform, curl: (7) CONNECT tunnel failed, response 407",
        )
        assert v.verdict == NEEDS_AUTH
        assert "username/password" in v.detail
        assert "user:pass@host:port" in v.detail

    def test_tor_reset_is_reported_as_the_target_blocking_tor(self):
        v = classify_error("Tor (local socks)", "socks5://127.0.0.1:9050",
                           "Recv failure: Connection was reset")
        assert v.verdict == BLOCKED
        assert "Tor" in v.detail

    def test_plain_timeout_is_dead(self):
        v = classify_error("x", "http://1.2.3.4:80", "Connection timed out after 15006 ms")
        assert v.verdict == DEAD


class TestTorRetries:
    """A Tor exit that is filtered says nothing about the next exit.

    Measured on the operator's campaign: one exit in three was accepted and the
    rest were dropped, which is why one blocked exit must not end the campaign.
    """

    def _patch(self, monkeypatch, outcomes):
        calls = []

        async def fake_check(target, proxy, timeout=20.0, label="", baseline=None):
            calls.append(proxy)
            verdict = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
            return SourceVerdict(label=proxy, proxy=proxy, verdict=verdict)

        monkeypatch.setattr(source_check, "check_source", fake_check)
        return calls

    def test_rotates_until_an_exit_is_accepted(self, monkeypatch):
        calls = self._patch(monkeypatch, [BLOCKED, BLOCKED, COUNTED])
        rotated = []

        verdict = asyncio.run(
            source_check.retry_source_until_it_works(
                TARGET,
                "socks5://127.0.0.1:9050",
                rotate=lambda: rotated.append(1) or "9.9.9.9",
                attempts=4,
            )
        )
        assert verdict.usable
        assert len(calls) == 3
        assert len(rotated) == 2

    def test_gives_up_after_the_attempt_limit(self, monkeypatch):
        calls = self._patch(monkeypatch, [BLOCKED])
        verdict = asyncio.run(
            source_check.retry_source_until_it_works(
                TARGET, "socks5://127.0.0.1:9050",
                rotate=lambda: "9.9.9.9", attempts=3,
            )
        )
        assert not verdict.usable
        assert len(calls) == 3

    def test_stops_rechecking_when_tor_refuses_to_rotate(self, monkeypatch):
        # rotate() returns "" when Tor rate-limits NEWNYM; re-checking the same
        # exit would just waste the campaign's start-up time.
        calls = self._patch(monkeypatch, [BLOCKED])
        verdict = asyncio.run(
            source_check.retry_source_until_it_works(
                TARGET, "socks5://127.0.0.1:9050",
                rotate=lambda: "", attempts=4,
            )
        )
        assert not verdict.usable
        assert len(calls) == 1

    def test_no_rotation_callable_means_a_single_check(self, monkeypatch):
        calls = self._patch(monkeypatch, [BLOCKED])
        asyncio.run(
            source_check.retry_source_until_it_works(
                TARGET, "socks5://127.0.0.1:9050", rotate=None, attempts=4
            )
        )
        assert len(calls) == 1

    def test_a_working_source_is_not_rotated_at_all(self, monkeypatch):
        calls = self._patch(monkeypatch, [COUNTED])
        verdict = asyncio.run(
            source_check.retry_source_until_it_works(
                TARGET, "socks5://127.0.0.1:9050",
                rotate=lambda: "9.9.9.9", attempts=4,
            )
        )
        assert verdict.usable
        assert len(calls) == 1


class TestHelpers:
    def test_usable_proxies_keeps_only_counted_sources(self):
        results = [
            SourceVerdict(label="direct", proxy=None, verdict=COUNTED),
            SourceVerdict(label="a", proxy="http://a:1", verdict=COUNTED),
            SourceVerdict(label="b", proxy="http://b:2", verdict=DECOY),
            SourceVerdict(label="c", proxy="http://c:3", verdict=NEEDS_AUTH),
            SourceVerdict(label="d", proxy="http://d:4", verdict=DEAD),
        ]
        assert usable_proxies(results) == ["http://a:1"]

    def test_surviving_urls_pairs_by_position_not_by_string(self):
        # check_sources returns the direct baseline first, then one verdict per
        # URL in order. This pairing is what keeps a working source in the
        # campaign - matching on the URL string silently dropped all of them.
        source_urls = ["socks5://127.0.0.1:9050", "http://1.2.3.4:8080"]
        results = [
            SourceVerdict(label="direct (this machine)", proxy=None, verdict=COUNTED),
            SourceVerdict(label="tor", proxy="socks5://127.0.0.1:9050", verdict=COUNTED),
            SourceVerdict(label="p2", proxy="http://1.2.3.4:8080", verdict=DECOY),
        ]
        assert surviving_urls(source_urls, results) == {"socks5://127.0.0.1:9050"}

    def test_surviving_urls_is_empty_when_every_source_is_refused(self):
        source_urls = ["socks5://127.0.0.1:9050"]
        results = [
            SourceVerdict(label="direct", proxy=None, verdict=COUNTED),
            SourceVerdict(label="tor", proxy="socks5://127.0.0.1:9050", verdict=BLOCKED),
        ]
        assert surviving_urls(source_urls, results) == set()

    def test_build_proxy_url_uses_protocol_and_port(self):
        class P:
            host, port, protocol = "1.2.3.4", 8080, "SOCKS5"

        assert build_proxy_url(P()) == "socks5://1.2.3.4:8080"
        assert build_proxy_url("http://x:1") == "http://x:1"

    def test_summary_names_the_407_problem_and_the_tor_block(self):
        results = [
            SourceVerdict(label="direct", proxy=None, verdict=COUNTED, status=200,
                          final_url=OFFER),
            SourceVerdict(label="Tor", proxy="socks5://127.0.0.1:9050",
                          verdict=BLOCKED, detail="dropped"),
            SourceVerdict(label="http://64.137.96.74:6641",
                          proxy="http://64.137.96.74:6641", verdict=NEEDS_AUTH),
        ]
        text = summarize(results, TARGET)
        assert "1 counted" in text
        assert "407" in text
        assert "user:pass@host:port" in text

    def test_summary_stops_the_operator_when_nothing_counts(self):
        results = [
            SourceVerdict(label="Tor", proxy="socks5://127.0.0.1:9050", verdict=BLOCKED,
                          detail="dropped"),
            SourceVerdict(label="http://a:1", proxy="http://a:1", verdict=NEEDS_AUTH),
        ]
        text = summarize(results, TARGET)
        assert "not one of these sources" in text
        assert "Tor" in text
