"""
DarkMatter Traffic Bot - Source Updater & Public Proxy List Sync

Automatically fetches, aggregates, and synchronizes fresh proxy source lists
from high-reputation public repositories and endpoints.
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

PUBLIC_SOURCE_ENDPOINTS = [
    # Top reputable raw proxy endpoints
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt",
    "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/socks5_proxies.txt",
]


class SourceUpdater:
    """
    Downloads and updates proxy sources from remote repositories.
    """

    def __init__(self, sources_file: str | Path | None = None, on_log: Callable[[str], None] | None = None):
        self.project_root = Path(__file__).parent.parent
        self.sources_file = Path(sources_file or (self.project_root / "resources" / "sources.txt"))
        self.on_log = on_log
        self.logger = logging.getLogger("dm-source-updater")

    def _log(self, msg: str):
        self.logger.info(msg)
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass

    async def fetch_source_url(self, session: aiohttp.ClientSession, url: str) -> list[str]:
        """Fetch list of proxies from a remote URL."""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
                    return lines
        except Exception as e:
            self._log(f"Failed to fetch {url}: {e}")
        return []

    async def update_sources_file(self, custom_endpoints: list[str] | None = None) -> int:
        """
        Sync public proxy endpoints into the sources.txt file.
        Returns total number of unique endpoints.
        """
        endpoints = custom_endpoints or PUBLIC_SOURCE_ENDPOINTS
        self._log(f"Syncing proxy sources with {len(endpoints)} verified endpoints...")

        existing_urls = set()
        if self.sources_file.exists():
            try:
                with open(self.sources_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            existing_urls.add(line)
            except Exception:
                pass

        new_count = 0
        for ep in endpoints:
            if ep not in existing_urls:
                existing_urls.add(ep)
                new_count += 1

        self.sources_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.sources_file, "w", encoding="utf-8") as f:
            f.write("# DarkMatter Bot - Curated Proxy Sources\n")
            f.write("# Auto-updated by SourceUpdater\n\n")
            for url in sorted(existing_urls):
                f.write(f"{url}\n")

        self._log(f"Updated {self.sources_file.name}: {len(existing_urls)} total sources ({new_count} newly added).")
        return len(existing_urls)

    async def scrape_raw_proxies(self) -> list[str]:
        """
        Scrape proxies directly from all public endpoints.
        Returns deduplicated list of 'ip:port' or 'protocol://ip:port' strings.
        """
        proxies = set()
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_source_url(session, url) for url in PUBLIC_SOURCE_ENDPOINTS]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for res in results:
                if isinstance(res, list):
                    for proxy_str in res:
                        if ":" in proxy_str and len(proxy_str.split(":")) >= 2:
                            proxies.add(proxy_str)

        self._log(f"Scraped {len(proxies)} unique proxies across public repositories.")
        return list(proxies)
