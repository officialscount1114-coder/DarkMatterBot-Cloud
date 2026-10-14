"""Live check: does a visit click things on the page during the dwell?"""
import asyncio, sys, time
PROJECT = r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL"
sys.path.insert(0, PROJECT)
from core.browser_engine import PlaywrightTrafficEngine
from core.config_builder import TrafficConfigBuilder

settings = {
    "target_url": "https://deshilink.my/VmTZnigp",
    "threads": 1, "viewtime_min": 20, "viewtime_max": 20, "clicks_per_ip": 1,
    "burst_mode": False, "block_direct_traffic": False, "total_visits": 0,
    "browser_headless": True, "engine_mode": "browser",
}
config = TrafficConfigBuilder.from_settings(settings, settings["target_url"])
logs = []
engine = PlaywrightTrafficEngine(config, [])
engine.on_log = logs.append

stats = {"tried": 0, "clicked": 0}
original = engine._click_something_harmless

async def counting_click(page):
    stats["tried"] += 1
    result = await original(page)
    if result:
        stats["clicked"] += 1
    print("   click attempt %d -> %s" % (stats["tried"], "clicked" if result else "nothing safe"))
    return result

engine._click_something_harmless = counting_click

async def main():
    runner = asyncio.create_task(engine.run())
    await asyncio.sleep(26)
    engine.stop()
    await asyncio.wait_for(runner, timeout=30)

asyncio.run(main())
print("")
print("delivered %d | tracked %d | clicks tried %d | clicks landed %d"
      % (engine.stats.success, engine.stats.tracked, stats["tried"], stats["clicked"]))
for line in logs:
    if "interaction" in line.lower() or "click" in line.lower():
        print("  LOG:", line[:120])
