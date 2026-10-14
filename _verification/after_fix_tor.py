"""After the fix: does the campaign now deliver anything through Tor?"""
import asyncio, sys
PROJECT = r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL"
sys.path.insert(0, PROJECT)
from core.browser_engine import PlaywrightTrafficEngine
from core.config_builder import TrafficConfigBuilder
from core.models import ProxyConfig

settings = {
    "target_url": "https://deshilink.my/VmTZnigp",
    "threads": 1, "viewtime_min": 6, "viewtime_max": 9, "clicks_per_ip": 1,
    "burst_mode": False, "block_direct_traffic": True, "total_visits": 0,
    "browser_headless": True, "engine_mode": "browser",
}
config = TrafficConfigBuilder.from_settings(settings, settings["target_url"])
logs = []
engine = PlaywrightTrafficEngine(config, [ProxyConfig(host="127.0.0.1", port=9050, protocol="socks5")])
engine.on_log = logs.append

async def main():
    runner = asyncio.create_task(engine.run())
    await asyncio.sleep(110)
    engine.stop()
    try:
        await asyncio.wait_for(runner, timeout=40)
    except Exception:
        pass

asyncio.run(main())
print("attempts=%d delivered=%d failed=%d tracked=%d delivery=%.0f%%"
      % (engine.stats.total_requests, engine.stats.success, engine.stats.failed,
         engine.stats.tracked, engine.stats.delivery_rate * 100))
print("rotations:", engine._ip_rotations)
for reason, count in engine._failure_reasons.most_common(4):
    print("   %-52s x%d" % (reason, count))
for line in logs:
    if "rotat" in line.lower() or "refus" in line.lower():
        print("  LOG:", line[:150])
