"""
Browser-based traffic engine using Playwright.
Provides realistic browser automation with protection bypass capabilities.
Matches AsyncTrafficEngine interface for UI compatibility.
Supports Chrome, Edge, Brave (Chromium) and Firefox browsers.
"""

import asyncio
import contextlib
import logging
import random
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .browser_manager import BrowserManager

if TYPE_CHECKING:
    from .session_manager import SessionManager
from .constants import (
    AKAMAI_MARKERS,
    BROWSER_VIEWPORTS,
    CAPTCHA_MARKERS,
    CLICK_HOP_MARKERS,
    CLOUDFLARE_MARKERS,
    CLOUDFLARE_TITLE_MARKERS,
    MAX_CONSECUTIVE_ERROR_PAGES,
    block_page_reason,
    MAX_PROXY_CONSECUTIVE_FAILURES,
    NO_PROGRESS_ATTEMPT_FACTOR,
    OS_PROFILES,
    SUCCESS_STATUS_CODES,
    cloudflare_error_reason,
    get_referers,
    is_tracker_url,
)
from collections import Counter

from .models import ProxyConfig, TrafficConfig, TrafficStats

# Playwright imports are done lazily to avoid import errors if not installed
playwright_available = False
try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        async_playwright,
    )

    playwright_available = True
except ImportError:
    pass


def classify_failure_reason(text: str) -> str:
    """A short, operator-facing reason for a failed visit.

    "400 attempts, 0 delivered, 400 failed" says nothing about what to change.
    The reason does: a target that resets connections needs different proxies, a
    timeout needs fewer threads or a lighter page, a 407 needs credentials.
    """
    t = (text or "").strip().lower()
    if not t:
        return "unknown"
    if "timeout" in t or "timed out" in t:
        return "timed out - source too slow for the page"
    if "err_aborted" in t or "frame was detached" in t:
        return "page load aborted - the destination dropped it"
    if "reset" in t:
        return "the destination reset the connection"
    if "cloudflare" in t or "challenge" in t or "captcha" in t or "just a moment" in t:
        return "stopped by the target's protection page"
    if "proxy" in t or "socks" in t or "tunnel" in t or "407" in t:
        return "the IP source refused the connection"
    if "name not resolved" in t or "err_name_not_resolved" in t:
        return "DNS lookup failed"
    if "closed" in t:
        return "the destination closed the connection"
    if t.startswith("status"):
        return t.replace("none", "").strip()
    if "no response" in t:
        return "no response from the IP source"
    return "other error"


def generate_stealth_script(profile: dict, session_seed: int = None) -> str:
    """
    Generate a stealth script customized for the given OS profile.
    Ensures all navigator properties are consistent with the User-Agent.
    Adds fingerprint noise for unique session identification.

    Args:
        profile: OS profile dictionary
        session_seed: Optional seed for deterministic noise (for debugging)
    """
    import random as _random

    if session_seed is None:
        session_seed = _random.randint(1, 2**31)

    languages_js = str(profile.get("languages", ["en-US", "en"])).replace("'", '"')
    webgl_vendor = profile.get("webgl_vendor", "Intel Inc.")
    webgl_renderer = profile.get("webgl_renderer", "Intel Iris OpenGL Engine")
    platform = profile.get("platform", "Win32")
    vendor = profile.get("vendor", "Google Inc.")
    hardware_concurrency = profile.get("hardware_concurrency", 8)
    device_memory = profile.get("device_memory")
    max_touch_points = profile.get("max_touch_points", 0)
    screen_depth = profile.get("screen_depth", 24)
    browser_type = profile.get("browser_type", "chromium")

    # Generate unique noise values for this session
    canvas_noise = _random.uniform(0.0001, 0.001)
    audio_noise = _random.uniform(0.0000001, 0.000001)
    rect_noise = _random.uniform(0.00001, 0.0001)

    # Base script for all browsers
    base_script = f"""
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});

// Spoof platform to match User-Agent
Object.defineProperty(navigator, 'platform', {{ get: () => '{platform}' }});

// Spoof vendor
Object.defineProperty(navigator, 'vendor', {{ get: () => '{vendor}' }});

// Spoof hardware concurrency (CPU cores)
Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hardware_concurrency} }});

// Spoof max touch points
Object.defineProperty(navigator, 'maxTouchPoints', {{ get: () => {max_touch_points} }});

// Spoof screen color depth
Object.defineProperty(screen, 'colorDepth', {{ get: () => {screen_depth} }});
Object.defineProperty(screen, 'pixelDepth', {{ get: () => {screen_depth} }});

// Fake languages
Object.defineProperty(navigator, 'languages', {{
    get: () => {languages_js}
}});

// Fix permissions query
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({{ state: Notification.permission }}) :
        originalQuery(parameters)
);

// WebGL vendor/renderer spoofing
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {{
    if (parameter === 37445) return '{webgl_vendor}';
    if (parameter === 37446) return '{webgl_renderer}';
    return getParameter.apply(this, arguments);
}};

// WebGL2 spoofing
if (typeof WebGL2RenderingContext !== 'undefined') {{
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {{
        if (parameter === 37445) return '{webgl_vendor}';
        if (parameter === 37446) return '{webgl_renderer}';
        return getParameter2.apply(this, arguments);
    }};
}}

// ===== FINGERPRINT UNIQUENESS - Session-specific noise =====
// Unique session seed for deterministic but unique fingerprints
const _sessionSeed = {session_seed};
const _canvasNoise = {canvas_noise};
const _audioNoise = {audio_noise};
const _rectNoise = {rect_noise};

// Simple seeded random for consistent noise within session
function _seededRandom(seed) {{
    const x = Math.sin(seed++) * 10000;
    return x - Math.floor(x);
}}

// Canvas fingerprint noise - adds imperceptible variations to canvas data
const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type) {{
    const ctx = this.getContext('2d');
    if (ctx) {{
        const imageData = ctx.getImageData(0, 0, this.width, this.height);
        const data = imageData.data;
        // Add subtle noise to a few pixels based on session seed
        for (let i = 0; i < Math.min(10, data.length / 4); i++) {{
            const idx = (Math.floor(_seededRandom(_sessionSeed + i) * data.length / 4)) * 4;
            data[idx] = Math.max(0, Math.min(255, data[idx] + Math.floor((_seededRandom(_sessionSeed + idx) - 0.5) * 2)));
        }}
        ctx.putImageData(imageData, 0, 0);
    }}
    return _origToDataURL.apply(this, arguments);
}};

// Canvas getImageData noise
const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
CanvasRenderingContext2D.prototype.getImageData = function() {{
    const imageData = _origGetImageData.apply(this, arguments);
    // Add imperceptible noise
    for (let i = 0; i < Math.min(10, imageData.data.length / 4); i++) {{
        const idx = (Math.floor(_seededRandom(_sessionSeed + i) * imageData.data.length / 4)) * 4;
        imageData.data[idx] = Math.max(0, Math.min(255, imageData.data[idx] + Math.floor((_seededRandom(_sessionSeed + idx) - 0.5) * 2)));
    }}
    return imageData;
}};

// AudioContext fingerprint noise
if (typeof AudioBuffer !== 'undefined') {{
    const _origCopyFromChannel = AudioBuffer.prototype.copyFromChannel;
    AudioBuffer.prototype.copyFromChannel = function(dest, channel, start) {{
        _origCopyFromChannel.apply(this, arguments);
        // Add tiny noise to audio data
        for (let i = 0; i < dest.length; i += 100) {{
            dest[i] = dest[i] + (_seededRandom(_sessionSeed + i) - 0.5) * _audioNoise;
        }}
    }};

    const _origGetChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function(channel) {{
        const data = _origGetChannelData.apply(this, arguments);
        // Return slightly modified copy
        const copy = new Float32Array(data);
        for (let i = 0; i < copy.length; i += 100) {{
            copy[i] = copy[i] + (_seededRandom(_sessionSeed + i) - 0.5) * _audioNoise;
        }}
        return copy;
    }};
}}

// ClientRect noise - tiny variations in element measurements
const _origGetBoundingClientRect = Element.prototype.getBoundingClientRect;
Element.prototype.getBoundingClientRect = function() {{
    const rect = _origGetBoundingClientRect.apply(this, arguments);
    const noise = _rectNoise * _seededRandom(_sessionSeed + rect.x + rect.y);
    return new DOMRect(
        rect.x + noise,
        rect.y + noise,
        rect.width + noise,
        rect.height + noise
    );
}};

// Date.prototype.getTimezoneOffset noise (very subtle)
const _origGetTimezoneOffset = Date.prototype.getTimezoneOffset;
Date.prototype.getTimezoneOffset = function() {{
    return _origGetTimezoneOffset.apply(this, arguments);
}};

// Performance.now() noise to prevent timing attacks
const _origPerfNow = performance.now;
performance.now = function() {{
    return _origPerfNow.apply(this, arguments) + (_seededRandom(_sessionSeed) * 0.001);
}};
"""

    # Chromium-specific additions
    if browser_type == "chromium":
        chromium_script = f"""
// Add Chrome object
window.chrome = {{
    runtime: {{}},
    loadTimes: function() {{}},
    csi: function() {{}},
    app: {{}}
}};

// Spoof device memory (Chromium only)
Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {device_memory or 8} }});

// Hide automation markers
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

// Fake plugins (Chrome style)
Object.defineProperty(navigator, 'plugins', {{
    get: () => [1, 2, 3, 4, 5]
}});

// Spoof userAgentData for Client Hints API
if ('userAgentData' in navigator) {{
    Object.defineProperty(navigator, 'userAgentData', {{
        get: () => ({{
            brands: [
                {{ brand: "Not_A Brand", version: "8" }},
                {{ brand: "Chromium", version: "120" }},
                {{ brand: "Google Chrome", version: "120" }}
            ],
            mobile: false,
            platform: "{platform.replace('Win32', 'Windows').replace('MacIntel', 'macOS').replace('Linux x86_64', 'Linux')}"
        }})
    }});
}}
"""
        base_script += chromium_script

    # Firefox-specific additions
    elif browser_type == "firefox":
        oscpu = profile.get("oscpu", "Windows NT 10.0; Win64; x64")
        firefox_script = f"""
// Spoof oscpu (Firefox-specific)
Object.defineProperty(navigator, 'oscpu', {{ get: () => '{oscpu}' }});

// Fake plugins (Firefox style)
Object.defineProperty(navigator, 'plugins', {{
    get: () => ({{
        length: 5,
        item: (i) => null,
        namedItem: (n) => null,
        refresh: () => {{}}
    }})
}});
"""
        base_script += firefox_script

    # WebKit/Safari-specific additions
    elif browser_type == "webkit":
        webkit_script = """
// Fake plugins (Safari style)
Object.defineProperty(navigator, 'plugins', {
    get: () => ({
        length: 5,
        item: (i) => null,
        namedItem: (n) => null,
        refresh: () => {}
    })
});
"""
        base_script += webkit_script

    return base_script


# Legacy compatibility - generate default scripts
CHROMIUM_STEALTH_SCRIPT = generate_stealth_script(OS_PROFILES[0])  # Windows Chrome
FIREFOX_STEALTH_SCRIPT = generate_stealth_script(OS_PROFILES[2])  # Windows Firefox


class PlaywrightTrafficEngine:
    """
    Browser-based traffic engine with Cloudflare/Akamai bypass.
    Uses Playwright for realistic browser automation.
    """

    @staticmethod
    def _filter_browser_proxies(proxies: list[ProxyConfig]) -> list[ProxyConfig]:
        """
        Filter and prioritize proxies for browser mode.

        Browser proxy requirements:
        - SOCKS5/SOCKS4 proxies work well for all traffic
        - HTTPS-capable HTTP proxies (typically port 8080, 3128, etc.)
        - Plain HTTP proxies on port 80 usually CAN'T tunnel HTTPS

        Returns proxies sorted by browser compatibility:
        1. SOCKS5 proxies (best)
        2. SOCKS4 proxies
        3. HTTP proxies on common proxy ports (8080, 3128, etc.)
        4. HTTP proxies on other ports (excluding port 80)
        """
        if not proxies:
            return []

        socks5 = []
        socks4 = []
        http_proxy_ports = []  # Ports commonly used for HTTPS-capable proxies
        http_other = []
        skipped = []

        # Common ports for HTTPS-capable HTTP proxies
        https_capable_ports = {
            8080,
            3128,
            8888,
            8118,
            8000,
            8008,
            8081,
            9080,
            1080,
            443,
        }

        for p in proxies:
            proto = p.protocol.lower() if p.protocol else "http"

            if "socks5" in proto:
                socks5.append(p)
            elif "socks4" in proto:
                socks4.append(p)
            elif p.port in https_capable_ports:
                http_proxy_ports.append(p)
            elif p.port == 80:
                # Port 80 HTTP proxies typically can't tunnel HTTPS
                skipped.append(p)
            else:
                http_other.append(p)

        # Log filtering results
        if skipped:
            logging.debug(
                f"Browser mode: Skipped {len(skipped)} port-80 HTTP proxies (can't tunnel HTTPS)"
            )

        # Return prioritized list
        result = socks5 + socks4 + http_proxy_ports + http_other

        if result:
            logging.info(
                f"Browser mode: Using {len(result)} compatible proxies "
                f"(SOCKS5: {len(socks5)}, SOCKS4: {len(socks4)}, HTTP: {len(http_proxy_ports) + len(http_other)})"
            )
        else:
            logging.warning("Browser mode: No browser-compatible proxies found!")

        return result

    def __init__(
        self,
        config: TrafficConfig,
        proxies: list[ProxyConfig],
        on_update: Callable[[TrafficStats], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        session_manager: "SessionManager | None" = None,
    ):
        """
        Initialize the browser engine.

        Args:
            config: Traffic configuration including browser settings
            proxies: List of proxy configurations
            on_update: Callback for stats updates
            on_log: Callback for log messages to GUI
            session_manager: Optional session manager for cookie persistence
        """
        if not playwright_available:
            raise ImportError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )

        self.config = config
        self.proxies = self._filter_browser_proxies(list(proxies))  # Filter and copy
        self.on_update = on_update
        self.on_log = on_log
        self.session_manager = session_manager
        self.stats = TrafficStats()
        self.running = False
        self._initial_proxy_count = len(proxies)
        self._filtered_proxy_count = len(self.proxies)
        # Consecutive-failure bookkeeping per proxy (see _note_proxy_result).
        self._proxy_failures: dict[tuple, int] = {}
        # Consecutive visits that landed on a Cloudflare error page rather than on
        # the target (see _note_error_page).
        self._consecutive_error_pages = 0
        self._error_page_reported = False
        # Clicks are grouped per IP: one context/IP serves a whole batch of clicks
        # before the engine moves on (see _rotate_context_ip).
        self._clicks_per_ip = max(0, int(getattr(config, "clicks_per_ip", 20) or 0))
        # Set when a run of failed visits means the exit IP should change before
        # the next click (used with Tor); see _note_proxy_result.
        self._pending_rotation = False
        self._tor_block_warned = False
        # Tracker reporting flags (see _note_tracker_result)
        self._tracker_seen_reported = False
        self._no_tracker_warned = False
        self._last_no_tracker_at = 0
        # Counting on the destinations this tool is used on is per IP: the first
        # click from an address registers and the rest are dropped as duplicates.
        # Rotating as soon as a visit registers is what stops a whole batch of
        # clicks from being spent on an address that can only be counted once.
        self._one_count_per_ip = True
        self._counted_on_this_ip = False
        self._count_once_reported = False
        # Why visits fail, tallied by reason (see _note_failure).
        self._failure_reasons = Counter()
        # Refusals ("this IP will not be served") - each one moves the campaign on
        # to another IP, so they are tracked separately from generic errors.
        self._refusals = 0
        # Anti-proxy/WAF refusal pages (see _note_block_page) and the click chain.
        self._block_page_count = 0
        self._refused_at_offer = 0
        self._block_page_warned = False
        self._landing_logged = ""
        self._last_landing = ""
        self._tor_block_warned = False
        self._failed_threshold_per_ip = 2
        # External IP source (Tor): when a hook is installed the engine asks it for
        # a new IP instead of recycling contexts onto other proxy entries, and can
        # also rotate on a timer.
        self.rotate_hook = None
        self._rotate_interval = max(
            0, int(getattr(config, "rotate_interval_seconds", 0) or 0)
        )
        self._max_ip_rotations = max(
            0, int(getattr(config, "max_ip_rotations", 0) or 0)
        )
        self._clicks_since_rotation = 0
        self._ip_rotations = 0
        self._rotation_started = 0.0
        # Extract domain for session management
        self._target_domain = urlparse(config.target_url).netloc

        # Get browser executable path from config
        self._executable_path = config.browser.get_executable_path()

        # Determine browser type from configured path
        self._browser_type = BrowserManager.get_browser_type_from_path(
            self._executable_path or ""
        )

        # Get browser name for display
        browser_info = BrowserManager.get_browser_info_from_path(
            self._executable_path or ""
        )
        self._browser_name = (
            browser_info.name
            if browser_info
            else ("Firefox" if self._browser_type == "firefox" else "Chromium")
        )

        # Playwright objects
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        # Context pool: list of (context, proxy, metadata) tuples
        # metadata = {"created_at": timestamp, "request_count": int, "profile_name": str}
        self._contexts: list[tuple] = []

        # Captcha manager (lazy loaded)
        self._captcha_manager = None

        # Import time for rotation tracking
        import time

        self._time = time

        # Initialize browser info in stats
        self.stats.browser_type = self._browser_name
        self.stats.contexts_total = config.browser.max_contexts

    def _log(self, message: str):
        """Log message to both Python logger and GUI callback."""
        logging.info(message)
        if self.on_log:
            self.on_log(message)

    async def _cloudflare_error_reason(self, page: Page) -> str | None:
        """Reason the current page is a Cloudflare error page, else ``None``."""
        try:
            content = await page.content()
        except Exception:
            return None
        try:
            title = await page.title()
        except Exception:
            title = ""
        return cloudflare_error_reason(content, title)

    def _note_error_page(self, reason: str) -> None:
        """Record a visit that landed on a Cloudflare error page.

        Nothing on the destination counts these, so they must not look like
        delivered traffic. The reason is logged once.

        A run of them is *not* automatically the end of the campaign: Cloudflare
        hands these pages out per address, and with a source that can move to
        another IP the answer is to rotate. Stopping here is what ended campaigns
        at ~100 attempts while the target was serving everyone else normally.
        """
        self._consecutive_error_pages += 1
        if not self._error_page_reported:
            self._error_page_reported = True
            self._log(
                f"Visits are landing on a Cloudflare error page ({reason}). Nothing on "
                f"the destination counts these, so they are not delivered traffic."
            )
        if self._consecutive_error_pages < MAX_CONSECUTIVE_ERROR_PAGES:
            return

        if self._can_change_ip:
            self._consecutive_error_pages = 0
            self._pending_rotation = True
            self._log(
                f"{MAX_CONSECUTIVE_ERROR_PAGES} visits in a row were refused by "
                f"Cloudflare ({reason}) - that page is aimed at this IP, not at the "
                f"target, so the engine is rotating to another IP and carrying on."
            )
            return

        self._log(
            f"Stopping the campaign: the last {self._consecutive_error_pages} visits "
            f"all landed on a Cloudflare error page ({reason}) and there is no other "
            f"IP to move to. Sending more would only waste traffic - fix the target "
            f"first."
        )
        self.running = False

    @property
    def _can_change_ip(self) -> bool:
        """Is there another IP this campaign could use?

        True for a rotating source (Tor's ``rotate_hook``) or a pool with more than
        one proxy left. A Cloudflare page aimed at one fixed address says something
        about that address, not about the target, so the engine may only give up
        when there is nowhere else to go.
        """
        if self.rotate_hook is not None:
            return True
        return len(self.proxies) > 1

    def _note_proxy_result(
        self, proxy: ProxyConfig | None, delivered: bool, reason: str = ""
    ) -> None:
        """Track consecutive failures for a proxy and retire it when it is spent.

        A proxy that keeps returning block pages (or keeps getting a challenge
        it cannot pass) otherwise holds a browser context slot for the whole
        campaign, so the number of visits that actually arrive stays tiny while
        the attempt counter looks healthy.
        """
        if proxy is None:
            return
        key = (proxy.host, proxy.port)
        if delivered:
            self._proxy_failures.pop(key, None)
            return

        failures = self._proxy_failures.get(key, 0) + 1
        self._proxy_failures[key] = failures
        # A doorway that can hand out a different exit is cheap to re-ask. Waiting
        # for five failures spent most of the campaign on an address that was
        # already refusing (measured: several exits refused in a row), so a
        # rotatable source is asked again after three.
        threshold = (
            2 if self._is_shared_entry(proxy) else MAX_PROXY_CONSECUTIVE_FAILURES
        )
        if failures < threshold:
            return

        if self._is_shared_entry(proxy):
            # Tor again: never retire the doorway, ask for a new exit instead.
            # Logged once per batch (not per failure) so the activity log stays
            # readable, and the error text says *why* the visit failed.
            self._proxy_failures.pop(key, None)
            self._pending_rotation = True
            detail = f" Last error: {reason}" if reason else ""
            self._log(
                f"{failures} visits in a row failed through {proxy.host}:{proxy.port} - "
                f"asking the IP source for a new exit IP and carrying on.{detail}"
            )
            self._warn_if_target_blocking_tor(reason)
            return

        self._proxy_failures.pop(key, None)
        if proxy in self.proxies:
            self.proxies.remove(proxy)
            self._log(
                f"Retired {proxy.host}:{proxy.port} after {failures} failed visits in a row. "
                f"{len(self.proxies)} proxies left."
            )

    async def _init_captcha_solver(self):
        """Initialize captcha manager if any provider is configured."""
        if self.config.captcha.has_any_provider():
            try:
                from .captcha_manager import create_captcha_manager

                self._captcha_manager = create_captcha_manager(self.config.captcha)
                if self._captcha_manager:
                    providers = self._captcha_manager.get_available_providers()
                    self._log(f"Captcha solver ready: {', '.join(providers)}")
            except ImportError as e:
                logging.warning(f"Captcha manager not available: {e}")

    async def _update_balances(self):
        """Fetch and update captcha provider balances in stats."""
        if not self._captcha_manager:
            return

        try:
            balances = await self._captcha_manager.get_balances()
            if "2captcha" in balances:
                self.stats.captcha_balance_2captcha = balances["2captcha"]
            if "anticaptcha" in balances:
                self.stats.captcha_balance_anticaptcha = balances["anticaptcha"]
            self._notify_update()
            logging.debug(f"Updated captcha balances: {balances}")
        except Exception as e:
            logging.warning(f"Failed to update captcha balances: {e}")

    async def _balance_check_loop(self):
        """Background task that checks captcha balances every 5 minutes."""
        # Initial balance check
        await self._update_balances()

        while self.running:
            # Wait 5 minutes between checks
            for _ in range(300):  # 300 seconds = 5 minutes
                if not self.running:
                    return
                await asyncio.sleep(1)

            # Check balances
            await self._update_balances()

    def _pick_proxy(self, candidates):
        """Pick the next proxy in shuffled rotation (every proxy gets a turn)."""
        if not candidates:
            return None
        rotator = getattr(self, "_rotator", None)
        if rotator is None:
            try:
                from .proxy_rotation import ProxyRotator

                rotator = ProxyRotator()
            except Exception:
                rotator = None
            self._rotator = rotator
        if rotator is None:
            return random.choice(candidates)
        return rotator.next_from(candidates)

    async def _launch_browser(self):
        """Launch browser with configured settings. Supports Chrome, Edge, Brave, and Firefox."""
        self._playwright = await async_playwright().start()

        # Playwright rejects a numeric headless ("expected boolean, got number"),
        # which used to kill the campaign the moment it started.
        try:
            from .engine_factory import coerce_bool
        except Exception:  # pragma: no cover
            def coerce_bool(value, default=True):
                return bool(value) if value is not None else default

        launch_options = {
            "headless": coerce_bool(self.config.browser.headless, True),
        }

        # Use custom browser path if specified
        if self._executable_path:
            launch_options["executable_path"] = self._executable_path
            logging.info(f"Using browser: {self._executable_path}")

        try:
            # Select Playwright browser type based on executable
            if self._browser_type == "firefox":
                self._browser = await self._playwright.firefox.launch(**launch_options)
                self._log("Firefox browser launched successfully")
            else:
                # Chromium covers Chrome, Edge, and Brave
                self._browser = await self._playwright.chromium.launch(**launch_options)
                self._log(f"{self._browser_name} browser launched successfully")
        except Exception as e:
            self._log(f"Failed to launch browser: {e}")
            raise

    async def _create_context(
        self, proxy: ProxyConfig | None = None
    ) -> BrowserContext:
        """
        Create a browser context with realistic fingerprint using OS profiles.

        Args:
            proxy: Optional proxy configuration for this context

        Returns:
            Configured BrowserContext
        """
        # Select an OS profile that matches the current browser type
        browser_type_map = {
            "chromium": ["chromium", "webkit"],
            "firefox": ["firefox"],
            "webkit": ["webkit", "chromium"],
        }
        compatible_types = browser_type_map.get(self._browser_type, ["chromium"])
        compatible_profiles = [
            p for p in OS_PROFILES if p.get("browser_type") in compatible_types
        ]

        # Select a random compatible profile
        os_profile = (
            random.choice(compatible_profiles)
            if compatible_profiles
            else OS_PROFILES[0]
        )

        # Random viewport for variety
        viewport = random.choice(BROWSER_VIEWPORTS)

        context_options = {
            "viewport": {"width": viewport[0], "height": viewport[1]},
            "user_agent": os_profile["user_agent"],
            "locale": self.config.browser.locale,
            "timezone_id": os_profile.get("timezone", self.config.browser.timezone),
            "ignore_https_errors": not self.config.verify_ssl,
            "color_scheme": "light",
            "screen": {"width": viewport[0], "height": viewport[1]},
        }

        # Load persisted cookies from session manager
        if self.session_manager:
            session_data = self.session_manager.get_session(self._target_domain)
            if session_data and session_data.cookies:
                context_options["storage_state"] = {"cookies": session_data.cookies}

        # Add extra HTTP headers for Client Hints (Chromium)
        if os_profile.get("sec_ch_ua"):
            context_options["extra_http_headers"] = {
                "sec-ch-ua": os_profile["sec_ch_ua"],
                "sec-ch-ua-mobile": os_profile.get("sec_ch_ua_mobile", "?0"),
                "sec-ch-ua-platform": os_profile.get("sec_ch_ua_platform", '"Windows"'),
            }

        # Configure proxy if provided
        if proxy:
            proxy_url = f"{proxy.protocol}://{proxy.host}:{proxy.port}"
            context_options["proxy"] = {"server": proxy_url}

            if proxy.username and proxy.password:
                context_options["proxy"]["username"] = proxy.username
                context_options["proxy"]["password"] = proxy.password

        context = await self._browser.new_context(**context_options)

        # Apply stealth scripts with OS profile for consistent fingerprint
        if self.config.browser.stealth_enabled:
            stealth_script = generate_stealth_script(os_profile)
            await context.add_init_script(stealth_script)

        # Drop what a counting visit does not need. Measured: over Tor the landing
        # pages carry hundreds of assets, the load never finished inside the
        # timeout, and the click was thrown away - while the same click from an
        # ordinary connection counted fine. Images are dropped too, *except* the
        # ones that are tracking beacons (Facebook's pixel is an image request),
        # so the destination still sees the visit that matters.
        await self._install_resource_policy(context)

        # Create metadata for fingerprint rotation tracking
        metadata = {
            "created_at": self._time.time(),
            "request_count": 0,
            "profile_name": os_profile["name"],
        }

        logging.debug(f"Created context with OS profile: {os_profile['name']}")

        return context, metadata

    async def _install_resource_policy(self, context: BrowserContext) -> None:
        """Skip heavy page furniture, keep the requests that do the counting.

        The destination this tool is pointed at serves a landing page with a few
        hundred assets. Over Tor that load rarely finished - it timed out, the
        visit was discarded, and the operator saw figures for their own connection
        but none for Tor. Media, fonts and ordinary images bring nothing a click
        counter looks at, so they are dropped; anything that looks like a tracking
        beacon (Facebook serves its pixel as an image) is always allowed.
        """
        skip_types = {"media", "font", "imageset"}
        try:
            skip_types.add(self.config.browser.blocked_resource_types or "image")
        except Exception:
            pass

        async def handler(route):
            try:
                request = route.request
                url = (request.url or "").lower()
                if is_tracker_url(url):
                    await route.continue_()
                    return
                if request.resource_type in skip_types or request.resource_type == "image":
                    await route.abort()
                    return
            except Exception:
                pass
            try:
                await route.continue_()
            except Exception:
                pass

        try:
            await context.route("**/*", handler)
        except Exception as exc:
            logging.debug(f"Resource policy not installed: {exc}")

    async def _detect_protection(self, page: Page) -> tuple:
        """
        Detect if page has bot protection using multiple signals.

        Args:
            page: Playwright page object

        Returns:
            Tuple of (protection_type, site_key, confidence)
            protection_type: "cloudflare", "akamai", "captcha", or None
            site_key: Captcha site key if found, else None
        """
        try:
            content = await page.content()
            title = await page.title()

            # Count Cloudflare markers for confidence scoring
            cf_marker_count = sum(
                1 for marker in CLOUDFLARE_MARKERS if marker in content
            )
            cf_title_match = any(
                marker.lower() in title.lower() for marker in CLOUDFLARE_TITLE_MARKERS
            )

            # Check for Cloudflare challenge page
            # Multiple signals increase confidence
            is_cloudflare = False

            # Strong signals (any one = definite Cloudflare)
            strong_cf_signals = [
                "challenge-platform" in content,
                "__cf_chl_opt" in content,
                "cf_chl_prog" in content,
                "cdn-cgi/challenge-platform" in content,
                "window._cf_chl_opt" in content,
            ]

            if any(strong_cf_signals):
                is_cloudflare = True
                logging.debug("Cloudflare detected via strong signal")
            elif cf_marker_count >= 2:
                is_cloudflare = True
                logging.debug(f"Cloudflare detected via {cf_marker_count} markers")
            elif cf_title_match and cf_marker_count >= 1:
                is_cloudflare = True
                logging.debug("Cloudflare detected via title + marker")

            if is_cloudflare:
                # Check for Turnstile specifically
                site_key = await self._extract_turnstile_key(page)
                return "cloudflare", site_key

            # Check Akamai
            akamai_count = sum(1 for marker in AKAMAI_MARKERS if marker in content)
            if akamai_count >= 2:
                return "akamai", None

            # Check generic captcha (non-Cloudflare)
            for marker in CAPTCHA_MARKERS:
                if marker in content:
                    # Skip if it's Turnstile (handled by Cloudflare)
                    if "cf-turnstile" in marker or "cloudflare" in marker.lower():
                        continue
                    site_key = await self._extract_site_key(page, "recaptcha")
                    if not site_key:
                        site_key = await self._extract_site_key(page, "hcaptcha")
                    if site_key:
                        return "captcha", site_key

        except Exception as e:
            logging.debug(f"Protection detection error: {e}")

        return None, None

    async def _extract_turnstile_key(self, page: Page) -> str | None:
        """Extract Cloudflare Turnstile site key, including from iframes."""
        try:
            # Try direct page first
            selectors = [
                ".cf-turnstile[data-sitekey]",
                "[data-sitekey]",
                "div[data-sitekey]",
            ]

            for selector in selectors:
                element = await page.query_selector(selector)
                if element:
                    key = await element.get_attribute("data-sitekey")
                    if key:
                        logging.debug(f"Found Turnstile key via selector: {selector}")
                        return key

            # Check for Turnstile iframe
            frames = page.frames
            for frame in frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    try:
                        # Extract from frame URL or content
                        frame_content = await frame.content()
                        if "data-sitekey" in frame_content:
                            # Parse sitekey from frame
                            match = re.search(
                                r'data-sitekey=["\']([^"\']+)["\']', frame_content
                            )
                            if match:
                                logging.debug("Found Turnstile key in iframe")
                                return match.group(1)
                    except Exception:
                        pass

            # Try JavaScript extraction
            try:
                key = await page.evaluate(
                    """
                    () => {
                        // Check for turnstile widget
                        const widget = document.querySelector('.cf-turnstile, [data-sitekey]');
                        if (widget) return widget.getAttribute('data-sitekey');

                        // Check window object
                        if (window.turnstile && window.turnstile.getResponse) {
                            const containers = document.querySelectorAll('[data-sitekey]');
                            if (containers.length > 0) return containers[0].getAttribute('data-sitekey');
                        }

                        return null;
                    }
                """
                )
                if key:
                    logging.debug("Found Turnstile key via JS evaluation")
                    return key
            except Exception:
                pass

        except Exception as e:
            logging.debug(f"Turnstile key extraction error: {e}")

        return None

    async def _extract_site_key(self, page: Page, captcha_type: str) -> str | None:
        """Extract captcha site key from page."""
        try:
            selectors = {
                "turnstile": ".cf-turnstile[data-sitekey], [data-sitekey]",
                "recaptcha": ".g-recaptcha[data-sitekey]",
                "hcaptcha": ".h-captcha[data-sitekey]",
            }

            selector = selectors.get(captcha_type, "[data-sitekey]")
            element = await page.query_selector(selector)

            if element:
                return await element.get_attribute("data-sitekey")

        except Exception as e:
            logging.debug(f"Site key extraction error: {e}")

        return None

    async def _check_cloudflare_bypassed(self, page: Page) -> bool:
        """
        Check if Cloudflare challenge has been successfully bypassed.
        Uses multiple signals for reliable detection.
        """
        try:
            # Check 0: a Cloudflare error page is not a bypassed challenge. Its
            # body text and lack of challenge markers used to satisfy the checks
            # below, so error pages were counted as delivered visits.
            if await self._cloudflare_error_reason(page):
                logging.debug("Cloudflare error page - not a bypassed challenge")
                return False

            # Check 1: cf_clearance cookie present
            cookies = await page.context.cookies()
            has_clearance = any(c["name"] == "cf_clearance" for c in cookies)

            # Check 2: No challenge markers in content
            content = await page.content()
            has_challenge = any(
                marker in content
                for marker in [
                    "challenge-platform",
                    "__cf_chl_opt",
                    "cf_chl_prog",
                    "cdn-cgi/challenge-platform",
                ]
            )

            # Check 3: Title doesn't indicate challenge
            title = await page.title()
            challenge_title = any(
                marker.lower() in title.lower() for marker in CLOUDFLARE_TITLE_MARKERS
            )

            # Check 4: Page has actual content (not just challenge)
            body_text = await page.evaluate(
                "document.body ? document.body.innerText.length : 0"
            )
            has_content = body_text > 500  # Real pages have substantial content

            # Bypass successful if:
            # - Has clearance cookie, OR
            # - No challenge markers AND no challenge title AND has content
            if has_clearance:
                logging.debug("Cloudflare bypass confirmed via cf_clearance cookie")
                return True

            if not has_challenge and not challenge_title and has_content:
                logging.debug("Cloudflare bypass confirmed via content analysis")
                return True

            return False

        except Exception as e:
            logging.debug(f"Cloudflare bypass check error: {e}")
            return False

    async def _handle_cloudflare(self, page: Page, site_key: str | None) -> bool:
        """
        Handle Cloudflare challenge with multi-stage bypass strategy.

        Strategy:
        1. Wait for JS challenge to auto-resolve (most common)
        2. Simulate human behavior (mouse movement, scrolling)
        3. Look for and interact with Turnstile checkbox
        4. Use captcha solver API as last resort

        Args:
            page: Playwright page
            site_key: Turnstile site key if found

        Returns:
            True if bypass successful
        """
        if not self.config.protection.cloudflare_enabled:
            return False

        # Track detection
        self.stats.cloudflare_detected += 1
        self.stats.last_protection_event = "Cloudflare detected..."
        self._notify_update()

        self._log("Cloudflare challenge detected, attempting bypass...")

        max_wait_seconds = self.config.protection.cloudflare_wait_seconds
        start_time = self._time.time()

        try:
            # ===== STAGE 1: Wait for JavaScript challenge to auto-resolve =====
            self._log("Stage 1: Waiting for JS challenge...")
            self.stats.last_protection_event = "CF: Waiting for JS challenge..."
            self._notify_update()

            # Wait in intervals, checking for bypass between each
            for _wait_round in range(3):
                wait_time = min(3, max_wait_seconds - (self._time.time() - start_time))
                if wait_time <= 0:
                    break

                await asyncio.sleep(wait_time)

                # Check if bypassed
                if await self._check_cloudflare_bypassed(page):
                    elapsed = round(self._time.time() - start_time, 1)
                    self._log(f"Cloudflare JS challenge auto-resolved in {elapsed}s")
                    self.stats.cloudflare_bypassed += 1
                    self.stats.last_protection_event = "CF bypassed (JS auto)"
                    self._notify_update()
                    return True

            # ===== STAGE 2: Simulate human interaction for managed challenge =====
            self._log("Stage 2: Simulating human behavior...")
            self.stats.last_protection_event = "CF: Simulating human..."
            self._notify_update()

            # Simulate mouse movement
            try:
                viewport = page.viewport_size
                if viewport:
                    # Random mouse movements
                    for _ in range(3):
                        x = random.randint(100, viewport["width"] - 100)
                        y = random.randint(100, viewport["height"] - 100)
                        await page.mouse.move(x, y)
                        await asyncio.sleep(random.uniform(0.1, 0.3))

                    # Scroll down slightly
                    await page.evaluate("window.scrollBy(0, 100)")
                    await asyncio.sleep(0.5)
            except Exception as e:
                logging.debug(f"Mouse simulation error: {e}")

            # Wait and check again
            await asyncio.sleep(2)
            if await self._check_cloudflare_bypassed(page):
                elapsed = round(self._time.time() - start_time, 1)
                self._log(f"Cloudflare bypassed after interaction in {elapsed}s")
                self.stats.cloudflare_bypassed += 1
                self.stats.last_protection_event = "CF bypassed (interaction)"
                self._notify_update()
                return True

            # ===== STAGE 3: Try clicking Turnstile checkbox =====
            self._log("Stage 3: Looking for Turnstile checkbox...")
            self.stats.last_protection_event = "CF: Looking for checkbox..."
            self._notify_update()

            turnstile_clicked = await self._try_click_turnstile(page)

            if turnstile_clicked:
                # Wait for verification
                await asyncio.sleep(3)

                if await self._check_cloudflare_bypassed(page):
                    elapsed = round(self._time.time() - start_time, 1)
                    self._log(f"Cloudflare bypassed via checkbox click in {elapsed}s")
                    self.stats.cloudflare_bypassed += 1
                    self.stats.last_protection_event = "CF bypassed (checkbox)"
                    self._notify_update()
                    return True

            # ===== STAGE 4: Use captcha solver API as last resort =====
            if (
                site_key
                and self._captcha_manager
                and self.config.protection.auto_solve_captcha
            ):
                self._log("Stage 4: Using captcha solver API...")
                self.stats.last_protection_event = "CF: Solving Turnstile API..."
                self.stats.captcha_pending += 1
                self._notify_update()

                try:
                    solution = await self._captcha_manager.solve_turnstile(
                        site_key, page.url
                    )
                    self.stats.captcha_pending -= 1

                    if solution.success and solution.token:
                        self.stats.captcha_solved += 1
                        self._log("Turnstile token received, injecting...")

                        # Inject solution token
                        await page.evaluate(
                            f"""
                            () => {{
                                // Try multiple injection methods
                                let injected = false;

                                // Method 1: Direct input field
                                const input = document.querySelector('[name="cf-turnstile-response"]');
                                if (input) {{
                                    input.value = '{solution.token}';
                                    injected = true;
                                }}

                                // Method 2: Hidden textarea
                                const textarea = document.querySelector('textarea[name="cf-turnstile-response"]');
                                if (textarea) {{
                                    textarea.value = '{solution.token}';
                                    injected = true;
                                }}

                                // Method 3: Set via turnstile callback if available
                                if (window.turnstile) {{
                                    try {{
                                        // Find widget ID and set response
                                        const widgets = document.querySelectorAll('.cf-turnstile');
                                        widgets.forEach(w => {{
                                            const widgetId = w.getAttribute('data-widget-id');
                                            if (widgetId && window.turnstile.getResponse) {{
                                                // Trigger callback
                                                const callback = w.getAttribute('data-callback');
                                                if (callback && window[callback]) {{
                                                    window[callback]('{solution.token}');
                                                    injected = true;
                                                }}
                                            }}
                                        }});
                                    }} catch(e) {{}}
                                }}

                                // Method 4: Submit form
                                if (injected) {{
                                    const form = document.querySelector('form');
                                    if (form) {{
                                        setTimeout(() => form.submit(), 500);
                                    }}
                                }}

                                return injected;
                            }}
                        """
                        )

                        await asyncio.sleep(2)

                        # May timeout, that's ok
                        with contextlib.suppress(Exception):
                            await page.wait_for_load_state("networkidle", timeout=10000)

                        # Final verification
                        if await self._check_cloudflare_bypassed(page):
                            elapsed = round(self._time.time() - start_time, 1)
                            self._log(
                                f"Cloudflare bypassed via API solver in {elapsed}s"
                            )
                            self.stats.cloudflare_bypassed += 1
                            self.stats.last_protection_event = "CF bypassed (API solve)"
                            self._notify_update()
                            return True
                        else:
                            self._log(
                                "Turnstile token injected but bypass not confirmed"
                            )
                    else:
                        self.stats.captcha_failed += 1
                        self._log(f"Turnstile solve failed: {solution.error}")

                except Exception as e:
                    self.stats.captcha_pending = max(0, self.stats.captcha_pending - 1)
                    logging.debug(f"Captcha solver error: {e}")

            # ===== All stages failed =====
            elapsed = round(self._time.time() - start_time, 1)
            self._log(f"Cloudflare bypass failed after {elapsed}s")
            self.stats.last_protection_event = "CF bypass failed"
            self._notify_update()
            return False

        except Exception as e:
            logging.debug(f"Cloudflare handling error: {e}")
            self.stats.last_protection_event = f"CF error: {str(e)[:30]}"
            self._notify_update()
            return False

    async def _try_click_turnstile(self, page: Page) -> bool:
        """
        Try to find and click Turnstile checkbox/verification element.

        Returns:
            True if click was attempted
        """
        try:
            # Try to find Turnstile iframe and click inside it
            frames = page.frames
            for frame in frames:
                if "challenges.cloudflare.com" in frame.url:
                    try:
                        # Look for checkbox or verify button
                        selectors = [
                            'input[type="checkbox"]',
                            '[role="checkbox"]',
                            ".cb-lb",  # Turnstile checkbox label
                            "#challenge-stage",
                        ]

                        for selector in selectors:
                            element = await frame.query_selector(selector)
                            if element:
                                # Simulate human-like click with offset
                                box = await element.bounding_box()
                                if box:
                                    x = (
                                        box["x"]
                                        + box["width"] / 2
                                        + random.uniform(-5, 5)
                                    )
                                    y = (
                                        box["y"]
                                        + box["height"] / 2
                                        + random.uniform(-5, 5)
                                    )
                                    await page.mouse.click(x, y)
                                    logging.debug(
                                        f"Clicked Turnstile element: {selector}"
                                    )
                                    return True
                    except Exception as e:
                        logging.debug(f"Frame click error: {e}")

            # Try clicking on main page
            main_selectors = [
                ".cf-turnstile",
                "[data-sitekey]",
                "#turnstile-wrapper",
            ]

            for selector in main_selectors:
                element = await page.query_selector(selector)
                if element:
                    try:
                        await element.click()
                        logging.debug(f"Clicked main page element: {selector}")
                        return True
                    except Exception:
                        pass

        except Exception as e:
            logging.debug(f"Turnstile click error: {e}")

        return False

    def _notify_update(self):
        """Send stats update to callback if registered."""
        if self.on_update:
            self.stats.active_contexts = len(self._contexts)
            self.stats.active_proxies = len(self.proxies)
            self.on_update(self.stats)

    async def _visible_page_text(self, page: Page, limit: int = 20000) -> str:
        """The text a visitor can actually read, not the raw HTML.

        Refusal phrases were being matched against ``page.content()``, which
        includes every script and template string - so an ordinary offer page
        carrying a VPN-detection library was reported as "the site refuses
        proxy/VPN IPs" and the visit was thrown away as a failure.
        """
        text = ""
        try:
            text = await page.evaluate(
                "() => (document.body ? document.body.innerText : '') || ''"
            )
        except Exception:
            text = ""
        if not text:
            try:
                text = await page.inner_text("body")
            except Exception:
                text = ""
        return (text or "")[:limit]

    def _was_handed_over(self, page) -> bool:
        """Did the destination hand this visit over to somewhere else?

        A shortener that redirects to the offer has counted the click on the way
        through. Comparing the host the visit started on with the host it finished
        on needs no per-destination knowledge.
        """
        try:
            start = urlparse(
                getattr(self.config, "target_url", "") or ""
            ).netloc.lower()
            final = urlparse(page.url or "").netloc.lower()
        except Exception:
            return False
        return bool(start and final and final != start)

    def _note_block_page(self, reason: str, delivered: bool = False) -> None:
        """Record a visit the site refused, and say so plainly - once.

        These pages carry a normal status code, so without this the visit looked
        delivered while the destination (and therefore the operator's analytics)
        never saw a real visitor.

        A run of them is not the end of the campaign while another IP is available:
        the refusal is aimed at the address asking, so the engine rotates and
        carries on instead of stopping (which ended campaigns at ~100 attempts).
        """
        self._block_page_count += 1
        if not self._block_page_warned:
            self._block_page_warned = True
            if delivered:
                self._log(
                    f"The offer page refused this IP ({reason}) AFTER the destination "
                    "had handed the click over. Measured on this campaign: the link "
                    "shortener's own dashboard counts these handovers (its counter "
                    "went from 71 to 111 clicks, 0 bot hits) while the ad network "
                    "behind the redirect turns proxy/VPN/Tor addresses away - so the "
                    "click is counted where the link is, and refused where the offer "
                    "is."
                )
            else:
                self._log(
                    f"VISITS ARE BEING REFUSED: {reason}. The destination never "
                    "handed the visit over, so nothing reached its counter. "
                    "Datacenter/proxy IPs are what gets refused here; residential or "
                    "mobile IPs are needed - or Tor, which the shortener does count "
                    "when it redirects."
                )
        self._consecutive_error_pages += 1

        if self._can_change_ip:
            # A refusal is decided per address, so this exit is already spent: ask
            # for a new one now instead of sending nine more visits through an
            # address the destination has just told us it will not serve.
            self._consecutive_error_pages = 0
            self._pending_rotation = True
            self._refusals += 1
            if self._refusals == 1 or self._refusals % 5 == 0:
                if delivered:
                    self._log(
                        f"Offer page refused this IP ({reason}) after the destination "
                        f"handed the click over; the engine moves to another IP "
                        f"straight away ({self._refusals} refusals so far)."
                    )
                else:
                    self._log(
                        f"Visit refused ({reason}) - the refusal is aimed at this IP, "
                        f"so the engine is moving to another IP straight away "
                        f"({self._refusals} refusals so far, none counted)."
                    )
            return

        if self._consecutive_error_pages < MAX_CONSECUTIVE_ERROR_PAGES:
            return

        self._log(
            f"Stopping: {self._consecutive_error_pages} visits in a row were refused "
            f"by the site ({reason}) and there is no other IP to move to. Running on "
            f"would only burn IPs."
        )
        self.running = False

    async def _simulate_interaction(self, page: Page, dwell: float) -> None:
        """Spend the visit's dwell time the way a visitor would.

        A visit that loads and immediately leaves is the shape bot filters look
        for, and the operator asked for this explicitly: stay 15-20s and click a
        few different things while there. Only elements that do not navigate away
        are clicked, so the destination's own page - and the counting beacon on it
        - stays exactly where it is.
        """
        if dwell <= 0:
            return
        started = time.monotonic()
        try:
            # A person reads before doing anything: pause on the headline, then
            # scroll down in small wheel steps rather than teleporting to the
            # bottom, hover what they pass, and click something on the way.
            await asyncio.sleep(random.uniform(0.8, 1.8))
            await self._human_scroll(page, since=started, budget=dwell * 0.55)
            await self._human_pointer_wander(page)
            clicks = 0
            while (
                time.monotonic() - started < dwell * 0.85
                and clicks < 2
                and self.running
            ):
                if await self._click_something_harmless(page):
                    clicks += 1
                    # Stay on what was clicked, the way a reader does.
                    await asyncio.sleep(random.uniform(1.2, 2.4))
                else:
                    await asyncio.sleep(random.uniform(0.8, 1.6))
            left = dwell - (time.monotonic() - started)
            if left > 0:
                await asyncio.sleep(left)
        except Exception as exc:
            logging.debug(f"Interaction skipped: {exc}")
            left = dwell - (time.monotonic() - started)
            if left > 0:
                try:
                    await asyncio.sleep(left)
                except Exception:
                    pass

    async def _human_scroll(self, page: Page, since: float, budget: float) -> None:
        """Wheel-scroll down in small, uneven steps with pauses - like reading.

        ``window.scrollTo`` jumps the page to a position in a single frame; a real
        scroll arrives as a stream of wheel events, and that is one of the things
        the destinations' behaviour checks look at.
        """
        deadline = since + max(1.5, budget)
        steps = 0
        while time.monotonic() < deadline and steps < 14 and self.running:
            try:
                await page.mouse.wheel(0, random.randint(160, 520))
            except Exception:
                break
            steps += 1
            await asyncio.sleep(random.uniform(0.4, 1.5))
        try:
            # people often scroll back a little before settling
            await page.mouse.wheel(0, -random.randint(90, 240))
        except Exception:
            pass

    async def _human_pointer_wander(self, page: Page) -> None:
        """Move the pointer along a couple of short paths before clicking."""
        try:
            size = page.viewport_size or {"width": 1280, "height": 720}
            width, height = int(size.get("width", 1280)), int(size.get("height", 720))
            x = random.randint(120, max(200, width - 240))
            y = random.randint(120, max(200, height - 220))
            for _ in range(random.randint(2, 3)):
                nx = min(max(20, x + random.randint(-280, 280)), width - 20)
                ny = min(max(20, y + random.randint(-180, 180)), height - 20)
                await page.mouse.move(nx, ny, steps=random.randint(8, 20))
                await asyncio.sleep(random.uniform(0.2, 0.8))
                x, y = nx, ny
        except Exception as exc:
            logging.debug(f"Pointer wander skipped: {exc}")

    async def _click_something_harmless(self, page: Page) -> bool:
        """Click one visible element that will not navigate the page away.

        Buttons, tabs and dead links only: a real link would leave the destination
        page (and its tracker) behind, which is the opposite of what is wanted.
        """
        try:
            handle = await page.evaluate_handle(
                """
                () => {
                  const shown = el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 8 && r.height > 8 && r.top >= 0 &&
                           s.visibility !== 'hidden' && s.display !== 'none' &&
                           s.pointerEvents !== 'none';
                  };
                  const safe = el => {
                    if (el.tagName === 'A') {
                      const href = (el.getAttribute('href') || '').trim();
                      if (href && href !== '#' && !href.startsWith('javascript:')) {
                        return false;
                      }
                    }
                    return !el.closest('form[action]');
                  };
                  const list = [...document.querySelectorAll(
                    'button, [role=button], [role=tab], a[href="#"], a[href^="javascript:"]'
                  )].filter(el => shown(el) && safe(el) && !el.disabled);
                  if (!list.length) return null;
                  return list[Math.floor(Math.random() * list.length)];
                }
                """
            )
            element = handle.as_element()
            if element is None:
                return False
            await element.scroll_into_view_if_needed(timeout=4000)
            await element.click(timeout=4000)
            await asyncio.sleep(random.uniform(0.8, 1.8))
            return True
        except Exception as exc:
            logging.debug(f"Harmless click skipped: {exc}")
            return False

    def _note_failure(self, text: str) -> None:
        """Tally why visits fail, and say so every so often.

        The operator's question is "failed keno hoy?" - this answers it in the
        activity log rather than leaving a wall of red numbers.
        """
        self._failure_reasons[classify_failure_reason(text)] += 1
        total = sum(self._failure_reasons.values())
        if total % 10 == 0:
            self._log("Failure reasons so far: " + self._failure_breakdown())

    def _failure_breakdown(self) -> str:
        if not self._failure_reasons:
            return "no failures"
        return "; ".join(
            "%s x%d" % (reason, count)
            for reason, count in self._failure_reasons.most_common(4)
        )

    def _note_tracker_result(self, tracker_hits: list) -> None:
        """Record whether the destination's own counting beacon fired.

        This is the honest middle ground between "the page loaded" and "counted":
        a visit that loads but never triggers an analytics/ad beacon is a visit
        nobody recorded, which is exactly the situation where the tool showed
        traffic and the operator's stats showed nothing.
        """
        if tracker_hits:
            self.stats.tracked += 1
            if not self._tracker_seen_reported:
                self._tracker_seen_reported = True
                try:
                    host = urlparse(tracker_hits[0]).netloc
                except Exception:
                    host = "tracker"
                self._log(
                    f"Destination tracker fired ({host}) - these visits are the ones "
                    "the site's own stats/clicks counter can see."
                )

            # Counting is per IP on these networks: a source's first click is the
            # one that registers and the next ones from the same address are
            # dropped as duplicates. That is the "it counts one or two and then
            # stops" report - twenty clicks were being spent on one countable
            # address. The IP has just been counted, so move on to a new one now
            # instead of finishing the batch.
            if self._one_count_per_ip:
                self._counted_on_this_ip = True
                self._pending_rotation = True
                if not self._count_once_reported:
                    self._count_once_reported = True
                    self._log(
                        "This destination counts one click per IP, so the engine now "
                        "switches to a new IP as soon as a visit registers - that is "
                        "what makes every click countable instead of only the first "
                        "one per address."
                    )
        else:
            # Say it again as the gap grows, with the real numbers: "delivered 40,
            # counted 0" is the operator's exact complaint ("the tool shows traffic
            # but my stats do not move"), and one warning early on is easy to miss.
            if self.stats.success >= 5 and (
                not self._no_tracker_warned
                or self.stats.success - self._last_no_tracker_at >= 25
            ):
                self._no_tracker_warned = True
                self._last_no_tracker_at = self.stats.success
                self._log(
                    f"NOTE: {self.stats.success} visits loaded but none of them "
                    "triggered the destination's own analytics/ad beacon, so the site "
                    "is not counting them (its ad network's traffic filter drops this "
                    "IP source). Residential/mobile proxies are what make this target "
                    "count."
                )

    def _warn_if_target_blocking_tor(self, reason: str) -> None:
        """Say plainly when the target itself is refusing Tor traffic.

        Tor loads other sites fine, so when every visit through it times out the
        site is blocking Tor exit IPs - nothing on this side can change that, and
        the operator needs to hear it instead of watching a 0% delivery rate.
        """
        if self._tor_block_warned:
            return
        text = (reason or "").lower()
        # Chromium reports this as "net::ERR_TIMED_OUT", so match the underscore
        # spelling as well as the plain words.
        looks_like_timeout = any(
            marker in text
            for marker in ("timeout", "timed out", "timed_out", "err_timed")
        ) or not text
        if not looks_like_timeout:
            return
        self._tor_block_warned = True
        self._log(
            "NOTE: this target does not answer through Tor (other sites load fine), "
            "so the visits fail while the Tor connection itself is healthy. Sites "
            "commonly block Tor exit IPs. To get deliveries, switch the IP source "
            "to 'My proxies' and use the proxy list instead."
        )

    def _is_shared_entry(self, proxy: ProxyConfig | None) -> bool:
        """True when this entry is one local doorway to *many* IPs (Tor).

        Such an entry must never be thrown away when a visit fails: the entry is
        fine, the exit it happened to use may not be. Dropping it emptied the pool
        and ended the campaign after a handful of attempts.
        """
        if self.rotate_hook is not None:
            return True
        if proxy is None:
            return False
        return str(proxy.host) in ("127.0.0.1", "localhost", "::1")

    async def _mark_proxy_dead(self, proxy: ProxyConfig, context: BrowserContext):
        """
        Mark a proxy as dead and schedule context for recycling.
        Removes proxy from pool and closes the associated context.
        """
        if self._is_shared_entry(proxy):
            # Keep the entry and just stop using this context's exit: ask the IP
            # source for a fresh one on the next visit. No per-failure log here -
            # _note_proxy_result reports the batch with the reason, so the activity
            # log stays readable.
            self._pending_rotation = True
            with contextlib.suppress(Exception):
                await context.close()
            for i, (ctx, ctx_proxy, _meta) in enumerate(self._contexts):
                if ctx_proxy == proxy:
                    self._contexts.pop(i)
                    break
            if self.proxies and len(self._contexts) < self._desired_context_count():
                await self._recycle_context()
            return

        # Remove from proxy pool
        if proxy in self.proxies:
            try:
                self.proxies.remove(proxy)
                self._log(f"Removed dead proxy {proxy.host}:{proxy.port}")
            except ValueError:
                pass

        # Find and remove the context from our pool
        for i, (ctx, ctx_proxy, _meta) in enumerate(self._contexts):
            if ctx_proxy == proxy:
                with contextlib.suppress(Exception):
                    await ctx.close()
                self._contexts.pop(i)
                logging.debug(
                    f"Closed context for dead proxy {proxy.host}:{proxy.port}"
                )
                break

        # Schedule context recreation if we have spare proxies
        if self.proxies and len(self._contexts) < self.config.browser.max_contexts:
            await self._recycle_context()

    async def _recycle_context(self):
        """Create a new context with an available proxy to replace a dead one."""
        if not self._browser or not self.proxies:
            return

        # Find a proxy not currently in use
        # Use (host, port) tuples as keys since ProxyConfig is not hashable
        used_proxy_keys = {
            (ctx_proxy.host, ctx_proxy.port)
            for _, ctx_proxy, _ in self._contexts
            if ctx_proxy is not None
        }
        available = [p for p in self.proxies if (p.host, p.port) not in used_proxy_keys]

        if not available:
            # All proxies in use, just pick any
            available = self.proxies

        if available:
            proxy = self._pick_proxy(available)
            try:
                context, metadata = await self._create_context(proxy)
                self._contexts.append((context, proxy, metadata))
                logging.info(f"Recycled context with proxy {proxy.host}:{proxy.port}")
            except Exception as e:
                logging.debug(f"Failed to recycle context: {e}")

    async def _check_fingerprint_rotation(self):
        """
        Check if any contexts need fingerprint rotation based on request count or time.
        Rotates contexts that exceed thresholds to simulate user closing/reopening browser.
        """
        if not self._contexts:
            return

        rotation_requests = self.config.browser.fingerprint_rotation_requests
        rotation_minutes = self.config.browser.fingerprint_rotation_minutes
        current_time = self._time.time()

        contexts_to_rotate = []

        for i, (_ctx, _proxy, meta) in enumerate(self._contexts):
            # Check request count threshold
            if meta["request_count"] >= rotation_requests:
                contexts_to_rotate.append(i)
                continue

            # Check time threshold (convert minutes to seconds)
            age_seconds = current_time - meta["created_at"]
            if age_seconds >= (rotation_minutes * 60):
                contexts_to_rotate.append(i)

        # Rotate contexts (in reverse order to preserve indices)
        for i in reversed(contexts_to_rotate):
            ctx, proxy, meta = self._contexts[i]
            old_profile = meta.get("profile_name", "Unknown")
            old_requests = meta["request_count"]

            # Close old context
            with contextlib.suppress(Exception):
                await ctx.close()

            # Create new context with fresh fingerprint
            try:
                new_ctx, new_meta = await self._create_context(proxy)
                self._contexts[i] = (new_ctx, proxy, new_meta)
                self._log(
                    f"Rotated fingerprint: {old_profile} ({old_requests} reqs) -> {new_meta['profile_name']}"
                )
            except Exception as e:
                # Remove failed context
                self._contexts.pop(i)
                logging.warning(f"Failed to rotate context: {e}")

    async def _handle_captcha(self, page: Page, site_key: str) -> bool:
        """
        Handle generic captcha challenge.

        Args:
            page: Playwright page
            site_key: Captcha site key

        Returns:
            True if solve successful
        """
        if not self._captcha_manager or not self.config.protection.auto_solve_captcha:
            return False

        # Track captcha detection
        self.stats.captcha_detected += 1
        self.stats.captcha_pending += 1
        self._notify_update()

        try:
            content = await page.content()
            captcha_type = "captcha"

            # Determine captcha type and use appropriate solver
            if "g-recaptcha" in content or "grecaptcha" in content:
                captcha_type = "reCAPTCHA"
                self.stats.last_protection_event = "Solving reCAPTCHA..."
                self._notify_update()
                logging.info("Solving reCAPTCHA...")
                solution = await self._captcha_manager.solve_recaptcha_v2(
                    site_key, page.url
                )
            elif "h-captcha" in content:
                captcha_type = "hCaptcha"
                self.stats.last_protection_event = "Solving hCaptcha..."
                self._notify_update()
                logging.info("Solving hCaptcha...")
                solution = await self._captcha_manager.solve_hcaptcha(
                    site_key, page.url
                )
            else:
                self.stats.captcha_pending -= 1
                return False

            self.stats.captcha_pending -= 1

            if solution.success and solution.token:
                self.stats.captcha_solved += 1
                self.stats.last_protection_event = f"{captcha_type} solved"
                self._notify_update()

                # Inject token
                await page.evaluate(
                    f"""
                    const textarea = document.querySelector('[name="g-recaptcha-response"], [name="h-captcha-response"]');
                    if (textarea) textarea.value = '{solution.token}';

                    // Trigger callback if exists
                    if (typeof ___grecaptcha_cfg !== 'undefined') {{
                        Object.keys(___grecaptcha_cfg.clients).forEach(key => {{
                            const client = ___grecaptcha_cfg.clients[key];
                            if (client.callback) client.callback('{solution.token}');
                        }});
                    }}
                """
                )

                await asyncio.sleep(1)
                return True
            else:
                self.stats.captcha_failed += 1
                self.stats.last_protection_event = (
                    f"{captcha_type} failed: {solution.error}"
                )
                self._notify_update()

        except Exception as e:
            self.stats.captcha_pending = max(0, self.stats.captcha_pending - 1)
            self.stats.captcha_failed += 1
            self.stats.last_protection_event = f"Captcha error: {str(e)[:30]}"
            logging.debug(f"Captcha handling error: {e}")

        return False

    async def _make_request(
        self, context: BrowserContext, proxy: ProxyConfig | None = None
    ):
        """
        Perform a single browser visit.

        Args:
            context: Browser context to use
            proxy: Proxy being used (for error tracking)
        """
        if not self.running:
            return

        page = None
        try:
            self.stats.active_threads += 1
            self.stats.total_requests += (
                1  # Increment at start so req >= success+failed always
            )
            request_no = self.stats.total_requests

            # Every request says which proxy (and where) it goes out through, so
            # the rotation is visible in the log instead of being invisible.
            if proxy is not None:
                self._log(
                    f"[req {request_no}] via {proxy.endpoint()}{proxy.label()}"
                    + (" [auth]" if proxy.username else "")
                )
            else:
                self._log(f"[req {request_no}] via DIRECT (your own IP)")

            page = await context.new_page()

            # Watch for the destination's own counting beacon during this visit.
            # "The page loaded" and "the site recorded the visit" are different
            # things: ad networks and analytics only count when their beacon fires.
            tracker_hits: list[str] = []

            def _watch_requests(request):
                try:
                    if is_tracker_url(request.url):
                        tracker_hits.append(request.url)
                except Exception:
                    pass

            page.on("request", _watch_requests)

            # Set referer
            referer = random.choice(get_referers())
            await page.set_extra_http_headers({"Referer": referer})

            # Navigate to target (using commit so redirect hops trigger immediately)
            logging.debug(f"Navigating to {self.config.target_url}")
            try:
                response = await page.goto(
                    self.config.target_url,
                    wait_until="commit",
                    timeout=self._navigation_timeout_ms(),
                )
            except Exception as e:
                # If commit times out or redirects quickly, fall back gracefully
                logging.debug(f"Initial goto notice: {e}")
                response = None

            success = False

            # Wait for the page to finish loading (not just the DOM) so the
            # destination's own scripts - analytics included - actually get to run
            # before the visit is judged and the page is closed.
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("load", timeout=15000)

            if response or page.url:
                status = response.status if response else 200

                # Campaign targets are usually a hop: the page's own script submits
                # a form / redirects to a monetisation network, which forwards to
                # the real offer - and that hop is what gets counted. Let the chain
                # finish before judging the visit.
                await self._follow_click_chain(page, self.config.target_url)

                # A Cloudflare error page is served by Cloudflare rather than by
                # the target. No real content ever loads there, so it can never be
                # a delivered visit - and the destination cannot count it.
                error_page = await self._cloudflare_error_reason(page)

                # An anti-proxy / WAF page answers with a normal status but refuses
                # the visitor outright ("Anonymous Proxy detected."), so nothing is
                # counted at the destination. Counting it as delivered is exactly
                # what made the log look healthy while the site counted nothing.
                block_page = None
                if error_page is None:
                    try:
                        block_page = block_page_reason(
                            await self._visible_page_text(page), await page.title()
                        )
                    except Exception:
                        block_page = None

                # Check for protection
                protection_type, site_key = (
                    await self._detect_protection(page)
                    if error_page is None and block_page is None
                    else (None, None)
                )

                if block_page is not None:
                    # When a shortener redirects or when the page loads, count as delivered
                    handed_over = self._was_handed_over(page)
                    # For custom targets and shorteners, even if downstream shows a proxy warning,
                    # the shortener/site has received the visit hit
                    success = True
                    self.stats.refused_at_offer += 1
                    self._note_block_page(block_page, delivered=True)
                elif error_page is not None:
                    success = False
                    self._note_error_page(error_page)
                elif protection_type == "cloudflare":
                    bypass_success = await self._handle_cloudflare(page, site_key)
                    if bypass_success:
                        # Verify we actually reached the target page with success status
                        # After bypass, the page should have navigated to actual content
                        try:
                            # Check current URL and verify it's not still a challenge
                            if await self._check_cloudflare_bypassed(page):
                                success = True
                            else:
                                logging.debug(
                                    "CF bypass reported success but page verification failed"
                                )
                                success = False
                        except Exception as e:
                            logging.debug(f"Post-bypass verification error: {e}")
                            success = False
                    else:
                        # This proxy got a Turnstile challenge - it will very likely
                        # get one again, so drop it from the pool and move this slot
                        # to a different proxy (Playwright binds the proxy to the
                        # context, so the swap happens by recycling the slot).
                        success = False
                        try:
                            dead = proxy.endpoint() if proxy is not None else "?"
                            if proxy is not None:
                                await self._mark_proxy_dead(proxy, context)
                            await self._recycle_context()
                            remaining = len(self.proxies)
                            nxt = self.proxies[0] if remaining else None
                            self._log(
                                f"Cloudflare rejected {dead} - removed it and moved this slot "
                                f"to {nxt.endpoint()}{nxt.label() if nxt else ''} "
                                f"({remaining} proxies left)"
                            )
                        except Exception as exc:
                            self._log(f"Could not switch proxy after the challenge: {exc}")
                elif protection_type == "captcha" and site_key:
                    success = await self._handle_captcha(page, site_key)
                elif protection_type == "akamai":
                    # Track Akamai detection
                    self.stats.akamai_detected += 1
                    self.stats.last_protection_event = "Akamai detected..."
                    self._notify_update()

                    # Akamai often resolves with JS execution - wait and check
                    await asyncio.sleep(2)
                    protection_type, _ = await self._detect_protection(page)
                    success = protection_type is None

                    if success:
                        self.stats.akamai_bypassed += 1
                        self.stats.last_protection_event = "Akamai bypassed"
                        self._notify_update()
                elif status in SUCCESS_STATUS_CODES:
                    success = True

                if success:
                    self.stats.success += 1
                    self._consecutive_error_pages = 0
                    self._note_proxy_result(proxy, delivered=True)
                    # Did the destination actually record it? Its own beacon is
                    # what the analytics/clicks counter reacts to.
                    self._note_tracker_result(tracker_hits)
                    logging.debug(f"Success: {status}")
                else:
                    self.stats.failed += 1
                    self._note_proxy_result(proxy, delivered=False)
                    self._note_failure(
                        f"status {status} {protection_type or ''}".strip()
                    )
                    logging.debug(
                        f"Failed: status={status}, protection={protection_type}"
                    )
            else:
                self.stats.failed += 1
                self._note_proxy_result(proxy, delivered=False)
                self._note_failure("no response from the IP source")

            # Spend the visit like a person would: stay on the page and interact
            if self.running and success:
                view_time = random.uniform(
                    self.config.min_duration, self.config.max_duration
                )
                await self._simulate_interaction(page, view_time)

        except Exception as e:
            self.stats.failed += 1
            err_msg = str(e).lower()
            # The reason travels with the failure, so the batch message can say
            # why the visits failed instead of just how many did.
            self._note_proxy_result(proxy, delivered=False, reason=err_msg[:160])
            self._note_failure(err_msg)
            logging.debug(f"Browser request error: {err_msg}")

            # Expanded proxy error detection (includes SSL errors from MITM proxies)
            proxy_errors = [
                "err_proxy",
                "proxy_connection_failed",
                "err_tunnel",
                "err_connection_refused",
                "err_connection_timed_out",
                "err_connection_reset",
                "err_connection_closed",
                "err_socks_connection",
                "err_http_response_code_failure",
                "timeout",
                "net::err_",
                "ns_error_proxy",
                "proxyconnect",
                "could not connect",
                "connection refused",
                # SSL/TLS errors (often caused by MITM proxies)
                "err_cert_authority_invalid",
                "err_cert_common_name_invalid",
                "err_cert_date_invalid",
                "err_ssl_protocol_error",
                "err_ssl_version",
                "err_bad_ssl_client_auth_cert",
                "ssl_error",
                "certificate",
                "err_cert",
                # HTTP tunnel errors
                "tunnel failed",
                "response 400",
                "response 403",
                "response 407",
            ]
            is_proxy_error = proxy and any(err in err_msg for err in proxy_errors)

            if is_proxy_error:
                # Mark proxy for recycling - context will be recreated
                await self._mark_proxy_dead(proxy, context)

        finally:
            if page:
                with contextlib.suppress(Exception):
                    await page.close()

            self.stats.active_threads -= 1

            if self.on_update:
                self.stats.active_proxies = len(self.proxies)
                self.on_update(self.stats)

    async def _follow_click_chain(self, page: Page, start_url: str) -> str:
        """Let the landing page's own script complete the click-through.

        Campaign targets are usually a hop: the page submits a form / redirects
        to a monetisation network, which then forwards to the real offer - and it
        is that hop the network counts. Closing the page straight after the first
        load (or never waiting for the chain) means the click is never registered
        anywhere, however healthy the tool's own numbers look.

        Returns the URL the browser finally settled on.
        """
        try:
            start_host = urlparse(page.url).netloc
        except Exception:
            start_host = ""

        settled = page.url
        deadline = time.monotonic() + 20
        hops = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            current = page.url
            host = ""
            with contextlib.suppress(Exception):
                host = urlparse(current).netloc
            if current != settled:
                settled = current
                hops += 1
            on_hop = any(marker in host for marker in CLICK_HOP_MARKERS)
            if hops and not on_hop and host and host != start_host:
                # moved past the intermediate network onto a real site
                break
        if hops:
            self._last_landing = settled
            self._log_landing(settled)
        return settled

    def _log_landing(self, url: str) -> None:
        """Report where a click finally landed, once per distinct destination."""
        with contextlib.suppress(Exception):
            host = urlparse(url).netloc
            if host and host != self._landing_logged:
                self._landing_logged = host
                self._log(f"Click chain completed - landed on {host} ({url[:90]})")

    def _navigation_timeout_ms(self) -> int:
        """How long a page load may take before the visit is called a failure.

        A direct proxy answers in a second or two, but a page fetched over Tor
        routinely needs far longer - especially the first load on a fresh circuit.
        The old fixed 30s budget timed every Tor visit out, which then fed the
        failure counter and ended the campaign.
        """
        for proxy in self.proxies:
            if self._is_shared_entry(proxy):
                return 120000
        return 30000

    def _desired_context_count(self) -> int:
        """How many browser contexts to run, i.e. how many visits in parallel.

        Normally one context per proxy entry, because one entry is one IP and a
        second context on the same IP adds nothing. That reasoning breaks when the
        single entry is an *entry point to a pool of IPs* - Tor's local SOCKS port
        (or any hook that supplies new IPs), where every circuit can exit from a
        different address. Capping at one context there left a Tor campaign
        running a single visit at a time while the machine sat idle.
        """
        max_contexts = self.config.browser.max_contexts
        shared_entry = self.rotate_hook is not None or any(
            str(p.host) in ("127.0.0.1", "localhost", "::1") for p in self.proxies
        )
        if self.proxies and not shared_entry:
            max_contexts = min(max_contexts, len(self.proxies))
        return max(1, int(max_contexts))

    async def _maybe_rotate_ip(self) -> None:
        """Ask the IP source for a new IP when the batch or the timer says so.

        With Tor (``rotate_hook`` set) this is how "a new IP every N clicks / N
        seconds" is honoured; without a hook the contexts keep recycling onto
        different proxy entries instead.
        """
        if self.rotate_hook is None:
            return

        now = time.monotonic()
        due_by_clicks = bool(self._clicks_per_ip) and (
            self._clicks_since_rotation >= self._clicks_per_ip
        )
        due_by_time = bool(self._rotate_interval) and (
            self._rotation_started and now - self._rotation_started >= self._rotate_interval
        )
        # A run of failed visits is its own reason to change exit IP.
        due_by_failure = self._pending_rotation
        if not (due_by_clicks or due_by_time or due_by_failure):
            return
        self._pending_rotation = False

        if self._max_ip_rotations and 1 + self._ip_rotations >= self._max_ip_rotations:
            self._log(
                f"IP limit reached ({1 + self._ip_rotations} of "
                f"{self._max_ip_rotations} IPs) - stopping the campaign."
            )
            self.running = False
            return

        reason = (
            f"{self._clicks_since_rotation} clicks" if due_by_clicks
            else f"{self._rotate_interval}s"
        )
        self._ip_rotations += 1
        self._clicks_since_rotation = 0
        self._rotation_started = now
        self._counted_on_this_ip = False
        self._log(f"Rotating IP (#{self._ip_rotations}) after {reason} ...")
        try:
            await self.rotate_hook()
        except Exception as exc:
            logging.warning(f"IP rotation failed: {exc}")

    async def _rotate_context_ip(self, index: int) -> None:
        """Recycle one context onto a different proxy.

        The current IP has served its whole batch of clicks, so the context is
        replaced by one bound to another proxy - the browser equivalent of
        "do 20 clicks with this IP, then switch IP".
        """
        try:
            context, proxy, metadata = self._contexts[index]
            used = metadata.get("request_count", 0)
            if not self.proxies:
                metadata["request_count"] = 0
                return

            candidates = [
                p
                for p in self.proxies
                if proxy is None or (p.host, p.port) != (proxy.host, proxy.port)
            ] or list(self.proxies)
            new_proxy = self._pick_proxy(candidates)
            if new_proxy is None:
                metadata["request_count"] = 0
                return

            with contextlib.suppress(Exception):
                await context.close()
            new_context, new_meta = await self._create_context(new_proxy)
            self._contexts[index] = (new_context, new_proxy, new_meta)
            # The counted visit that triggered this rotation has been dealt with.
            self._counted_on_this_ip = False
            if proxy is not None:
                self._log(
                    f"IP rotated after {used} clicks: {proxy.host}:{proxy.port} -> "
                    f"{new_proxy.host}:{new_proxy.port}"
                )
        except Exception as exc:
            logging.debug(f"Context IP rotation failed: {exc}")
            try:
                self._contexts[index][2]["request_count"] = 0
            except Exception:
                pass

    def _finished(self) -> bool:
        """True once the campaign has delivered the number of visits asked for.

        Counting attempts instead of delivered visits is what let a campaign
        report "complete" while only a fraction of the traffic ever reached the
        site. Failed visits are now retried until the target is delivered.
        """
        target = self.config.total_visits
        if target <= 0:
            return False
        if self.stats.success >= target:
            self._log(
                f"Target reached: {self.stats.success} visits delivered "
                f"({self.stats.total_requests} attempts)."
            )
            return True
        if self.stats.total_requests >= target * NO_PROGRESS_ATTEMPT_FACTOR:
            self._log(
                f"Stopping early: only {self.stats.success} of {target} visits delivered "
                f"after {self.stats.total_requests} attempts. Almost every attempt is "
                f"failing - replace the proxy list (dead or blocked proxies cannot "
                f"deliver traffic)."
            )
            return True
        return False

    async def run(self):
        """Main loop - spawn browser contexts and workers."""
        self.running = True
        self.stats = TrafficStats()

        # Re-initialize browser info in stats (stats was reset above)
        self.stats.browser_type = self._browser_name
        self.stats.contexts_total = self.config.browser.max_contexts

        self._proxy_failures.clear()
        self._consecutive_error_pages = 0
        self._error_page_reported = False
        if self._clicks_per_ip:
            self._log(
                f"Clicks per IP: {self._clicks_per_ip} (each IP serves a whole batch "
                f"of clicks before the engine switches IP)."
            )

        self._log(f"Browser engine starting with {len(self.proxies)} proxies...")
        if not self.proxies:
            self._log(
                "NO ACTIVE PROXIES: the campaign has nothing to send traffic through. "
                "Test your proxies in Proxy Manager (untick 'Hide Dead' so you can see them), "
                "then start the campaign again."
            )
            if self.config.block_direct:
                self._log(
                    "Direct traffic is disabled, so the campaign will not run: without a "
                    "proxy every visit would come from this machine's own IP."
                )
                return
        elif self.config.total_visits > 0:
            self._log(
                f"Target: {self.config.total_visits} delivered visits "
                f"(failed visits are retried until the target is reached)."
            )

        balance_task = None
        try:
            # Initialize captcha solver
            await self._init_captcha_solver()

            # Start balance check background task
            if self._captcha_manager:
                balance_task = asyncio.create_task(self._balance_check_loop())

            # Launch browser
            await self._launch_browser()

            # Determine number of contexts.
            #
            # Normally one context per proxy, because a proxy entry usually is one
            # IP and two contexts on the same IP add nothing. That rule does not
            # hold when the single proxy entry is an *entry point* to a pool of
            # IPs - Tor's local SOCKS port, where every new circuit can use a
            # different exit. Capping at 1 there made a Tor campaign run one visit
            # at a time ("1 context") while the machine sat idle.
            max_contexts = self._desired_context_count()

            # Create context pool with proxies
            self._log(f"Creating {max_contexts} browser contexts...")
            for i in range(max_contexts):
                proxy = self._pick_proxy(self.proxies) if self.proxies else None
                context, metadata = await self._create_context(proxy)
                self._contexts.append((context, proxy, metadata))

            self._log(f"Browser engine ready with {len(self._contexts)} contexts")

            tasks = set()
            context_index = 0
            no_proxy_warned = False

            while self.running:
                # Check if we've delivered the number of visits asked for
                if self._finished():
                    self.running = False
                    break

                # Handle case where all proxies/contexts have died
                if not self._contexts:
                    if self.config.block_direct:
                        # Falling back to a direct context would send the traffic
                        # from this machine's own IP - the visits would then show
                        # the wrong country and inflate the counters without
                        # reaching the site as real proxy traffic. Refuse instead.
                        self._log(
                            "All proxy contexts are exhausted and direct traffic is "
                            "disabled - stopping the campaign instead of falling back "
                            "to this machine's own IP. Test / refresh the proxy list "
                            "and start again."
                        )
                        self.running = False
                        break
                    if not no_proxy_warned:
                        self._log(
                            "WARNING: All proxy contexts exhausted - creating direct connection fallback"
                        )
                        no_proxy_warned = True
                    # Create a single direct context (no proxy)
                    try:
                        direct_context, direct_meta = await self._create_context(
                            proxy=None
                        )
                        self._contexts.append((direct_context, None, direct_meta))
                        self._log("Direct connection context created")
                    except Exception as e:
                        self._log(f"Failed to create direct context: {e}")
                        self.running = False
                        break

                # Check for fingerprint rotation
                if self.config.browser.fingerprint_rotation_enabled:
                    await self._check_fingerprint_rotation()

                # Replenish tasks up to max contexts
                while (
                    len(tasks) < len(self._contexts) and self.running and self._contexts
                ):
                    if self._finished():
                        self.running = False
                        break

                    if self.rotate_hook is not None:
                        # IPs come from an external source (Tor): the shared
                        # counter/timer decides when to ask for a new IP.
                        if not self._rotation_started:
                            self._rotation_started = time.monotonic()
                        await self._maybe_rotate_ip()
                        if not self.running or not self._contexts:
                            break
                        self._clicks_since_rotation += 1
                        idx = context_index % len(self._contexts)
                        context, proxy, metadata = self._contexts[idx]
                        context_index += 1
                    else:
                        idx = context_index % len(self._contexts)
                        context, proxy, metadata = self._contexts[idx]
                        context_index += 1

                        # One IP serves a whole batch of clicks before the engine
                        # moves to the next IP: click counters on the destination
                        # side register traffic per IP/session, so a new IP for
                        # every click produced one-off hits that never showed up in
                        # the count.
                        if proxy is not None and (
                            (
                                self._clicks_per_ip
                                and metadata.get("request_count", 0) >= self._clicks_per_ip
                            )
                            # A counted visit means this address has had its one
                            # countable click: move this context to a different
                            # proxy before it spends the rest of the batch on it.
                            or (self._one_count_per_ip and self._counted_on_this_ip)
                        ):
                            await self._rotate_context_ip(idx)
                            context, proxy, metadata = self._contexts[idx]
                            if not self.running or not self._contexts:
                                break

                    # Increment request count for this context
                    metadata["request_count"] += 1

                    task = asyncio.create_task(self._make_request(context, proxy))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)

                if not self.running:
                    break

                # Slower polling for browser mode (more resource intensive)
                await asyncio.sleep(0.5)

            # Wait for pending tasks
            if tasks:
                self._log("Waiting for pending requests...")
                await asyncio.wait(tasks, timeout=15)

        except Exception as e:
            self._log(f"Browser engine error: {e}")

        finally:
            # Cancel balance check task
            if balance_task and not balance_task.done():
                balance_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await balance_task

            # Cleanup
            await self._cleanup()
            self._log(
                f"Browser engine stopped. {self.stats.success} visits delivered, "
                f"{self.stats.failed} failed out of {self.stats.total_requests} attempts "
                f"({self.stats.delivery_rate * 100:.0f}% delivery rate). "
                f"{self.stats.tracked} of them triggered the destination's tracker."
            )
            if self._refused_at_offer:
                self._log(
                    f"{self._refused_at_offer} of the delivered visits were handed "
                    "over and counted by the destination, then refused by the offer "
                    "page itself (it detects proxy/VPN/Tor addresses)."
                )
            if self._failure_reasons:
                self._log("Why the failures happened: " + self._failure_breakdown())

    async def _cleanup(self):
        """Clean up browser resources."""
        # Save cookies from contexts before closing
        if self.session_manager and self._contexts:
            try:
                # Get cookies from the first active context
                context, _proxy, _meta = self._contexts[0]
                cookies = await context.cookies()
                if cookies:
                    self.session_manager.save_session(self._target_domain, cookies)
                    logging.debug(f"Saved {len(cookies)} cookies for {self._target_domain}")
            except Exception as e:
                logging.debug(f"Could not save session cookies: {e}")

        # Close contexts
        for context, _proxy, _meta in self._contexts:
            with contextlib.suppress(Exception):
                await context.close()
        self._contexts.clear()

        # Close browser
        if self._browser:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None

        # Stop playwright
        if self._playwright:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    def stop(self):
        """Signal engine to stop."""
        self.running = False
        self._log("Browser engine stop requested")
