"""Live check: the campaign size the operator picks is the campaign size that runs.

Reported from the field: "I set 5000 / 10000 / unlimited but it stops after 100,
13, 20 and says finished." The amount box is restored from the saved setting and
used to win over the preset on every keystroke, so an old number kept overriding
whatever was selected - including Unlimited, which was clamped to 1.

Run with the interpreter that has the project's dependencies (Python 3.13 here).
"""
import os
import sys
import traceback

from pathlib import Path

_HERE = Path(__file__).resolve().parent
PROJECT = str(_HERE.parent)
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)

FAILURES = []


def fail(msg):
    FAILURES.append(msg)
    print("  FAIL  " + msg)


def ok(msg):
    print("  PASS  " + msg)


def expect(label, got, want):
    if got != want:
        fail("%s -> got %r, expected %r" % (label, got, want))
    else:
        ok("%s -> %r" % (label, got))


print("[1] building the application")
from ui.app import ModernTrafficBot  # noqa: E402

try:
    app = ModernTrafficBot()
except Exception:
    print(traceback.format_exc())
    fail("ModernTrafficBot() raised during construction")
    raise SystemExit(1)

for _ in range(4):
    app.update_idletasks()
    app.update()

page = app.dashboard_page
box = page.entry_visits

print("[2] a preset must beat a stale number left in the amount box")
box.delete(0, "end")
box.insert(0, "13")                       # what an earlier run left behind
page.on_visits_change("5000")
expect("box cleared after choosing preset 5000", box.get().strip(), "")
expect("campaign size after preset 5000", page.visits_target(), 5000)
expect("settings.total_visits", app.settings.get("total_visits"), 5000)

page.on_visits_change("Unlimited")
expect("campaign size after Unlimited", page.visits_target(), 0)
expect("settings.total_visits for Unlimited", app.settings.get("total_visits"), 0)

print("[3] a typed amount is used once Apply is pressed")
box.delete(0, "end")
box.insert(0, "10000")
page.on_apply_amount()
expect("campaign size after typing 10000 + Apply", page.visits_target(), 10000)
expect("settings.total_visits", app.settings.get("total_visits"), 10000)

print("[4] typing without Apply must not change the running campaign size")
page.on_visits_change("2500")
box.delete(0, "end")
box.insert(0, "7")                        # half-typed / abandoned number
expect("size unchanged while the box is only being typed in",
       app.settings.get("total_visits"), 2500)

print("[5] '0' means unlimited, not one visit")
page.on_visits_change("1000")
box.delete(0, "end")
box.insert(0, "0")
page.on_apply_amount()
expect("campaign size for 0", page.visits_target(), 0)

print("[6] an empty box falls back to the preset")
box.delete(0, "end")
page.on_visits_change("500")
expect("campaign size with an empty box", page.visits_target(), 500)

print("[7] the engine is configured with exactly this number")
from core.config_builder import TrafficConfigBuilder  # noqa: E402

built = TrafficConfigBuilder.from_settings(
    {**app.settings, "total_visits": page.visits_target()}, "https://example.com"
)
expect("TrafficConfig.total_visits", built.total_visits, 500)

try:
    app.destroy()
except Exception:
    pass

print("")
print("failures: %d" % len(FAILURES))
print("RESULT: %s" % ("ALL PASS" if not FAILURES else "FAILED"))
raise SystemExit(1 if FAILURES else 0)
