"""Validate the campaign's IP sources against the campaign target itself.

Why this exists
---------------
A proxy that answers ``httpbin.org`` is not a proxy that can deliver a click. The
two failures the operator keeps hitting are:

* **dead proxy** - no response at all, so the visit never happens;
* **filtered source** - the proxy/Tor exit reaches *something*, but the campaign
  target's own traffic filter hands it a decoy instead of the offer page. The
  visit "succeeds" locally, the site never counts it, and the operator sees
  traffic in the tool and zero in their stats.

Checking each source against the real target, and comparing where it lands with
where a normal visit lands, tells the two apart before a campaign burns hours on
a source that can never be counted.

Used both by the campaign pre-flight (keep only sources that work) and by the
"Test sources" action in the UI.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .constants import cloudflare_error_reason

#: The source reached the destination's own page, the way a normal visitor does.
COUNTED = "counts"
#: The source answered, but the target kept it (no hand-off to the destination).
DECOY = "decoy"
#: The target/WAF refused the visitor (error page, 403/429, challenge).
BLOCKED = "blocked"
#: The source answered, but demands a username/password (HTTP 407).
NEEDS_AUTH = "needs-auth"
#: Nothing answered.
DEAD = "dead"

#: Statuses that mean "this source is refused" rather than "the target is broken".
REFUSAL_STATUSES = {401, 403, 406, 407, 429, 451, 503, 511}

#: Markers of a challenge / anti-bot wall rather than a real page.
CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "attention required",
    "captcha",
    "cf-challenge",
    "cf_chl_",
    "access denied",
    "you have been blocked",
    "request blocked",
    "bot detected",
)


@dataclass
class SourceVerdict:
    """Result of checking one IP source against the campaign target."""

    label: str
    proxy: str | None = None
    ok: bool = False
    verdict: str = DEAD
    status: int | None = None
    final_url: str = ""
    decoy: bool = False
    detail: str = ""
    latency_ms: int = 0

    @property
    def usable(self) -> bool:
        """True when this source is worth sending campaign traffic through."""
        return self.verdict == COUNTED

    def line(self) -> str:
        """One operator-facing line."""
        where = self.final_url or "-"
        return "%-34s %-9s %s  %s" % (
            self.label[:33],
            self.verdict,
            ("HTTP %s" % self.status) if self.status else "-",
            where[:70],
        )


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def classify_error(label: str, proxy: str | None, error: str) -> SourceVerdict:
    """Explain *why* a source did not answer, instead of just calling it dead.

    The two cases the operator keeps meeting are a proxy that wants a
    username/password (407 - the list was saved without credentials) and a
    destination that drops the connection when it arrives from Tor.
    """
    text = (error or "").lower()
    verdict = SourceVerdict(label=label, proxy=proxy)

    if "407" in text or "proxy authentication" in text or "authenticationrequired" in text:
        verdict.verdict = NEEDS_AUTH
        verdict.detail = (
            "This proxy wants a username/password (HTTP 407), but the list was "
            "saved without one. Add the credentials (user:pass@host:port) before "
            "the campaign can use it."
        )
        return verdict

    if proxy and "9050" in proxy:
        if "reset" in text or "timed out" in text or "timeout" in text:
            verdict.verdict = BLOCKED
            verdict.detail = (
                "Tor answered for other sites but the campaign target dropped the "
                "connection (%s). Ad networks refuse the public Tor exit list, so "
                "Tor visits here cannot be counted." % error[:80]
            )
            return verdict

    if "timed out" in text or "timeout" in text:
        verdict.verdict = DEAD
        verdict.detail = "No response before the timeout (%s)." % error[:80]
        return verdict

    verdict.verdict = DEAD
    verdict.detail = "No response (%s)." % (error or "unknown error")[:100]
    return verdict


def classify_response(
    target_url: str,
    status: int | None,
    body: str = "",
    final_url: str = "",
    error: str = "",
    label: str = "",
    latency_ms: int = 0,
    handoff_expected: bool = False,
    proxy: str | None = None,
) -> SourceVerdict:
    """Turn one probe result into a verdict. Pure function, so it is testable.

    ``handoff_expected`` says whether a normal visit is handed over to another
    domain (true for a monetised/tracking link, false for a plain site). It is
    decided once from the direct baseline, because "stayed on the target host"
    only means "filtered" when a handover normally happens - on a plain site it
    is simply what a working visit looks like.
    """
    # ``proxy`` must be carried through: it is what tells the campaign which of
    # its sources passed the check. Dropping it made every verdict look like the
    # direct baseline, so a working source was never recognised.
    verdict = SourceVerdict(label=label, proxy=proxy, status=status,
                            final_url=final_url, latency_ms=latency_ms)

    if status is None:
        verdict.verdict = DEAD
        verdict.detail = "No response (%s)." % (error or "timeout / DNS failure")
        return verdict

    lowered = (body or "").lower()
    title = ""
    if "<title" in lowered:
        try:
            start = lowered.index("<title") + len("<title")
            start = lowered.index(">", start) + 1
            title = body[start:lowered.index("</title>", start)].strip()
        except ValueError:
            title = ""

    reason = cloudflare_error_reason(body or "", title)
    if reason:
        verdict.verdict = BLOCKED
        verdict.detail = reason
        return verdict

    if status in REFUSAL_STATUSES:
        verdict.verdict = BLOCKED
        verdict.detail = "The target refused this source (HTTP %s)." % status
        return verdict

    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        verdict.verdict = BLOCKED
        verdict.detail = "The target served a challenge / block page instead of the site."
        return verdict

    if not 200 <= status < 400:
        verdict.verdict = BLOCKED
        verdict.detail = "The target answered HTTP %s instead of the page." % status
        return verdict

    target_host = _host(target_url)
    final_host = _host(final_url)
    if final_host and target_host and final_host != target_host:
        verdict.verdict = COUNTED
        verdict.ok = True
        verdict.detail = "Handed over to the destination (%s)." % final_host
        return verdict

    if not handoff_expected:
        # A plain site: answering here, on its own host, is a normal visit.
        verdict.verdict = COUNTED
        verdict.ok = True
        verdict.detail = "Served the site's own page (HTTP %s)." % status
        return verdict

    verdict.verdict = DECOY
    verdict.decoy = True
    verdict.detail = (
        "Answered on %s and stayed there, while a normal visit is handed over to "
        "another domain - this source's visits are the ones the site does not count."
        % (final_host or target_host or "the target")
    )
    return verdict


async def _probe(target_url: str, proxy: str | None, timeout: float, label: str,
                 handoff_expected: bool = False):
    """One request through one source. Returns a SourceVerdict."""
    import time

    try:
        from curl_cffi import requests as curl_requests
    except ImportError:  # pragma: no cover
        return SourceVerdict(label=label, proxy=proxy, verdict=DEAD,
                             detail="curl_cffi is not installed.")

    proxies = {"http": proxy, "https": proxy} if proxy else None
    started = time.monotonic()
    try:
        async with curl_requests.AsyncSession() as session:
            response = await session.get(
                target_url,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=True,
                impersonate="chrome",
            )
            body = response.text or ""
            status = response.status_code
            final_url = str(response.url)
    except Exception as exc:  # noqa: BLE001 - transport failures are explained below
        logging.debug("Source check failed for %s: %s", label, exc)
        return classify_error(label, proxy, str(exc))

    latency = int((time.monotonic() - started) * 1000)
    return classify_response(
        target_url, status, body, final_url, label=label, latency_ms=latency,
        handoff_expected=handoff_expected, proxy=proxy,
    )


def _default_label(proxy: str | None) -> str:
    if not proxy:
        return "direct (this machine)"
    return "Tor (local socks)" if "9050" in proxy else str(proxy)


async def check_source(target_url: str, proxy: str | None = None,
                       timeout: float = 25.0, label: str = "",
                       baseline: SourceVerdict | None = None) -> SourceVerdict:
    """Check one source (``proxy=None`` means the machine's own connection).

    Pass ``baseline`` (the direct probe) so "stayed on the target host" can be
    judged against what a normal visit does.
    """
    handoff = bool(baseline and baseline.verdict == COUNTED
                   and _host(baseline.final_url) != _host(target_url))
    return await _probe(target_url, proxy, timeout, label or _default_label(proxy),
                        handoff_expected=handoff)


async def check_sources(target_url: str, proxies: list[str] | None = None,
                        timeout: float = 25.0, concurrency: int = 8,
                        include_direct: bool = True) -> list[SourceVerdict]:
    """Check every candidate source against the target, in parallel.

    The direct connection is probed first and used as the baseline: when a normal
    visit is handed over to another domain, any source that is *not* handed over
    is being filtered, and that is reported as a decoy.
    """
    baseline: SourceVerdict | None = None
    if include_direct:
        baseline = await _probe(target_url, None, timeout, "direct (this machine)")
    handoff = bool(
        baseline
        and baseline.verdict == COUNTED
        and _host(baseline.final_url) != _host(target_url)
    )

    jobs = [(str(proxy), str(proxy)) for proxy in proxies or []]
    results: list[SourceVerdict] = [baseline] if baseline else []
    if not jobs:
        return results

    gate = asyncio.Semaphore(max(1, concurrency))

    async def run(proxy, label):
        async with gate:
            return await _probe(target_url, proxy, timeout, label,
                                handoff_expected=handoff)

    results.extend(await asyncio.gather(*(run(p, lbl) for p, lbl in jobs)))
    return results


async def retry_source_until_it_works(
    target_url: str,
    proxy: str,
    rotate=None,
    attempts: int = 3,
    timeout: float = 20.0,
    on_event=None,
) -> SourceVerdict:
    """Keep asking a pool-like source for a different address until one works.

    Some sources are a *pool*, not one address. A Tor exit that the destination
    filters says nothing about the next exit - measured on this campaign, one exit
    in three was accepted and the others were dropped. Rotating and re-checking is
    what turns "Tor is blocked" into "that exit was blocked", and it is the only
    way a Tor campaign can find an exit the destination will count.

    Args:
        target_url: the campaign target.
        proxy: the source to check (``socks5://127.0.0.1:9050`` style).
        rotate: callable that asks the source for a new address; called in a
            worker thread because it does blocking network work.
        attempts: total number of exits to try (1 = no rotation).
        timeout: per-check timeout in seconds.
        on_event: called as ``on_event(verdict, rotated)`` after every attempt so
            the caller can log exactly what was tried.
    """
    verdict = await check_source(target_url, proxy, timeout=timeout, label=proxy)
    if on_event:
        on_event(verdict, False)
    if verdict.usable or rotate is None:
        return verdict

    for _ in range(max(1, attempts) - 1):
        try:
            new_address = await asyncio.to_thread(rotate)
        except Exception as exc:  # noqa: BLE001 - a failed rotation is not fatal
            logging.info("Source rotation failed: %s", exc)
            if on_event:
                on_event(verdict, True)
            break
        if not new_address and not verdict.usable:
            # Nothing changed (Tor refused the request too soon); do not burn the
            # remaining attempts re-checking the same address.
            if on_event:
                on_event(verdict, True)
            break
        verdict = await check_source(target_url, proxy, timeout=timeout, label=proxy)
        if on_event:
            on_event(verdict, True)
        if verdict.usable:
            return verdict

    return verdict


def usable_proxies(results: list[SourceVerdict]) -> list[str]:
    """The proxy URLs that can actually deliver a counted visit."""
    return [r.proxy for r in results if r.proxy and r.usable]


def surviving_urls(source_urls: list[str], results: list[SourceVerdict]) -> set:
    """Which of the checked URLs the campaign is allowed to use.

    ``check_sources`` returns the optional direct baseline first and then exactly
    one verdict per URL, in the order the URLs were given, so the pairing is done
    by position. Matching on the URL string instead looked correct but silently
    dropped every source, because the verdict's own copy of the string is not
    guaranteed to be byte-identical to the caller's.
    """
    checked = [r for r in results if r.proxy is not None]
    return {url for url, verdict in zip(source_urls, checked) if verdict.usable}


def summarize(results: list[SourceVerdict], target_url: str = "") -> str:
    """Operator-facing multi-line report for the activity log."""
    if not results:
        return "No IP sources to check."

    counted = [r for r in results if r.verdict == COUNTED]
    decoy = [r for r in results if r.verdict == DECOY]
    dead = [r for r in results if r.verdict == DEAD]
    blocked = [r for r in results if r.verdict == BLOCKED]
    need_auth = [r for r in results if r.verdict == NEEDS_AUTH]

    lines = ["IP SOURCE CHECK against %s" % (target_url or "the campaign target")]
    for result in results:
        lines.append("   " + result.line())

    lines.append(
        "   -> %d counted, %d decoy/not handed over, %d blocked, %d need login, %d dead"
        % (len(counted), len(decoy), len(blocked), len(need_auth), len(dead))
    )
    if need_auth:
        lines.append(
            "   %d proxy/proxies answered 407 (username+password required): the list "
            "was saved without credentials. Paste them as user:pass@host:port in "
            "Proxy Manager so they can be used." % len(need_auth)
        )

    if not counted:
        lines.append(
            "   VERDICT: not one of these sources gets the destination's page, so "
            "nothing sent through them can be counted. Fix the sources before "
            "running a campaign."
        )
        tor_blocked = [r for r in blocked if r.proxy and "9050" in r.proxy]
        if tor_blocked:
            lines.append(
                "   Tor: the target dropped every visit from the Tor exit "
                "(measured, not guessed). Ad networks refuse the public exit list, "
                "so Tor cannot deliver a counted click here - use working proxies "
                "for this campaign."
            )
    else:
        lines.append(
            "   VERDICT: %d source(s) deliver the destination's page - the campaign "
            "will use only these." % len(counted)
        )
        if decoy:
            lines.append(
                "   The %d source(s) marked 'decoy' answer but are not handed the "
                "offer page: that is the filter that made the tool show traffic "
                "while your stats stayed at zero." % len(decoy)
            )
    if dead:
        lines.append("   Dead sources: %s" % ", ".join(r.label for r in dead[:10]))
    return "\n".join(lines)


def build_proxy_url(proxy) -> str:
    """ProxyConfig (or a string) -> ``scheme://host:port`` for the checks."""
    if isinstance(proxy, str):
        return proxy
    host = getattr(proxy, "host", "")
    port = getattr(proxy, "port", "")
    protocol = str(getattr(proxy, "protocol", "http") or "http").lower()
    if protocol not in ("http", "https", "socks4", "socks5"):
        protocol = "http"
    if not host or not port:
        return ""
    return "%s://%s:%s" % (protocol, host, port)
