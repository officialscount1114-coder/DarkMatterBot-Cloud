import asyncio
import contextlib
import logging
import random
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from curl_cffi import requests

from .constants import (
    BROWSER_HEADERS,
    BROWSER_IMPERSONATIONS,
    MAX_PROXY_CONSECUTIVE_FAILURES,
    NO_PROGRESS_ATTEMPT_FACTOR,
    PROXY_ERROR_CODES,
    REQUEST_TIMEOUT_SECONDS,
    SUCCESS_STATUS_CODES,
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
        # Extract domain for session management
        self._target_domain = urlparse(config.target_url).netloc

    def _log(self, message: str):
        """Log message to both Python logger and GUI callback."""
        logging.info(message)
        if self.on_log:
            self.on_log(message)

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
        """Acquire an available proxy for exclusive use by a task."""
        async with self._proxy_lock:
            if not self.proxies:
                return None

            # Find proxies not currently in use
            available = [
                p for p in self.proxies if (p.host, p.port) not in self._proxies_in_use
            ]

            if available:
                # Weighted selection from available proxies
                try:
                    weights = [max(p.score, 0.1) for p in available]
                    proxy = random.choices(available, weights=weights, k=1)[0]
                except (ValueError, IndexError):
                    proxy = random.choice(available)
            else:
                # All proxies in use - use round-robin to distribute load
                self._proxy_index = (self._proxy_index + 1) % len(self.proxies)
                proxy = self.proxies[self._proxy_index]

            # Mark proxy as in use
            self._proxies_in_use.add((proxy.host, proxy.port))
            # Say which proxy (and where) this request goes out through, so the
            # rotation is visible in the log.
            try:
                label = proxy.label() if hasattr(proxy, "label") else ""
                endpoint = proxy.endpoint() if hasattr(proxy, "endpoint") else f"{proxy.host}:{proxy.port}"
                self._log(f"[via] {endpoint}{label}")
            except Exception:
                pass
            return proxy

    async def _release_proxy(self, proxy: ProxyConfig | None):
        """Release a proxy back to the pool."""
        if proxy is None:
            return
        async with self._proxy_lock:
            self._proxies_in_use.discard((proxy.host, proxy.port))

    async def _make_request(self):
        """Performs a single visit using a unique proxy and browser impersonation."""
        if not self.running:
            return

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

            # Create fresh session for each request (clean cookies/TLS state per "user")
            session = requests.AsyncSession(impersonate=impersonate)

            # Load persisted cookies if session manager is available
            if self.session_manager:
                session_data = self.session_manager.get_session(self._target_domain)
                if session_data and session_data.cookies:
                    for cookie in session_data.cookies:
                        session.cookies.set(
                            cookie.get("name", ""),
                            cookie.get("value", ""),
                            domain=cookie.get("domain", self._target_domain),
                        )

            # Set proxy for this request
            proxies_dict = {"http": proxy, "https": proxy} if proxy else None

            # Use browser-consistent headers (User-Agent is set by curl_cffi impersonate)
            headers = BROWSER_HEADERS.copy()
            headers["Referer"] = random.choice(get_referers())

            logging.debug(f"Request to {self.config.target_url} via {impersonate}")

            response = await session.get(
                self.config.target_url,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
                verify=self.config.verify_ssl,
                proxies=proxies_dict,
            )

            if response.status_code in SUCCESS_STATUS_CODES:
                self.stats.success += 1
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
