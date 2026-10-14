"""Pre-flight health check for the campaign target.

Why this exists
---------------
A Cloudflare error such as **526 (invalid origin SSL certificate)** is produced by
Cloudflare's edge while it is talking to the *origin* server, before any request
reaches the site. That means:

* no bot, browser, user-agent, cookie or proxy can get past it - it is not a
  client-side challenge and cannot be bypassed from here;
* every visitor sees the same error page, so the site's own analytics never runs
  and no click can be counted, no matter how much traffic is sent.

What the tool can do is refuse to burn proxies on a target that is broken, and
tell the operator exactly what has to be fixed. That is what this module is for.
"""

import logging
from dataclasses import dataclass, field

from .constants import cloudflare_error_reason

#: Cloudflare status codes that mean "something is wrong on the origin side".
CLOUDFLARE_ERROR_STATUSES = {
    520: "Cloudflare 520: the origin returned an unknown error.",
    521: "Cloudflare 521: the origin web server is down.",
    522: "Cloudflare 522: the connection to the origin timed out.",
    523: "Cloudflare 523: the origin is unreachable.",
    524: "Cloudflare 524: the origin took too long to respond.",
    525: "Cloudflare 525: the SSL handshake with the origin failed.",
    526: "Cloudflare 526: the origin SSL certificate is invalid.",
    527: "Cloudflare 527: the connection to the origin timed out (railgun).",
    530: "Cloudflare 530: DNS resolution for the origin failed.",
}

#: What the operator has to change on their own side, per error.
FIX_STEPS = {
    526: (
        "The origin server's SSL certificate is invalid (expired, self-signed, or "
        "it does not cover this domain). Fix it in Cloudflare: SSL/TLS -> Overview "
        "-> set the mode to Full (strict); then SSL/TLS -> Origin Server -> Create "
        "Certificate and install that certificate on your server - or renew the "
        "certificate from your hosting panel."
    ),
    525: (
        "The TLS handshake with the origin failed. In Cloudflare set SSL/TLS -> "
        "Overview to Full (strict) and make sure the origin really serves HTTPS on "
        "port 443 with a valid certificate."
    ),
    521: "The origin web server is not answering. Start/restart it, or check that Cloudflare points at the right IP.",
    522: "The origin is not answering in time. Check the origin server's load, ports and firewall (allow Cloudflare IPs).",
    523: "Cloudflare cannot reach the origin IP. Verify the DNS record in Cloudflare points at the right server.",
    524: "The origin takes longer than 100 seconds to respond. Speed up the backend or serve a static page for this link.",
    520: "The origin returned something Cloudflare could not read. Check the origin web server logs.",
    530: "Cloudflare cannot resolve the origin hostname. Fix the DNS record for the origin.",
}

GENERIC_FIX = (
    "This is an origin-side error, so it cannot be bypassed by the bot: every "
    "visitor hits the same error page and the site's analytics never runs. Fix it "
    "on the hosting / Cloudflare side before running a campaign."
)


@dataclass
class TargetHealth:
    """Result of one health probe of the campaign target."""

    ok: bool
    status: int | None = None
    reason: str = ""
    advice: str = ""
    final_url: str = ""
    is_cloudflare_error: bool = False
    details: dict = field(default_factory=dict)

    def summary(self) -> str:
        """One-line, operator-facing summary."""
        if self.ok:
            return f"Target OK (HTTP {self.status}) - ready to run traffic."
        return f"TARGET PROBLEM: {self.reason}"


def classify(
    status: int | None,
    body: str = "",
    title: str = "",
    final_url: str = "",
    error: str = "",
) -> TargetHealth:
    """Turn one probe result into a verdict plus what to do about it.

    Pure function so it can be tested without any network access.
    """
    if status is None:
        return TargetHealth(
            ok=False,
            status=None,
            reason=f"No response from the target ({error or 'timeout / DNS failure'}).",
            advice=(
                "Check the link is correct and reachable, then try again. If other "
                "sites work, the problem is on the target side."
            ),
            final_url=final_url,
        )

    # Cloudflare's own error codes come straight from the edge.
    if status in CLOUDFLARE_ERROR_STATUSES:
        return TargetHealth(
            ok=False,
            status=status,
            reason=CLOUDFLARE_ERROR_STATUSES[status],
            advice=FIX_STEPS.get(status, GENERIC_FIX),
            final_url=final_url,
            is_cloudflare_error=True,
        )

    # A Cloudflare error page can also arrive with a 2xx/4xx status.
    cf_reason = cloudflare_error_reason(body, title)
    if cf_reason:
        return TargetHealth(
            ok=False,
            status=status,
            reason=cf_reason,
            advice=GENERIC_FIX,
            final_url=final_url,
            is_cloudflare_error=True,
        )

    if 200 <= status < 400:
        return TargetHealth(
            ok=True,
            status=status,
            reason=f"Target reachable (HTTP {status}).",
            final_url=final_url,
        )

    return TargetHealth(
        ok=False,
        status=status,
        reason=f"The target answered HTTP {status} instead of the page.",
        advice=(
            "A 403/429 usually means the site is blocking this IP range; a 404 "
            "means the link itself is wrong. Check the link, then try another "
            "proxy or ask the site owner."
        ),
        final_url=final_url,
    )


async def check_target_health(
    url: str, proxy: str | None = None, timeout: int = 20
) -> TargetHealth:
    """Fetch the target once and report whether traffic can be counted on it.

    Args:
        url: the campaign target
        proxy: optional proxy URL (``http://host:port``); when omitted the check
            runs directly from this machine
        timeout: request timeout in seconds
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:  # pragma: no cover - curl_cffi is a hard dependency
        return TargetHealth(
            ok=False,
            reason="curl_cffi is not installed, cannot check the target.",
            advice="Reinstall the requirements (pip install -r requirements.txt).",
        )

    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        async with curl_requests.AsyncSession() as session:
            response = await session.get(
                url,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=True,
                impersonate="chrome",
            )
            body = response.text or ""
            final_url = str(response.url)
    except Exception as exc:
        logging.debug(f"Target health probe failed: {exc}")
        return classify(None, error=str(exc)[:200])

    title = ""
    lowered = body.lower()
    if "<title" in lowered:
        try:
            start = lowered.index("<title") + len("<title")
            start = lowered.index(">", start) + 1
            end = lowered.index("</title>", start)
            title = body[start:end].strip()
        except ValueError:
            title = ""

    return classify(response.status_code, body, title, final_url)


def format_report(health: TargetHealth) -> str:
    """Multi-line, operator-facing report for the activity log."""
    lines = [health.summary()]
    if health.final_url and health.final_url != "":
        lines.append(f"   URL: {health.final_url}")
    if health.advice:
        lines.append(f"   What to do: {health.advice}")
    return "\n".join(lines)
