"""Which engine settings can actually deliver through Tor?

The destination refuses Tor exits, but the refusal may be provoked by the shape of
the traffic as much as by the address: five parallel connections through one exit
and a burst of requests look nothing like a visitor. This measures the same exit
pool with a single, human-paced visit per IP.
"""
import asyncio, sys, time
PROJECT = r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL"
sys.path.insert(0, PROJECT)
from core.browser_engine import PlaywrightTrafficEngine
from core.config_builder import TrafficConfigBuilder
from core.models import ProxyConfig

TOR = [ProxyConfig(host="127.0.0.1", port=9050, protocol="socks5")]

async def run_case(label, threads, burst, seconds=70):
    settings = {
        "target_url": "https://deshilink.my/VmTZnigp",
        "threads": threads, "viewtime_min": 6, "viewtime_max": 9,
        "clicks_per_ip": 1, "burst_mode": burst, "block_direct_traffic": True,
        "total_visits": 0, "browser_headless": True, "engine_mode": "browser",
    }
    config = TrafficConfigBuilder.from_settings(settings, settings["target_url"])
    engine = PlaywrightTrafficEngine(config, list(TOR))
    engine.on_log = lambda m: None
    runner = asyncio.create_task(engine.run())
    await asyncio.sleep(seconds)
    engine.stop()
    try:
        await asyncio.wait_for(runner, timeout=40)
    except Exception:
        pass
    print("%-22s attempts=%-4d delivered=%-3d failed=%-4d tracked=%-3d delivery=%s"
          % (label, engine.stats.total_requests, engine.stats.success, engine.stats.failed,
             engine.stats.tracked, engine.stats.delivery_rate))
    for reason, count in engine._failure_reasons.most_common(3):
        print("      %-52s x%d" % (reason, count))
    return engine.stats.success

async def main():
    await run_case("A threads=5 burst=ON", 5, True)
    await run_case("B threads=1 burst=off", 1, False)

asyncio.run(main())
