import re

from .paths import bundle_path

"""
Application-wide constants and configuration defaults.
"""

# Browser impersonation options for TLS fingerprinting
BROWSER_IMPERSONATIONS = ["chrome120", "chrome124", "safari15_5"]

# Proxy checking defaults
DEFAULT_PROXY_TIMEOUT_MS = 3000
MIN_PROXY_TIMEOUT_MS = 100
MAX_PROXY_TIMEOUT_MS = 30000

DEFAULT_CHECK_THREADS = 50
MIN_CHECK_THREADS = 1
MAX_CHECK_THREADS = 2000

DEFAULT_SCRAPE_THREADS = 20
MIN_SCRAPE_THREADS = 1
MAX_SCRAPE_THREADS = 100

# Traffic engine defaults
DEFAULT_ATTACK_THREADS = 5
MIN_ATTACK_THREADS = 1
MAX_ATTACK_THREADS = 500

DEFAULT_VIEW_TIME_MIN = 5
DEFAULT_VIEW_TIME_MAX = 10
MIN_VIEW_TIME = 1
MAX_VIEW_TIME = 300

# Request defaults
REQUEST_TIMEOUT_SECONDS = 30
SCRAPE_TIMEOUT_SECONDS = 15

# Proxy checker batch size (for staggered launch)
PROXY_CHECK_BATCH_SIZE = 50

# GUI update interval (ms)
GUI_UPDATE_INTERVAL_MS = 100

# Buffer drain limit per GUI update
BUFFER_DRAIN_LIMIT = 40

# Curl error codes indicating proxy failure
PROXY_ERROR_CODES = [
    "curl: (56)",
    "curl: (97)",
    "curl: (28)",
    "curl: (7)",
    "curl: (35)",
]

# Success status codes
SUCCESS_STATUS_CODES = [200, 201, 301, 302]

# How many visits a proxy may fail in a row before it is retired from the pool.
# Without this a proxy that always answers with a block page / captcha keeps a
# slot for the whole campaign, so the delivery rate collapses while the attempt
# counter keeps climbing.
MAX_PROXY_CONSECUTIVE_FAILURES = 5

# When a campaign is asked for a fixed number of visits it stops once that many
# visits have actually been *delivered*. If the delivery rate is so bad that this
# many attempts still deliver nothing, stop and say so instead of spinning
# forever.
NO_PROGRESS_ATTEMPT_FACTOR = 10

# Stop a campaign after this many visits in a row landed on a Cloudflare error
# page: nothing can be counted on the destination side while that is happening,
# so continuing only burns proxies.
MAX_CONSECUTIVE_ERROR_PAGES = 10


# Cloudflare serves these pages itself when something is wrong on the origin
# (5xx, invalid origin certificate, blocked request). They contain Cloudflare
# text and plenty of body text, so the challenge-bypass check used to mistake
# them for a resolved challenge and count the visit as delivered - while the
# destination never served a page and therefore never counted anything.
CLOUDFLARE_ERROR_MARKERS = (
    ("cf-error-details", "Cloudflare error page (cf-error-details)"),
    ("5xx-error-landing", "Cloudflare 5xx error page"),
    ("invalid ssl certificate", "Cloudflare 526: invalid origin SSL certificate"),
    ("error 526", "Cloudflare 526: invalid origin SSL certificate"),
    ("error 521", "Cloudflare 521: origin web server is down"),
    ("error 522", "Cloudflare 522: connection timed out"),
    ("error 523", "Cloudflare 523: origin is unreachable"),
    ("error 525", "Cloudflare 525: SSL handshake failed"),
    ("cloudflare ray id", "Cloudflare error page (Ray ID footer)"),
)


#: Pages that refuse the *visitor* rather than the request: anti-proxy / anti-VPN
#: walls, WAF blocks and bot walls. The request "succeeds" (often HTTP 200) but
#: the site serves a refusal page, so nothing on the destination is ever counted.
BLOCK_PAGE_MARKERS = (
    ("anonymous proxy detected", "the site refuses proxy/VPN IPs"),
    ("proxy detected", "the site refuses proxy/VPN IPs"),
    ("vpn detected", "the site refuses proxy/VPN IPs"),
    ("access denied", "the site blocked this visitor"),
    ("your ip has been blocked", "the site blocked this IP"),
    ("you have been blocked", "the site blocked this visitor"),
    ("unusual traffic from your computer", "the site flagged automated traffic"),
    ("are you a robot", "the site served a robot wall"),
    ("enable javascript and cookies to continue", "the site served a bot wall"),
    ("request blocked", "the site's firewall blocked the request"),
    ("captcha required", "the site demanded a captcha"),
)


def block_page_reason(content: str, title: str = "") -> str | None:
    """Reason the page is an anti-proxy / WAF block page, else ``None``.

    Such a page answers with a normal status, so counting it as a delivered visit
    is what made the stats look healthy while the destination counted nothing.
    """
    low = (content or "").lower()
    title_low = (title or "").lower()
    for marker, reason in BLOCK_PAGE_MARKERS:
        if marker in low or marker in title_low:
            return reason
    return None


#: Hosts that are *click hops* rather than the destination. A visit is only
#: finished once the browser has moved past these to the real landing page.
CLICK_HOP_MARKERS = (
    "profitableratecpmnetwork",
    "adsterra",
    "monetag",
    "adscool",
    "hilltopads",
)


#: Hints that a request is the destination's own counting beacon. Ad networks and
#: analytics packages record a visit when one of these fires - a page that loads
#: without any of them is a visit nobody counted.
TRACKER_URL_PATTERNS = (
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.com/tr",
    "connect.facebook.net",
    "analytics",
    "doubleclick.net",
    "adscool",
    "histats",
    "metrika",
    "clarity.ms",
    "hotjar",
    "plausible.io",
    "umami",
    "matomo",
    "piwik",
    "cdn-cgi/rum",
    "/pageview",
    "/collect",
    "/track",
    "/pixel",
    "/beacon",
    "/batch",
)


def is_tracker_url(url: str) -> bool:
    """True when ``url`` looks like a tracking/analytics beacon.

    Heuristic on purpose: the destination decides what it counts, so the tool
    only needs to notice that *something* counting-related was requested, and can
    then say "the site itself recorded these visits" instead of guessing.
    """
    low = (url or "").lower()
    return any(pattern in low for pattern in TRACKER_URL_PATTERNS)


def cloudflare_error_reason(content: str, title: str = "") -> str | None:
    """Describe the page when it is a Cloudflare error page, else ``None``.

    A Cloudflare error page is *not* the target page: a visitor sees an error
    instead of the site's content, so the site's own analytics never fires. It
    must never be counted as a delivered visit.

    Args:
        content: the page HTML
        title: the page title, e.g. ``"example.com | 526: Invalid SSL certificate"``

    Returns:
        A short human-readable reason, or ``None`` if this is not an error page.
    """
    lowered = (content or "").lower()
    for marker, reason in CLOUDFLARE_ERROR_MARKERS:
        if marker in lowered:
            return reason

    # Cloudflare error titles look like "<domain> | 526: Invalid SSL certificate"
    match = re.search(r"\|\s*(\d{3})\s*:", title or "")
    if match:
        return "Cloudflare error %s page" % match.group(1)
    return None

# Dead proxy sentinel speed value
DEAD_PROXY_SPEED_MS = 9999

# Browser-consistent headers for use with curl_cffi impersonation
# Note: User-Agent is NOT included - curl_cffi sets it automatically based on impersonate value
BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Common referer sources for traffic diversity (fallback if file not found)
DEFAULT_REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
    "https://www.google.co.uk/",
    "https://search.yahoo.com/",
]

# Cached referers loaded from file
_loaded_referers = None


def load_referers(filepath: str = None) -> list:
    """
    Load referers from file. Returns DEFAULT_REFERERS if file not found or empty.
    Caches result for subsequent calls.

    Args:
        filepath: Path to referers file. Defaults to resources/referers.txt

    Returns:
        List of referer URLs
    """
    global _loaded_referers

    # Return cached if already loaded
    if _loaded_referers is not None:
        return _loaded_referers

    import os

    if filepath is None:
        # Try to find resources/referers.txt relative to this file or cwd
        base_paths = [
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # Project root
            os.getcwd(),
        ]
        for base in base_paths:
            candidate = bundle_path("resources/referers.txt")
            if os.path.exists(candidate):
                filepath = candidate
                break

    if filepath and os.path.exists(filepath):
        try:
            with open(filepath, encoding="utf-8") as f:
                referers = []
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if line and not line.startswith("#"):
                        referers.append(line)
                if referers:
                    _loaded_referers = referers
                    return _loaded_referers
        except OSError:
            pass

    # Fallback to defaults
    _loaded_referers = DEFAULT_REFERERS
    return _loaded_referers


def get_referers() -> list:
    """Get the list of referers (loads from file on first call)."""
    return load_referers()


# =============================================================================
# Browser Engine Constants (Playwright-based)
# =============================================================================

# Browser context limits (RAM-constrained)
DEFAULT_BROWSER_CONTEXTS = 5
MIN_BROWSER_CONTEXTS = 1
MAX_BROWSER_CONTEXTS = 10

# Protection bypass timing
CLOUDFLARE_WAIT_SECONDS = 10
CAPTCHA_TIMEOUT_SECONDS = 120

# Common browser viewports for realistic traffic
BROWSER_VIEWPORTS = [
    (1920, 1080),  # Full HD (most common)
    (1366, 768),  # HD laptop
    (1536, 864),  # Surface/laptop scaled
    (1440, 900),  # MacBook
    (2560, 1440),  # QHD monitor
]

# Cloudflare detection markers in page content
# These indicate an active challenge page that needs bypassing
CLOUDFLARE_MARKERS = [
    # Challenge page identifiers
    "cf-browser-verification",
    "challenge-platform",
    "__cf_chl_opt",
    "cf_chl_prog",
    "cf-challenge-running",
    "cf-please-wait",
    # Page text markers
    "Checking your browser",
    "Just a moment...",
    "Enable JavaScript and cookies",
    "Verifying you are human",
    "needs to review the security",
    "Ray ID:",  # Combined with other markers
    # Script/element markers
    "cdn-cgi/challenge-platform",
    "/cdn-cgi/challenge-platform/",
    "cpo.src",  # Cloudflare challenge script loader
    "window._cf_chl_opt",
    # Turnstile specific
    "challenges.cloudflare.com",
    "turnstile.if.js",
]

# Cloudflare title markers - page titles during challenge
CLOUDFLARE_TITLE_MARKERS = [
    "Just a moment...",
    "Attention Required",
    "Please Wait...",
    "Checking your browser",
    "Security Check",
]

# Cloudflare success indicators - markers that show bypass worked
CLOUDFLARE_SUCCESS_MARKERS = [
    "cf_clearance",  # Cookie name
]

# Captcha detection markers
CAPTCHA_MARKERS = [
    "g-recaptcha",
    "cf-turnstile",
    "h-captcha",
    "data-sitekey",
    "grecaptcha",
    "challenges.cloudflare.com/turnstile",
]

# Turnstile-specific markers (Cloudflare's captcha)
TURNSTILE_MARKERS = [
    "cf-turnstile",
    "challenges.cloudflare.com/turnstile",
    "turnstile.if.js",
    "data-sitekey",  # Combined with turnstile class
]

# Akamai Bot Manager detection markers
AKAMAI_MARKERS = [
    "_abck",
    "bm_sz",
    "ak_bmsc",
    "akamai",
]

# Browser error patterns
BROWSER_ERROR_PATTERNS = [
    "net::ERR_",
    "Timeout",
    "Navigation failed",
    "Target closed",
    "Protocol error",
]

# User agents for browser mode (matched to viewport for consistency)
BROWSER_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

# OS Emulation Profiles - Consistent fingerprints for realistic browser spoofing
# Each profile contains all properties needed to maintain consistency across checks
OS_PROFILES = [
    # Windows 10/11 - Chrome (most common)
    {
        "name": "Windows Chrome",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "platform": "Win32",
        "vendor": "Google Inc.",
        "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "webgl_vendor": "Google Inc. (Intel)",
        "webgl_renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "languages": ["en-US", "en"],
        "timezone": "America/New_York",
        "screen_depth": 24,
        "hardware_concurrency": 8,
        "device_memory": 8,
        "max_touch_points": 0,
        "browser_type": "chromium",
        # Client Hints
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0",
    },
    # Windows 10/11 - Edge
    {
        "name": "Windows Edge",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        "platform": "Win32",
        "vendor": "Google Inc.",
        "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "webgl_vendor": "Google Inc. (Intel)",
        "webgl_renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "languages": ["en-US", "en"],
        "timezone": "America/New_York",
        "screen_depth": 24,
        "hardware_concurrency": 8,
        "device_memory": 8,
        "max_touch_points": 0,
        "browser_type": "chromium",
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0",
    },
    # Windows - Firefox
    {
        "name": "Windows Firefox",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "platform": "Win32",
        "vendor": "",
        "renderer": "Intel(R) UHD Graphics 630",
        "webgl_vendor": "Intel Inc.",
        "webgl_renderer": "Intel(R) UHD Graphics 630",
        "languages": ["en-US", "en"],
        "timezone": "America/New_York",
        "screen_depth": 24,
        "hardware_concurrency": 8,
        "device_memory": None,  # Firefox doesn't expose this
        "max_touch_points": 0,
        "browser_type": "firefox",
        "oscpu": "Windows NT 10.0; Win64; x64",
        "sec_ch_ua": None,  # Firefox doesn't send client hints
        "sec_ch_ua_platform": None,
        "sec_ch_ua_mobile": None,
    },
    # macOS - Chrome
    {
        "name": "macOS Chrome",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "platform": "MacIntel",
        "vendor": "Google Inc.",
        "renderer": "ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)",
        "webgl_vendor": "Google Inc. (Apple)",
        "webgl_renderer": "ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)",
        "languages": ["en-US", "en"],
        "timezone": "America/Los_Angeles",
        "screen_depth": 30,
        "hardware_concurrency": 10,
        "device_memory": 8,
        "max_touch_points": 0,
        "browser_type": "chromium",
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec_ch_ua_platform": '"macOS"',
        "sec_ch_ua_mobile": "?0",
    },
    # macOS - Safari
    {
        "name": "macOS Safari",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "platform": "MacIntel",
        "vendor": "Apple Computer, Inc.",
        "renderer": "Apple M1 Pro",
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple M1 Pro",
        "languages": ["en-US", "en"],
        "timezone": "America/Los_Angeles",
        "screen_depth": 30,
        "hardware_concurrency": 10,
        "device_memory": None,  # Safari doesn't expose this
        "max_touch_points": 0,
        "browser_type": "webkit",
        "sec_ch_ua": None,  # Safari doesn't send client hints
        "sec_ch_ua_platform": None,
        "sec_ch_ua_mobile": None,
    },
    # Linux - Chrome
    {
        "name": "Linux Chrome",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "platform": "Linux x86_64",
        "vendor": "Google Inc.",
        "renderer": "ANGLE (Mesa Intel(R) UHD Graphics 620, OpenGL 4.6)",
        "webgl_vendor": "Google Inc. (Intel)",
        "webgl_renderer": "ANGLE (Mesa Intel(R) UHD Graphics 620, OpenGL 4.6)",
        "languages": ["en-US", "en"],
        "timezone": "Europe/London",
        "screen_depth": 24,
        "hardware_concurrency": 4,
        "device_memory": 8,
        "max_touch_points": 0,
        "browser_type": "chromium",
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec_ch_ua_platform": '"Linux"',
        "sec_ch_ua_mobile": "?0",
    },
]
