import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from curl_cffi import requests

from .constants import (
    BROWSER_HEADERS,
    BROWSER_IMPERSONATIONS,
    MAX_CONSECUTIVE_ERROR_PAGES,
    MAX_PROXY_CONSECUTIVE_FAILURES,
    NO_PROGRESS_ATTEMPT_FACTOR,
    PROXY_ERROR_CODES,
    REQUEST_TIMEOUT_SECONDS,
    SUCCESS_STATUS_CODES,
    block_page_reason,
    cloudflare_error_reason,
    get_referers,
)
from .models import ProxyConfig, TrafficConfig, TrafficStats

if TYPE_CHECKING:
    from .session_manager import SessionManager


class AsyncTrafficEngine:
    def __init__(
        self,
        config: TrafficConfig,
        proxies: list[ProxyConfig],
        on_update: Callable[[TrafficStats], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        session_manager: "SessionManager | None" = None,
    ):
        self.config = config
        self.proxies = proxies
        self.on_update = on_update
        self.on_log = on_log
        self.session_manager = session_manager
        self.stats = TrafficStats()
        self.running = False
        self._stop_event = asyncio.Event()
        self._initial_proxy_count = len(proxies)
        # Proxy pool management - track which proxies are currently in use
        self._proxies_in_use: set = set()
        self._proxy_lock = asyncio.Lock()
        self._proxy_index = 0  # For round-robin when all proxies are in use
        # Consecutive-failure bookkeeping per proxy. A proxy that answers with a
        # block page / captcha / timeout forever used to stay in the pool for the
        # whole campaign, so nearly every "visit" was wasted and the counter
        # looked far healthier than what the target actually received.
        self._proxy_failures: dict[tuple, int] = {}
        # Consecutive responses that were a Cloudflare error page (see above).
        self._consecutive_error_pages = 0
        self._error_page_reported = False
        # Anti-proxy / WAF refusal pages (see _note_block_page).
        self._block_page_warned = False
        # Multi-target URLs support (7-8+ links support)
        if getattr(config, "target_urls", None) and config.target_urls:
            self.target_urls = [u.strip() for u in config.target_urls if u.strip()]
        elif getattr(config, "target_url", None) and config.target_url:
            self.target_urls = [config.target_url.strip()]
        else:
            self.target_urls = ["https://example.com"]

        self._current_link_idx = 0
        self._clicks_on_current_link = 0
        self._clicks_per_link = max(1, int(getattr(config, "clicks_per_link_per_ip", 4) or 4))
        self.continuous_mode = bool(getattr(config, "continuous_mode", False) or config.total_visits == 0)

        # Clicks are grouped per IP (see _acquire_proxy).
        if hasattr(config, "clicks_per_ip") and config.clicks_per_ip is not None:
            self._clicks_per_ip = max(0, int(config.clicks_per_ip))
        elif len(self.target_urls) > 1:
            self._clicks_per_ip = self._clicks_per_link * len(self.target_urls)
        else:
            self._clicks_per_ip = 20
        self._sticky_proxy: ProxyConfig | None = None
        self._sticky_uses = 0
        # IP rotation driven by an external source (Tor). When rotate_hook is set
        # the engine asks that source for a new IP instead of switching proxy
        # entries, and can also rotate on a timer.
        self.rotate_hook = None
        # Set when a run of failed visits means the exit IP should change before
        # the next click (used with Tor).
        self._pending_rotation = False
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
        self._target_domain = urlparse(self.target_urls[0]).netloc

    def _log(self, message: str):
        """Log message to both Python logger and GUI callback."""
        logging.info(message)
        if self.on_log:
            self.on_log(message)

    async def _maybe_rotate_ip(self) -> None:
        """Ask the IP source for a new IP when the batch or the timer says so.

        Used when the IPs come from an external source (``rotate_hook``, e.g. Tor)
        instead of a proxy list: the hook gets a new IP and the engine waits for
        it, so "20 clicks per IP" and "a new IP every N seconds" both hold.
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
        self._log(f"Rotating IP (#{self._ip_rotations}) after {reason} ...")
        try:
            await self.rotate_hook()
        except Exception as exc:
            logging.warning(f"IP rotation failed: {exc}")

    def _note_block_page(self, reason: str) -> None:
        """Record a visit the site refused, and explain it once.

        Without this the refusal page counted as a delivered visit, which is why
        the stats looked healthy while the site's own analytics recorded nothing.
        """
        self._consecutive_error_pages += 1
        if not self._block_page_warned:
            self._block_page_warned = True
            self._log(
                f"VISITS ARE BEING REFUSED: {reason}. These pages answer normally, so "
                "they used to count as deliveries - but the site's own analytics never "
                "sees them. Datacenter/proxy IPs are what gets refused; residential or "
                "mobile IPs are needed here."
            )
        if self._consecutive_error_pages < MAX_CONSECUTIVE_ERROR_PAGES:
            return

        # Same rule as the browser engine: a refusal is aimed at the address that
        # asked, so with another IP available the campaign rotates and carries on
        # rather than stopping at ~100 attempts.
        if self.rotate_hook is not None or len(self.proxies) > 1:
            self._consecutive_error_pages = 0
            self._pending_rotation = True
            self._log(
                f"{MAX_CONSECUTIVE_ERROR_PAGES} visits in a row were refused ({reason}) "
                f"- rotating to another IP and carrying on."
            )
            return

        self._log(
            f"Stopping: {self._consecutive_error_pages} visits in a row were refused "
            f"by the site ({reason}) and there is no other IP to move to."
        )
        self.running = False

    def _is_shared_entry(self, proxy: ProxyConfig | None) -> bool:
        """True when this entry is one local doorway to *many* IPs (Tor)."""
        if self.rotate_hook is not None:
            return True
        if proxy is None:
            return False
        return str(proxy.host) in ("127.0.0.1", "localhost", "::1")

    def _note_error_page(self, reason: str) -> None:
        """Record a response that is a Cloudflare error page rather than the target.

        Nothing on the destination counts those, and a long run of them means the
        target itself is broken, so the campaign stops instead of burning proxies.
        """
        self._consecutive_error_pages += 1
        if not self._error_page_reported:
            self._error_page_reported = True
            self._log(
                f"TARGET IS NOT SERVING A PAGE: requests are landing on a Cloudflare "
                f"error page ({reason}). Real visitors see the same error, so the "
                f"site's own analytics never fires and nothing can be counted. Fix "
                f"the origin server / SSL certificate in Cloudflare first."
            )
        if self._consecutive_error_pages >= MAX_CONSECUTIVE_ERROR_PAGES:
            self._log(
                f"Stopping the campaign: the last {self._consecutive_error_pages} "
                f"requests all landed on a Cloudflare error page ({reason}). Sending "
                f"more would only waste proxies - fix the target first."
            )
            self.running = False

    def _note_proxy_result(self, proxy: ProxyConfig | None, delivered: bool) -> None:
        """Track consecutive failures for a proxy and retire it when it is spent.

        Delivering a visit clears the counter; every failed attempt adds one.
        After ``MAX_PROXY_CONSECUTIVE_FAILURES`` failures in a row the proxy is
        dropped from the pool so the remaining slots go to proxies that still
        work - otherwise dead weight keeps the delivery rate near zero while the
        attempt counter keeps climbing.
        """
        if proxy is None:
            return
        key = (proxy.host, proxy.port)
        if delivered:
            self._proxy_failures.pop(key, None)
            return

        failures = self._proxy_failures.get(key, 0) + 1
        self._proxy_failures[key] = failures
        if failures < MAX_PROXY_CONSECUTIVE_FAILURES:
            return

        if self._is_shared_entry(proxy):
            # Tor's local SOCKS port is one doorway to many exit IPs: the doorway
            # is fine, the exit may not be. Drop nothing; change exit instead.
            self._proxy_failures.pop(key, None)
            self._pending_rotation = True
            self._log(
                f"{failures} visits in a row failed through {proxy.host}:{proxy.port} - "
                "asking the IP source for a new exit IP and carrying on."
            )
            return

        self._proxy_failures.pop(key, None)
        try:
            if proxy in self.proxies:
                self.proxies.remove(proxy)
                self._log(
                    f"Retired {proxy.host}:{proxy.port} after {failures} failed visits in a row. "
                    f"{len(self.proxies)} proxies left."
                )
        except ValueError:
            pass
        if not self.proxies and self._initial_proxy_count:
            self._log("No usable proxies left - stopping the campaign.")
            self.running = False

    async def _acquire_proxy(self) -> ProxyConfig | None:
        """Pick the IP the next click goes out through.

        Clicks are grouped per IP: the same proxy serves ``clicks_per_ip`` clicks
        in a row and only then the engine moves to the next IP. Click counters on
        the destination side register traffic per IP/session, so rotating the
        proxy on every single click produced one-off hits that never appeared in
        the count.
        """
        async with self._proxy_lock:
            if not self.proxies:
                return None

            sticky = self._sticky_proxy
            if sticky is not None and sticky not in self.proxies:
                # Retired while it was the active IP (dead / blocked).
                sticky = self._sticky_proxy = None

            if sticky is not None and self._sticky_uses < self._clicks_per_ip:
                self._sticky_uses += 1
                self._log_proxy_use(sticky)
                return sticky

            # Batch finished (or this is the first click): move to another IP.
            available = [
                p for p in self.proxies if (p.host, p.port) not in self._proxies_in_use
            ] or list(self.proxies)

            # Prefer a different IP - staying on the one that just finished its
            # batch would defeat the point of rotating.
            candidates = [
                p
                for p in available
                if sticky is None or (p.host, p.port) != (sticky.host, sticky.port)
            ] or available

            try:
                weights = [max(p.score, 0.1) for p in candidates]
                proxy = random.choices(candidates, weights=weights, k=1)[0]
            except (ValueError, IndexError):
                proxy = random.choice(candidates)

            if sticky is not None and (proxy.host, proxy.port) != (sticky.host, sticky.port):
                self._log(
                    f"IP rotated: {self._sticky_uses} clicks done on "
                    f"{sticky.host}:{sticky.port} -> now going through "
                    f"{proxy.host}:{proxy.port}"
                )

            self._sticky_proxy = proxy
            self._sticky_uses = 1
            self._log_proxy_use(proxy)
            return proxy

    def _log_proxy_use(self, proxy: ProxyConfig) -> None:
        """Log which IP this click uses, with its position in the batch."""
        try:
            label = proxy.label() if hasattr(proxy, "label") else ""
            endpoint = (
                proxy.endpoint()
                if hasattr(proxy, "endpoint")
                else f"{proxy.host}:{proxy.port}"
            )
            position = (
                f"{self._sticky_uses}/{self._clicks_per_ip}"
                if self._clicks_per_ip
                else str(self._sticky_uses)
            )
            self._log(f"[via {position}] {endpoint}{label}")
        except Exception:
            pass

    async def _release_proxy(self, proxy: ProxyConfig | None):
        """Release a proxy back to the pool."""
        if proxy is None:
            return
        async with self._proxy_lock:
            self._proxies_in_use.discard((proxy.host, proxy.port))

    async def update_proxies(self, new_proxies: list[ProxyConfig]):
        """Inject fresh live proxies from background grabber/checker into active campaign pool."""
        async with self._proxy_lock:
            existing_keys = {(p.host, p.port) for p in self.proxies}
            added = 0
            for p in new_proxies:
                if (p.host, p.port) not in existing_keys:
                    self.proxies.append(p)
                    existing_keys.add((p.host, p.port))
                    added += 1
            if added > 0:
                self._log(
                    f"Dynamic Proxy Pool: Added {added} fresh live proxies. "
                    f"Active live pool: {len(self.proxies)}"
                )

    def _get_next_target_url(self) -> str:
        """Get target URL according to multi-link click distribution rule (4-5 clicks per link per IP)."""
        if not self.target_urls:
            return getattr(self.config, "target_url", "https://example.com")

        url = self.target_urls[self._current_link_idx % len(self.target_urls)]
        self._clicks_on_current_link += 1

        # When current link reaches click quota for this IP, advance to next link
        if self._clicks_on_current_link >= self._clicks_per_link:
            self._clicks_on_current_link = 0
            self._current_link_idx = (self._current_link_idx + 1) % len(self.target_urls)

        return url

    async def _make_request(self):
        """Perform a single HTTP request with proxy and header rotation."""
        if not self.running:
            return

        if self.rotate_hook is not None:
            # The IP comes from an external source (Tor): rotate before the click
            # when the batch or the timer says so.
            if not self._rotation_started:
                self._rotation_started = time.monotonic()
            await self._maybe_rotate_ip()
            if not self.running:
                return
            self._clicks_since_rotation += 1

        if self._initial_proxy_count > 0 and not self.proxies:
            self._log("No active proxies remaining. Stopping engine.")
            self.running = False
            return

        proxy = None
        proxy_config = None
        if self.proxies:
            # Acquire a proxy for exclusive use during this request
            proxy_config = await self._acquire_proxy()
            if proxy_config:
                proxy = proxy_config.to_curl_cffi_format()

        # Randomize impersonation
        impersonate = random.choice(BROWSER_IMPERSONATIONS)

        session = None
        try:
            self.stats.active_threads += 1
            self.stats.total_requests += (
                1  # Increment at start so req >= success+failed always
            )

            # Get target URL for this request
            target_url = self._get_next_target_url()
            target_domain = urlparse(target_url).netloc or self._target_domain

            # Create fresh session for each request (clean cookies/TLS state per "user")
            session = requests.AsyncSession(impersonate=impersonate)

            # Load persisted cookies if session manager is available
            if self.session_manager:
                session_data = self.session_manager.get_session(target_domain)
                if session_data and session_data.cookies:
                    for cookie in session_data.cookies:
                        session.cookies.set(
                            cookie.get("name", ""),
                            cookie.get("value", ""),
                            domain=cookie.get("domain", target_domain),
                        )

            # Set proxy for this request
            proxies_dict = {"http": proxy, "https": proxy} if proxy else None

            # Use browser-consistent headers (User-Agent is set by curl_cffi impersonate)
            headers = BROWSER_HEADERS.copy()
            headers["Referer"] = random.choice(get_referers())

            logging.debug(f"Request to {target_url} via {impersonate}")

            response = await session.get(
                target_url,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
                verify=self.config.verify_ssl,
                proxies=proxies_dict,
            )

            # A Cloudflare error page (5xx / invalid origin certificate) is served
            # by Cloudflare, not by the target, and some of them answer with a
            # success status. Nothing on the destination counts those visits, so
            # they must not be counted here either.
            body_text = getattr(response, "text", "") or ""
            error_page = cloudflare_error_reason(body_text, "")

            # An anti-proxy / WAF page answers normally but refuses the visitor,
            # so nothing is counted at the destination.
            block_page = block_page_reason(body_text, "") if not error_page else None

            if block_page:
                self.stats.failed += 1
                self._note_proxy_result(proxy_config, delivered=False)
                self._note_block_page(block_page)
                logging.warning(f"Visitor refused by the site: {block_page}")
            elif error_page:
                self.stats.failed += 1
                self._note_proxy_result(proxy_config, delivered=False)
                self._note_error_page(error_page)
            elif response.status_code in SUCCESS_STATUS_CODES:
                self.stats.success += 1
                self._consecutive_error_pages = 0
                self._note_proxy_result(proxy_config, delivered=True)
                logging.debug(f"Success: {response.status_code}")
                # Save cookies from successful response
                if self.session_manager and response.cookies:
                    cookies_list = [
                        {"name": c.name, "value": c.value, "domain": c.domain or self._target_domain}
                        for c in response.cookies
                    ]
                    if cookies_list:
                        self.session_manager.save_session(self._target_domain, cookies_list)
            else:
                self.stats.failed += 1
                self._note_proxy_result(proxy_config, delivered=False)
                logging.warning(f"Failed with status: {response.status_code}")

            # Simulate reading/view time
            if self.running:
                view_time = random.uniform(
                    self.config.min_duration, self.config.max_duration
                )
                await asyncio.sleep(view_time)

        except Exception as e:
            self.stats.failed += 1
            err_msg = str(e)
            self._note_proxy_result(proxy_config, delivered=False)

            # Identify fatal proxy errors
            is_proxy_error = any(code in err_msg for code in PROXY_ERROR_CODES)

            if is_proxy_error and proxy_config:
                logging.debug(f"Proxy Failure: {err_msg}")
                if self.proxies and proxy_config in self.proxies:
                    try:
                        self.proxies.remove(proxy_config)
                        self._proxy_failures.pop((proxy_config.host, proxy_config.port), None)
                        remaining = len(self.proxies)
                        self._log(
                            f"Removed dead proxy {proxy_config.host}:{proxy_config.port}. {remaining} remaining."
                        )
                        if remaining == 0:
                            self._log("CRITICAL: All proxies removed! Stopping.")
                            self.running = False
                    except ValueError:
                        pass  # Already removed
            elif "curl: (60)" in err_msg:
                logging.debug(f"SSL/TLS Error: {err_msg}")
            else:
                logging.debug(f"Request Error: {err_msg}")
        finally:
            # Release proxy back to pool
            await self._release_proxy(proxy_config)

            # Clean up session
            if session:
                with contextlib.suppress(Exception):
                    await session.close()
            self.stats.active_threads -= 1
            if self.on_update:
                self.stats.active_proxies = len(self.proxies)
                self.on_update(self.stats)

    def _finished(self) -> bool:
        """True once the campaign has delivered the number of visits asked for.

        The target of a campaign is *delivered visits*, not attempts: counting
        attempts let a campaign report "complete" while most of the visits never
        reached the site (the exact "shows 100, only 10-15 arrive" symptom).
        Failed attempts are retried until the delivered count is reached, the
        proxy pool runs dry, or the no-progress guard below trips.
        """
        target = self.config.total_visits
        if self.continuous_mode or target <= 0:
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
                f"failing - replace the proxy list (test it in Proxy Manager, dead "
                f"proxies cannot deliver traffic)."
            )
            return True
        return False

    async def run(self):
        """Main loop to spawn workers."""
        self.running = True
        self.stats = TrafficStats()  # Reset stats
        self._proxy_failures.clear()
        self._consecutive_error_pages = 0
        self._error_page_reported = False
        self._sticky_proxy = None
        self._sticky_uses = 0
        self._pending_rotation = False
        if self._clicks_per_ip:
            self._log(
                f"Clicks per IP: {self._clicks_per_ip} (the same IP serves a whole "
                f"batch of clicks before the engine switches IP)."
            )

        if not self.proxies and self.config.block_direct:
            self._log(
                "Refusing to run: no usable proxy and direct traffic is disabled. "
                "Without a proxy every visit would come from this machine's own IP "
                "(the 'visits show my own country' problem). Test the proxies in "
                "Proxy Manager first, or untick 'Block direct traffic' in Settings."
            )
            return

        # Burst mode tracking
        burst_count = 0
        burst_mode = self.config.burst_mode
        burst_size = self.config.burst_requests

        mode_str = "burst" if burst_mode else "continuous"
        self._log(
            f"Fast engine started ({mode_str}). {len(self.proxies)} proxies, {self.config.max_threads} threads."
        )
        if self.config.total_visits > 0:
            self._log(
                f"Target: {self.config.total_visits} delivered visits "
                f"(failed attempts are retried until the target is reached)."
            )

        tasks = set()

        try:
            while self.running:
                # Replenish tasks up to max_threads
                while len(tasks) < self.config.max_threads and self.running:
                    if self._finished():
                        self.running = False
                        break

                    task = asyncio.create_task(self._make_request())
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)

                    # Track burst progress
                    if burst_mode:
                        burst_count += 1
                        if burst_count >= burst_size:
                            break  # Exit inner loop to trigger sleep

                if not self.running:
                    break

                # Burst mode: sleep between bursts
                if burst_mode and burst_count >= burst_size:
                    # Wait for current burst to complete
                    if tasks:
                        await asyncio.wait(tasks, timeout=10)
                        tasks = set()

                    # Sleep between bursts
                    sleep_time = random.uniform(
                        self.config.burst_sleep_min, self.config.burst_sleep_max
                    )
                    self._log(
                        f"Burst complete ({burst_size} requests). Sleeping {sleep_time:.1f}s..."
                    )
                    await asyncio.sleep(sleep_time)
                    burst_count = 0
                else:
                    await asyncio.sleep(0.1)  # Prevent tight loop

            # Wait for pending tasks to finish gracefully
            if tasks:
                await asyncio.wait(tasks, timeout=5)

        finally:
            self._log(
                f"Fast engine stopped. {self.stats.success} visits delivered, "
                f"{self.stats.failed} failed out of {self.stats.total_requests} attempts "
                f"({self.stats.delivery_rate * 100:.0f}% delivery rate)."
            )

    def stop(self):
        self.running = False
