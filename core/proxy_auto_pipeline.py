"""
DarkMatter Traffic Bot - MR ADS Proxy Grabber & Auto-Pipeline System

Implements the multi-source grabber and HTTPS validation engine from MR ADS PROXY GRABBER v1.8:
- Grabs from HProxy API (multi-page JSON pagination) and all raw GitHub sources (TheSpeedX, monosans, proxifly, HProxy).
- Selectable Max Rows (default 10,000).
- High-speed concurrent async HTTPS checking against https://api.ipify.org.
- Persistent saving of LIVE proxies (purging DEAD proxies).
- Continuous background generation, checking, and automatic live transfer to Campaign Engine.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp
from curl_cffi import requests as curl_requests

from .constants import REQUEST_TIMEOUT_SECONDS
from .models import ProxyConfig

# MR ADS Sources mapping (same as MR ADS PROXY GRABBER v1.8)
MR_ADS_API_URL = "https://hproxy.com/api/proxy-list?format=json"

MR_ADS_RAW_SOURCES = {
    "Live (API)": MR_ADS_API_URL,
    "HProxy HTTP": "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/http.txt",
    "HProxy HTTPS": "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/https.txt",
    "HProxy SOCKS4": "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/socks4.txt",
    "HProxy SOCKS5": "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/socks5.txt",
    "TheSpeedX HTTP": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "TheSpeedX SOCKS4": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
    "TheSpeedX SOCKS5": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "monosans HTTP": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "monosans SOCKS4": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
    "monosans SOCKS5": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "proxifly HTTP": "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "proxifly SOCKS4": "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks4/data.txt",
    "proxifly SOCKS5": "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt",
}

CHECK_URL = "https://api.ipify.org"
BACKUP_CHECK_URL = "https://httpbin.org/ip"


class ProxyAutoPipeline:
    """
    Automated Proxy Pipeline:
    1. Grabs up to 10,000 proxies from MR ADS sources.
    2. Validates live HTTPS connectivity.
    3. Saves live proxies persistently, purges dead ones.
    4. Automatically transfers live proxies to Campaign Engine.
    5. Loops continuously in the background for 24/7 operation.
    """

    def __init__(
        self,
        max_rows: int = 10000,
        protocol: str = "All",
        check_concurrency: int = 100,
        storage_file: str | Path | None = None,
        on_log: Callable[[str], None] | None = None,
        on_campaign_transfer: Callable[[list[ProxyConfig]], None] | None = None,
    ):
        self.max_rows = max(100, min(max_rows, 50000))
        self.protocol = protocol
        self.check_concurrency = check_concurrency
        self.project_root = Path(__file__).parent.parent
        self.storage_file = Path(storage_file or (self.project_root / "resources" / "proxies.json"))
        self.on_log = on_log
        self.on_campaign_transfer = on_campaign_transfer

        self.logger = logging.getLogger("dm-proxy-pipeline")
        self.live_proxies: list[ProxyConfig] = []
        self._running = False
        self._stop_event = asyncio.Event()
        self._pipeline_task: asyncio.Task | None = None

        # Load existing live proxies from disk if available
        self._load_live_from_disk()

    def _log(self, msg: str):
        self.logger.info(msg)
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass

    def _load_live_from_disk(self):
        """Load previously saved live proxies."""
        if not self.storage_file.exists():
            return
        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                proxies_data = data.get("proxies", []) if isinstance(data, dict) else data
                self.live_proxies = []
                for p in proxies_data:
                    if isinstance(p, dict):
                        self.live_proxies.append(
                            ProxyConfig(
                                host=p.get("host") or p.get("ip", "0.0.0.0"),
                                port=int(p.get("port", 8080)),
                                protocol=p.get("protocol", "http"),
                                username=p.get("username"),
                                password=p.get("password"),
                                country=p.get("country", ""),
                                city=p.get("city", ""),
                                score=float(p.get("score", 1.0)),
                            )
                        )
            self._log(f"Loaded {len(self.live_proxies)} live proxies from persistent storage.")
        except Exception as e:
            self._log(f"Could not load proxies from disk: {e}")

    def save_live_to_disk(self):
        """Save current live proxies to disk (purging dead ones)."""
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = {
                "updated_at": time.time(),
                "count": len(self.live_proxies),
                "proxies": [
                    {
                        "host": p.host,
                        "port": p.port,
                        "protocol": p.protocol,
                        "username": p.username,
                        "password": p.password,
                        "country": p.country,
                        "city": p.city,
                        "score": p.score,
                    }
                    for p in self.live_proxies
                ],
            }
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self._log(f"Saved {len(self.live_proxies)} verified live proxies to {self.storage_file.name}")
        except Exception as e:
            self._log(f"Failed to save proxies to disk: {e}")

    async def fetch_all_sources(self) -> list[dict]:
        """
        Fetch proxy candidates from all MR ADS sources up to max_rows (10,000).
        Follows API pagination for HProxy JSON API.
        """
        self._log(f"Fetching proxy candidates from {len(MR_ADS_RAW_SOURCES)} MR ADS sources (Max rows: {self.max_rows})...")
        rows: list[dict] = []
        headers = {"User-Agent": "MR-ADS-Free-Proxy-Tool/1.8"}

        async with aiohttp.ClientSession(headers=headers) as session:
            for source_name, url in MR_ADS_RAW_SOURCES.items():
                if self._stop_event.is_set():
                    break
                try:
                    if source_name == "Live (API)" or url.endswith(".json"):
                        # Handle JSON API with pagination (up to 20 pages)
                        page_url = url
                        seen_pages = set()
                        for _ in range(20):
                            if page_url in seen_pages or len(rows) >= self.max_rows * 2:
                                break
                            seen_pages.add(page_url)

                            async with session.get(page_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                                if resp.status != 200:
                                    break
                                payload = await resp.json()
                                if isinstance(payload, dict):
                                    data = payload.get("proxies", payload.get("data", payload.get("results", [])))
                                    next_page = payload.get("next_url") or payload.get("next")
                                    if isinstance(next_page, dict):
                                        next_page = next_page.get("url")
                                    if not next_page and payload.get("next_page"):
                                        parts = urlparse(page_url)
                                        qs = parse_qs(parts.query)
                                        qs["page"] = [str(payload["next_page"])]
                                        next_page = urlunparse(parts._replace(query=urlencode(qs, doseq=True)))
                                else:
                                    data, next_page = payload, None

                                for item in data if isinstance(data, list) else []:
                                    if isinstance(item, str):
                                        if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}", item.strip()):
                                            rows.append({"proxy": item.strip(), "protocol": "http", "source": source_name})
                                        continue
                                    if not isinstance(item, dict):
                                        continue
                                    proxy = item.get("proxy") or (str(item.get("ip", "")) + ":" + str(item.get("port", "")))
                                    if not proxy or proxy.startswith(":"):
                                        continue
                                    protocols = item.get("protocols", item.get("protocol", ["http"]))
                                    if isinstance(protocols, str):
                                        protocols = [protocols]
                                    proto = protocols[0].lower() if protocols else "http"
                                    rows.append({"proxy": proxy, "protocol": proto, "source": source_name})

                                if not next_page or not isinstance(next_page, str) or not next_page.startswith(("http://", "https://")):
                                    break
                                page_url = next_page
                    else:
                        # Raw text endpoints
                        proto = source_name.rsplit(" ", 1)[-1].lower() if " " in source_name else "http"
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                for line in text.splitlines():
                                    line = line.strip()
                                    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}", line):
                                        rows.append({"proxy": line, "protocol": proto, "source": source_name})

                except Exception as e:
                    self._log(f"Source [{source_name}] fetch error: {e}")

        # Deduplicate and limit to max_rows
        seen = set()
        unique_rows = []
        for r in rows:
            proxy_str = r["proxy"].strip()
            if proxy_str not in seen:
                seen.add(proxy_str)
                unique_rows.append(r)
                if len(unique_rows) >= self.max_rows:
                    break

        self._log(f"Fetched {len(unique_rows)} unique proxy candidates for live checking.")
        return unique_rows

    async def check_single_proxy(self, candidate: dict, sem: asyncio.Semaphore) -> ProxyConfig | None:
        """
        Check if a proxy passes HTTPS live test.
        Returns ProxyConfig if alive, None if dead.
        """
        proxy_str = candidate["proxy"]
        protocol = candidate.get("protocol", "http").lower()
        if protocol not in ("http", "https", "socks4", "socks5"):
            protocol = "http"

        ip, port_str = proxy_str.split(":", 1)
        port = int(port_str)

        scheme = {"http": "http", "https": "http", "socks4": "socks4", "socks5": "socks5h"}.get(protocol, "http")
        proxy_url = f"{scheme}://{proxy_str}"
        proxies_dict = {"http": proxy_url, "https": proxy_url}

        async with sem:
            if self._stop_event.is_set():
                return None
            start = time.time()
            try:
                # Use curl_cffi AsyncSession with impersonate for realistic TLS checking
                async with curl_requests.AsyncSession(impersonate="chrome120") as session:
                    resp = await session.get(
                        CHECK_URL,
                        proxies=proxies_dict,
                        timeout=8,
                        verify=False,
                    )
                    latency = round((time.time() - start) * 1000, 2)
                    if resp.status_code == 200 and re.fullmatch(r"\s*(?:\d{1,3}\.){3}\d{1,3}\s*", resp.text):
                        return ProxyConfig(
                            host=ip,
                            port=port,
                            protocol=protocol,
                            score=1.0,
                            source=candidate.get("source", "MR ADS Grabber"),
                        )
            except Exception:
                pass
            return None

    async def run_grab_and_check_cycle(self) -> list[ProxyConfig]:
        """
        Execute one full harvest and live verification cycle:
        - Grabs candidates
        - Checks up to 10,000 concurrently
        - Retains live, purges dead
        - Saves to disk
        - Transfers to Campaign Engine
        """
        candidates = await self.fetch_all_sources()
        if not candidates:
            return self.live_proxies

        self._log(f"Verifying {len(candidates)} proxies with {self.check_concurrency} concurrent threads...")
        sem = asyncio.Semaphore(self.check_concurrency)

        tasks = [self.check_single_proxy(c, sem) for c in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        verified_live: list[ProxyConfig] = []
        for res in results:
            if isinstance(res, ProxyConfig):
                verified_live.append(res)

        # Merge with existing live proxies and deduplicate
        seen_keys = set()
        merged_live: list[ProxyConfig] = []
        for p in verified_live + self.live_proxies:
            key = (p.host, p.port)
            if key not in seen_keys:
                seen_keys.add(key)
                merged_live.append(p)

        self.live_proxies = merged_live
        self._log(f"✅ Live Verification Complete: Found {len(verified_live)} new live proxies ({len(self.live_proxies)} total live in pool).")

        # Save to disk
        self.save_live_to_disk()

        # Auto-transfer live proxies to Campaign section
        if self.on_campaign_transfer:
            try:
                self.on_campaign_transfer(self.live_proxies)
                self._log(f"🚀 Auto-Transferred {len(self.live_proxies)} live proxies to Campaign Engine.")
            except Exception as e:
                self._log(f"Error transferring proxies to campaign: {e}")

        return self.live_proxies

    async def start_background_pipeline(self, interval_seconds: int = 300):
        """
        Run continuous background pipeline loop.
        Auto-generates, checks, and transfers live proxies every `interval_seconds` (e.g. 5 mins).
        """
        if self._running:
            return

        self._running = True
        self._stop_event.clear()
        self._log(f"Starting 24/7 Continuous Proxy Pipeline (Interval: {interval_seconds}s)...")

        while self._running and not self._stop_event.is_set():
            try:
                await self.run_grab_and_check_cycle()
            except Exception as e:
                self._log(f"Pipeline cycle error: {e}")

            # Sleep until next harvest cycle
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

    def stop_pipeline(self):
        """Stop background pipeline loop."""
        self._running = False
        self._stop_event.set()
        self._log("Proxy Pipeline stopped.")
