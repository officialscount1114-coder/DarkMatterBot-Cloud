"""
DarkMatter Traffic Bot - Scenario Engine (Multi-Step Human Browsing Simulation)

Simulates realistic multi-page browsing journeys:
Landing -> Human Dwell Delay -> Subpage Exploration -> Internal Link Navigation
with realistic Referer header chains and bounce rates.
"""

import asyncio
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from curl_cffi import requests

from .constants import BROWSER_IMPERSONATIONS, get_referers
from .models import ProxyConfig, TrafficStats


@dataclass
class ScenarioStep:
    """A single step in a multi-page user session."""
    url: str
    dwell_time_seconds: float = 3.0
    referer: str = ""
    status_code: int = 0
    duration_ms: float = 0.0
    success: bool = False


@dataclass
class ScenarioSession:
    """A complete multi-step user browsing session."""
    session_id: str
    proxy: ProxyConfig | None = None
    steps: list[ScenarioStep] = field(default_factory=list)
    total_time: float = 0.0
    success: bool = False


class ScenarioEngine:
    """
    Advanced multi-step browsing session engine.
    Emulates realistic user navigation flows across websites.
    """

    def __init__(
        self,
        target_url: str,
        proxies: list[ProxyConfig] | None = None,
        max_depth: int = 3,
        min_dwell: float = 2.0,
        max_dwell: float = 6.0,
        bounce_rate: float = 0.25,
        impersonate: str = "chrome120",
        on_log: Callable[[str], None] | None = None,
        on_update: Callable[[TrafficStats], None] | None = None,
    ):
        self.target_url = target_url
        self.proxies = proxies or []
        self.max_depth = max(1, max_depth)
        self.min_dwell = min_dwell
        self.max_dwell = max_dwell
        self.bounce_rate = bounce_rate
        self.impersonate = impersonate
        self.on_log = on_log
        self.on_update = on_update

        self.target_domain = urlparse(target_url).netloc
        self.running = False
        self._stop_event = asyncio.Event()
        self.logger = logging.getLogger("dm-scenario")
        self.stats = TrafficStats()

    def _log(self, msg: str):
        self.logger.info(msg)
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass

    def _extract_internal_links(self, html: str, base_url: str) -> list[str]:
        """Extract internal links from page HTML."""
        internal_links = set()
        href_pattern = re.compile(r'href=["\'](.*?)["\']', re.IGNORECASE)

        for match in href_pattern.finditer(html):
            link = match.group(1).strip()
            if not link or link.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                continue

            full_url = urljoin(base_url, link)
            parsed = urlparse(full_url)

            # Check if domain matches target domain
            if parsed.netloc == self.target_domain and parsed.scheme in ("http", "https"):
                # Avoid binary / static assets
                path_lower = parsed.path.lower()
                if not any(path_lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".pdf", ".zip", ".woff", ".woff2")):
                    internal_links.add(full_url)

        return list(internal_links)

    async def execute_session(self, proxy: ProxyConfig | None = None) -> ScenarioSession:
        """Run a single multi-page user session."""
        session_id = f"sess_{random.randint(10000, 99999)}"
        session_result = ScenarioSession(session_id=session_id, proxy=proxy)
        start_session_time = time.time()

        proxy_dict = None
        if proxy:
            proxy_url = f"{proxy.protocol}://{proxy.ip}:{proxy.port}"
            if proxy.username and proxy.password:
                proxy_url = f"{proxy.protocol}://{proxy.username}:{proxy.password}@{proxy.ip}:{proxy.port}"
            proxy_dict = {"http": proxy_url, "https": proxy_url}

        impersonate_choice = self.impersonate
        if impersonate_choice == "random" or not impersonate_choice:
            impersonate_choice = random.choice(BROWSER_IMPERSONATIONS)

        initial_referer = random.choice(get_referers() or ["https://www.google.com/"])
        current_url = self.target_url
        current_referer = initial_referer

        async with requests.AsyncSession(impersonate=impersonate_choice) as session:
            for step_num in range(1, self.max_depth + 1):
                if self._stop_event.is_set():
                    break

                step_start = time.time()
                step = ScenarioStep(url=current_url, referer=current_referer)

                try:
                    headers = {"Referer": current_referer} if current_referer else {}
                    resp = await session.get(
                        current_url,
                        headers=headers,
                        proxies=proxy_dict,
                        timeout=15,
                    )

                    step.status_code = resp.status_code
                    step.duration_ms = round((time.time() - step_start) * 1000, 2)
                    step.success = 200 <= resp.status_code < 400

                    self._log(f"[{session_id}] Step {step_num}/{self.max_depth} -> {current_url} ({resp.status_code}, {step.duration_ms}ms)")

                    if step.success:
                        self.stats.success_requests += 1
                        # If bounce rate check hits or it's the last step, end session
                        if step_num == 1 and random.random() < self.bounce_rate:
                            self._log(f"[{session_id}] User bounced after landing page.")
                            session_result.steps.append(step)
                            break

                        # Extract internal links for next step
                        if step_num < self.max_depth:
                            internal_links = self._extract_internal_links(resp.text, current_url)
                            if internal_links:
                                next_url = random.choice(internal_links)
                                dwell = random.uniform(self.min_dwell, self.max_dwell)
                                step.dwell_time_seconds = dwell
                                session_result.steps.append(step)

                                self._log(f"[{session_id}] Dwell time: {dwell:.1f}s before next page...")
                                await asyncio.sleep(dwell)

                                current_referer = current_url
                                current_url = next_url
                                continue
                    else:
                        self.stats.failed_requests += 1

                except Exception as e:
                    step.success = False
                    self.stats.failed_requests += 1
                    self._log(f"[{session_id}] Step {step_num} error on {current_url}: {e}")

                session_result.steps.append(step)
                if not step.success:
                    break

        session_result.total_time = round(time.time() - start_session_time, 2)
        session_result.success = any(s.success for s in session_result.steps)
        return session_result

    async def run(self, total_sessions: int = 10, concurrency: int = 3):
        """Run multiple scenario sessions concurrently."""
        self.running = True
        self._stop_event.clear()
        self._log(f"Starting Scenario Engine: {total_sessions} sessions, max_depth={self.max_depth}, concurrency={concurrency}")

        sem = asyncio.Semaphore(concurrency)
        completed = 0

        async def worker():
            nonlocal completed
            async with sem:
                if self._stop_event.is_set():
                    return
                proxy = random.choice(self.proxies) if self.proxies else None
                await self.execute_session(proxy=proxy)
                completed += 1
                if self.on_update:
                    try:
                        self.on_update(self.stats)
                    except Exception:
                        pass

        tasks = [asyncio.create_task(worker()) for _ in range(total_sessions)]
        await asyncio.gather(*tasks, return_exceptions=True)

        self.running = False
        self._log(f"Scenario Engine completed: {completed}/{total_sessions} sessions executed.")

    def stop(self):
        """Signal engine to stop."""
        self._stop_event.set()
        self.running = False
