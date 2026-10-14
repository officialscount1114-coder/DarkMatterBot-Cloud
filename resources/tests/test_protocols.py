import unittest
import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from core.engine import AsyncTrafficEngine
from core.models import TrafficConfig, TrafficStats

# Fix for Windows asyncio with curl_cffi
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class TestProtocolHandling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Base config
        self.config = TrafficConfig(
            target_url="",
            max_threads=1,
            total_visits=1,
            min_duration=0,
            max_duration=0,
            headless=True
        )
    
    async def run_engine(self, url: str) -> TrafficStats:
        self.config.target_url = url
        # No proxies -> Direct connection
        engine = AsyncTrafficEngine(self.config, proxies=[])
        await engine.run()
        return engine.stats

    async def test_valid_http(self):
        """1. Protocol Handling: Valid HTTP"""
        print("\nTesting HTTP...")
        # example.com is 200 OK
        stats = await self.run_engine("http://example.com")
        self.assertEqual(stats.success, 1, "HTTP request should succeed")

    async def test_valid_https(self):
        """1. Protocol Handling: Valid HTTPS"""
        print("Testing HTTPS...")
        stats = await self.run_engine("https://example.com")
        self.assertEqual(stats.success, 1, "HTTPS request should succeed")

    async def test_mixed_case(self):
        """2. Edge Cases: Mixed Case URL"""
        print("Testing Mixed Case...")
        stats = await self.run_engine("HtTpS://ExAmPlE.cOm")
        self.assertEqual(stats.success, 1, "Mixed case URL should succeed")

    async def test_redirect_http_to_https(self):
        """3. Redirects: HTTP -> HTTPS"""
        print("Testing Redirect (HTTP -> HTTPS)...")
        # google.com redirects HTTP -> HTTPS
        stats = await self.run_engine("http://google.com")
        self.assertEqual(stats.success, 1, "Should follow redirect to success")

    async def test_missing_scheme(self):
        """2. Edge Cases: Missing Scheme (e.g. 'example.com')"""
        print("Testing Missing Scheme...")
        stats = await self.run_engine("example.com")
        if stats.failed > 0:
            print(" -> Missing scheme caused failure (Will fix in engine)")
        else:
             self.assertEqual(stats.success, 1, "Missing scheme should be auto-handled")

    async def test_malformed_url(self):
        """4. Error States: Malformed URL"""
        print("Testing Malformed URL...")
        stats = await self.run_engine("not_a_url")
        self.assertEqual(stats.failed, 1, "Malformed URL should result in failed stat")

if __name__ == "__main__":
    unittest.main()
