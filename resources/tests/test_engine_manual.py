import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from core.models import TrafficConfig, ProxyConfig, TrafficStats
from core.engine import AsyncTrafficEngine

def print_stats(stats: TrafficStats):
    print(f"\r[Stats] Success: {stats.success} | Failed: {stats.failed} | Active: {stats.active_threads}", end="")

async def main():
    print("Starting Manual Engine Test...")
    
    # Config: Target httpbin to see our headers
    config = TrafficConfig(
        target_url="https://httpbin.org/get",
        max_threads=2,
        total_visits=5,
        min_duration=1,
        max_duration=2,
        headless=True
    )
    
    proxies = [] # No proxies for this test, direct connection
    
    engine = AsyncTrafficEngine(config, proxies, on_update=print_stats)
    
    print(f"Targeting: {config.target_url}")
    print("Press Ctrl+C to stop manually if needed.")
    
    try:
        await engine.run()
    except KeyboardInterrupt:
        engine.stop()
    
    print("\nTest Complete.")

if __name__ == "__main__":
    asyncio.run(main())
