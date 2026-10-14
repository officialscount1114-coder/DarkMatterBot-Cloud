import asyncio, sys, time
sys.path.insert(0, r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL")
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

counts = {"scroll": 0, "move": 0, "clicks": 0, "clicked_ok": 0}
o_scroll, o_wander, o_click = (engine._human_scroll, engine._human_pointer_wander,
                               engine._click_something_harmless)

async def scroll(page, since, budget):
    counts["scroll"] += 1
    return await o_scroll(page, since, budget)

async def wander(page):
    counts["move"] += 1
    return await o_wander(page)

async def click(page):
    counts["clicks"] += 1
    ok = await o_click(page)
    counts["clicked_ok"] += 1 if ok else 0
    return ok

engine._human_scroll, engine._human_pointer_wander = scroll, wander
engine._click_something_harmless = click

async def main():
    runner = asyncio.create_task(engine.run())
    await asyncio.sleep(30)
    engine.stop()
    try:
        await asyncio.wait_for(runner, timeout=40)
    except Exception:
        pass

asyncio.run(main())
print("delivered=%d failed=%d tracked=%d" % (engine.stats.success, engine.stats.failed, engine.stats.tracked))
print("scroll passes=%d  pointer wanders=%d  click attempts=%d  clicks landed=%d"
      % (counts["scroll"], counts["move"], counts["clicks"], counts["clicked_ok"]))
