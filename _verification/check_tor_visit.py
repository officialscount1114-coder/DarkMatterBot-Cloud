"""What exactly happens on a Tor visit now? Print every log line.

Run with the interpreter that has the project's dependencies (Python 3.13).
"""
import asyncio
import sys
import time

PROJECT = r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL"
sys.path.insert(0, PROJECT)

from core.browser_engine import PlaywrightTrafficEngine      # noqa: E402
from core.config_builder import TrafficConfigBuilder         # noqa: E402
from core.models import ProxyConfig                          # noqa: E402

SETTINGS = {
    "target_url": "https://deshilink.my/VmTZnigp",
    "threads": 1,
    "viewtime_min": 15,
    "viewtime_max": 18,
    "clicks_per_ip": 1,
    "burst_mode": False,
    "block_direct_traffic": True,
    "total_visits": 0,
    "engine_mode": "browser",
    "headless": 1,
    "stealth_enabled": True,
}


async def main() -> None:
    config = TrafficConfigBuilder.from_settings(SETTINGS, SETTINGS["target_url"])
    try:
        config.browser.max_contexts = 1
    except Exception:
        pass

    proxy = ProxyConfig(host="127.0.0.1", port=9050, protocol="socks5")
    engine = PlaywrightTrafficEngine(config, [proxy])
    engine.on_log = lambda message: print("   " + str(message), flush=True)
    engine.running = True

    async def rotate():
        print("   [would rotate the Tor exit here]", flush=True)

    engine.rotate_hook = rotate

    task = asyncio.create_task(engine.run())

    async def stop_after(seconds):
        await asyncio.sleep(seconds)
        engine.running = False

    stopper = asyncio.create_task(stop_after(int(sys.argv[1]) if len(sys.argv) > 1 else 90))
    await asyncio.gather(task, stopper, return_exceptions=True)

    print(
        f"\nRESULT attempts={engine.stats.total_requests} delivered={engine.stats.success} "
        f"failed={engine.stats.failed} tracked={engine.stats.tracked}",
        flush=True,
    )
    if getattr(engine, "_failure_reasons", None):
        print("REASONS " + str(dict(engine._failure_reasons)), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
