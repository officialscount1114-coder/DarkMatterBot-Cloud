"""Run a real campaign and report exactly what happens, second by second.

Boots the real app, configures the operator's setup (Tor source, browser engine,
20 clicks per IP, 30s timer, unlimited visits), presses START CAMPAIGN and then
watches: every log line, the stats, and whether the engine is still alive.

Usage: run_campaign_probe.py [seconds]
"""
import os
import sys
import time
from pathlib import Path

PROJECT = str(Path(__file__).resolve().parent.parent)
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)

seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 180

import customtkinter as ctk  # noqa: E402

ctk.set_appearance_mode("dark")

from ui.app import ModernTrafficBot  # noqa: E402

app = ModernTrafficBot()

# Reproduce the operator's configuration exactly (their saved file lost these).
app.settings.update(
    {
        "proxy_source": "tor",
        "clicks_per_ip": 20,
        "rotate_interval_seconds": 30,
        "max_ip_rotations": 0,
        "total_visits": 0,
        "engine_mode": "browser",
        "threads": 5,
        "block_direct_traffic": True,
        "tor_auto_start": True,
    }
)
page = app.dashboard_page
page.menu_proxy_source.set("Tor (free)")
page.menu_visits.set("Unlimited")
page.menu_clicks_per_ip.set("20")
page.menu_rotate_seconds.set("30s")
page.menu_max_ips.set("Unlimited")
page._apply_source_mode()
app.update()
app.update_idletasks()

print("target      :", app.settings.get("target_url"))
print("engine      :", app.settings.get("engine_mode"))
print("starting campaign in 1.2s (toggle_attack via after, exactly once) ...")

# The real app runs mainloop(), and the worker thread touches Tk widgets, so the
# sampler has to be an after() callback on the main thread - exactly like the
# app's own update loop. Driving it with update() from a while-loop made the
# engine die with "main thread is not in main loop".
state = {"seen": 0, "start": time.time(), "next_report": time.time() + 15,
         "stopped_at": None, "last_stats": None, "done": False}


def sample():
    if state["done"]:
        return
    elapsed = time.time() - state["start"]

    try:
        text = app.log_box.get("1.0", "end")
    except Exception:
        text = ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    while state["seen"] < len(lines):
        print("   ", lines[state["seen"]])
        state["seen"] += 1

    stats = (app.stats.get("req"), app.stats.get("success"), app.stats.get("fail"))
    if stats != state["last_stats"]:
        print("    [stats] attempts=%s delivered=%s failed=%s" % stats)
        state["last_stats"] = stats

    if not app.running and state["stopped_at"] is None and elapsed > 2:
        state["stopped_at"] = elapsed
        print("    >>> app.running became False at t=%.0fs" % elapsed)
        finish()
        return

    if time.time() >= state["next_report"]:
        state["next_report"] = time.time() + 30
        engine_alive = getattr(app.engine, "running", None) if app.engine else None
        print(
            "    [t=%3.0fs] app.running=%s engine.running=%s stats=%s"
            % (elapsed, app.running, engine_alive, stats)
        )

    if elapsed >= seconds:
        finish()
        return
    app.after(1000, sample)


def finish():
    if state["done"]:
        return
    state["done"] = True
    elapsed = time.time() - state["start"]
    print("\n================ RESULT ================")
    print("elapsed            : %.0fs" % elapsed)
    print("app.running        :", app.running)
    print("engine exists      :", app.engine is not None)
    if app.engine is not None:
        print("engine.running     :", getattr(app.engine, "running", None))
        print("engine stats       :", getattr(app.engine, "stats", None))
    print("stopped early at   :", state["stopped_at"])
    print("total log lines    :", state["seen"])
    try:
        app.running = False
        if app.engine:
            app.engine.stop()
    except Exception:
        pass
    app.after(1500, app.destroy)


app.after(1200, app.toggle_attack)
app.after(1500, sample)
app.mainloop()
