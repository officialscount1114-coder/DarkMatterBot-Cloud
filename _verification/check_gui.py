"""Live GUI check: really build the app, open every page and measure the layout.

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
NOTES = []


def fail(msg):
    FAILURES.append(msg)
    print("  FAIL  " + msg)


def note(msg):
    NOTES.append(msg)
    print("  ..    " + msg)


def ok(msg):
    print("  PASS  " + msg)


# ---------------------------------------------------------------- construct
print("[1] building the application")
from ui.app import ModernTrafficBot  # noqa: E402

try:
    app = ModernTrafficBot()
except Exception:
    print(traceback.format_exc())
    fail("ModernTrafficBot() raised during construction")
    raise SystemExit(1)

WINDOW_TITLE = app.title()
ok("window built, title=%r geometry=%r" % (WINDOW_TITLE, app.geometry()))

for _ in range(6):
    app.update_idletasks()
    app.update()

print("    window size: %dx%d" % (app.winfo_width(), app.winfo_height()))

# ---------------------------------------------------------------- pages
print("[2] opening every page")
page_keys = list(app.pages.keys())
print("    pages:", page_keys)
for key in page_keys:
    try:
        app.select_page(key, animate=False)
        for _ in range(4):
            app.update_idletasks()
            app.update()
        frame = app.pages[key]
        w, h = frame.winfo_width(), frame.winfo_height()
        if w <= 1 or h <= 1:
            fail("page %r laid out at %dx%d (collapsed)" % (key, w, h))
        else:
            ok("page %r -> %dx%d mapped=%s" % (key, w, h, bool(frame.winfo_ismapped())))
    except Exception as exc:
        fail("select_page(%r) raised %s: %s" % (key, type(exc).__name__, exc))

# ---------------------------------------------------------------- chrome
print("[3] window chrome and key panels")
app.select_page("run", animate=False)
for _ in range(6):
    app.update_idletasks()
    app.update()

for name, widget in (
    ("content", app.content),
    ("header", app.header),
    ("page_host", app.page_host),
    ("dashboard log box", app.log_box),
):
    try:
        w, h = widget.winfo_width(), widget.winfo_height()
        if w <= 1 or h <= 1:
            fail("%s is %dx%d (collapsed/blank)" % (name, w, h))
        else:
            ok("%s -> %dx%d" % (name, w, h))
    except Exception as exc:
        fail("%s could not be measured: %s" % (name, exc))

# ---------------------------------------------------------------- stat cards
print("[4] dashboard stat cards (Attempts / Delivered / Failed / Delivery / Proxies)")
expected_keys = {"req", "success", "fail", "rate", "proxies"}
missing = expected_keys - set(app.lbl_stats)
if missing:
    fail("missing stat cards: %s" % sorted(missing))
else:
    ok("all 5 cards present: %s" % sorted(app.lbl_stats))

try:
    app.stats["req"] = 100
    app.stats["success"] = 12
    app._update_delivery_rate_label()
    app.update_idletasks()
    shown = app.lbl_stats["rate"].cget("text")
    if shown != "12%":
        fail("delivery card shows %r, expected '12%%'" % shown)
    else:
        ok("delivery card computes the real rate (100 attempts / 12 delivered -> 12%%)")
except Exception as exc:
    fail("_update_delivery_rate_label raised: %s" % exc)

# ---------------------------------------------------------------- scaling
print("[5] DPI scaling of the raw proxy grid")
try:
    from ui.scaling import get_scaling_factor

    import customtkinter as ctk

    ctk_factor = ctk.ScalingTracker.get_widget_scaling(app)
    comp_factor = get_scaling_factor()
    row_h = app.proxy_grid.row_h
    print("    ctk get_widget_scaling(app) = %.3f" % ctk_factor)
    print("    ui.scaling.get_scaling_factor() = %.3f" % comp_factor)
    print("    proxy_grid.row_h = %d (base %d)" % (row_h, app.proxy_grid._base_row_h))

    if abs(comp_factor - ctk_factor) > 0.001:
        fail("component scaling factor %.3f != customtkinter %.3f" % (comp_factor, ctk_factor))
    else:
        ok("component scaling factor matches customtkinter exactly")

    expected_row_h = int(app.proxy_grid._base_row_h * ctk_factor)
    if row_h != expected_row_h:
        fail("grid row height %d != expected %d (canvas would mis-scale)" % (row_h, expected_row_h))
    else:
        ok("grid row height follows the real DPI factor (%d px)" % row_h)
except Exception as exc:
    fail("scaling check raised: %s" % exc)

# ---------------------------------------------------------------- header alignment
print("[6] proxy grid header columns vs canvas columns")
try:
    grid = app.proxy_grid
    grid.update_idletasks()
    app.update()
    canvas_width = grid.canvas.winfo_width()
    print("    canvas width = %d" % canvas_width)
    positions = grid._get_col_positions(canvas_width)
    worst = 0.0
    for index, btn in enumerate(grid.header_btns):
        expected_x = positions[index]
        actual_x = btn.winfo_x()
        drift = abs(actual_x - expected_x)
        worst = max(worst, drift)
    print("    headers=%d worst drift=%.1f px" % (len(grid.header_btns), worst))
    if worst > 3.0:
        fail("column headers drift up to %.1f px from their canvas columns" % worst)
    else:
        ok("column headers align with the canvas columns (<= %.1f px)" % worst)
except Exception as exc:
    fail("header alignment check raised: %s" % exc)

# ---------------------------------------------------------------- collapsed widgets
print("[7] scanning the visible dashboard for collapsed widgets")
try:
    app.select_page("run", animate=False)
    for _ in range(8):
        app.update_idletasks()
        app.update()

    collapsed = []
    # customtkinter builds private helpers (scrollbar, canvas, unused label)
    # inside its scrollable frames; those are not app layout.
    INTERNAL = ("CTkScrollbar", "CTkCanvas", "Canvas", "Scrollbar", "CTkScrollableFrame")

    def walk(widget, depth=0):
        if depth > 12:
            return
        for child in widget.winfo_children():
            try:
                cls = type(child).__name__
                if cls in INTERNAL:
                    continue
                if child.winfo_ismapped():
                    w, h = child.winfo_width(), child.winfo_height()
                    # 1px hairlines are intentional separators
                    if w <= 1 or h <= 1:
                        collapsed.append((cls, str(child), w, h))
            except Exception:
                pass
            walk(child, depth + 1)

    walk(app.pages["run"])
    if collapsed:
        for cls, text, w, h in collapsed[:12]:
            note("collapsed widget %s %r %dx%d" % (cls, text[:60], w, h))
        fail("%d visible widgets are collapsed on the dashboard" % len(collapsed))
    else:
        ok("no collapsed widgets on the dashboard")
except Exception as exc:
    fail("collapsed-widget scan raised: %s" % exc)

# ------------------------------------------------------- page switching / modes
print("[7b] Switching pages must not tear the layout")
if getattr(app, "_page_slide_enabled", True):
    fail("page geometry animation is still enabled - it re-lays out every widget "
         "on every frame, which is what made pages look half-drawn")
else:
    ok("page geometry animation is off (instant swap, no per-frame relayout)")

for key in ("run", "proxy", "stress", "master", "settings"):
    app.select_page(key, animate=True)
    for _ in range(8):
        app.update_idletasks()
        app.update()
    switched = app.pages[key]
    w, h = switched.winfo_width(), switched.winfo_height()
    if not switched.winfo_ismapped() or w < 300 or h < 200:
        fail("page %r did not settle after switching (%dx%d mapped=%s)"
             % (key, w, h, switched.winfo_ismapped()))
    else:
        ok("page %r settled cleanly (%dx%d)" % (key, w, h))

print("[7c] IP-source panel shows only the chosen source's settings")
app.select_page("run", animate=False)
for _ in range(8):
    app.update_idletasks()
    app.update()
dash = app.dashboard_page
try:
    dash.on_proxy_source_change("My proxies")
    app.update_idletasks()
    app.update()
    if dash.frame_proxy_note.winfo_ismapped() and not dash.frame_tor_controls.winfo_ismapped():
        ok("proxy mode: proxy panel shown, Tor panel hidden")
    else:
        fail("proxy mode: wrong panel visibility (proxy=%s tor=%s)"
             % (dash.frame_proxy_note.winfo_ismapped(),
                dash.frame_tor_controls.winfo_ismapped()))

    dash.on_proxy_source_change("Tor (free)")
    app.update_idletasks()
    app.update()
    if dash.frame_tor_controls.winfo_ismapped() and not dash.frame_proxy_note.winfo_ismapped():
        ok("Tor mode: Tor panel shown, proxy panel hidden")
    else:
        fail("Tor mode: wrong panel visibility (proxy=%s tor=%s)"
             % (dash.frame_proxy_note.winfo_ismapped(),
                dash.frame_tor_controls.winfo_ismapped()))
    dash.on_proxy_source_change("My proxies")
except Exception as exc:
    fail("IP-source mode switch raised: %s" % exc)

print("[9b] Tor missing must stop the campaign with ONE clear message")
try:
    captured: list[str] = []
    # log_safe() is what the worker paths use; it would normally schedule the
    # message on the Tk loop, so capture it directly here.
    original_log_safe = app.log_safe
    app.log_safe = lambda message: captured.append(str(message))  # type: ignore[assignment]

    # Force ports nothing listens on, and no auto-start, so the "Tor is not
    # usable" path is deterministic even on a machine that has Tor running.
    from core.tor_manager import TorSettings as _TorSettings

    app._tor_settings = lambda: _TorSettings(  # type: ignore[assignment]
        socks_port=1, control_port=1, auto_start=False
    )
    app.tor_manager = None

    result = app._ensure_tor()
    app.log_safe = original_log_safe  # type: ignore[assignment]

    _EXPLANATIONS = (
        "not installed yet",
        "Tor was not found",
        "Tor is not running",
        "already running",
    )
    warnings = [m for m in captured if any(k in m for k in _EXPLANATIONS)]
    aborts = [m for m in captured if "Campaign NOT started" in m]
    if result is not None:
        fail("_ensure_tor returned a manager although Tor is not running")
    else:
        ok("_ensure_tor refused to hand back a Tor manager when Tor is down")
    if len(warnings) == 1:
        ok("the 'Tor not installed' message is logged exactly once")
    else:
        fail("expected 1 Tor-missing message, got %d: %s" % (len(warnings), warnings))
    if aborts:
        ok("the log explains that the campaign was not started")
    else:
        fail("nothing told the user the campaign was refused")

    app.settings["tor_auto_start"] = True
except Exception as exc:
    fail("Tor-missing check raised: %s" % exc)

app.destroy()

print("[8] MR ADS branding + campaign-size controls")
print("    window title:", WINDOW_TITLE)
if WINDOW_TITLE != "MR ADS":
    fail("window title is %r, expected 'MR ADS'" % WINDOW_TITLE)
else:
    ok("window title is MR ADS")

page = app.dashboard_page
visits_menu = getattr(page, "menu_visits", None)
per_ip_menu = getattr(page, "menu_clicks_per_ip", None)
if visits_menu is None:
    fail("'Visits to take' selector is missing from the dashboard")
else:
    ok("'Visits to take' selector present: %s" % ", ".join(visits_menu.cget("values")))
if per_ip_menu is None:
    fail("'Clicks per IP' selector is missing from the dashboard")
else:
    ok("'Clicks per IP' selector present: %s" % ", ".join(per_ip_menu.cget("values")))

# the selectors must reach the settings the engine is configured from.
# Only the selection handlers are exercised here: the window has already been
# destroyed, so anything that talks to Tk itself would fail.
if visits_menu is not None:
    page.on_visits_change("500")
    if app.settings.get("total_visits") != 500:
        fail("visits selector did not store total_visits (got %r)"
             % app.settings.get("total_visits"))
    else:
        ok("choosing 500 visits stores total_visits=500")
    page.on_visits_change("Unlimited")
    if app.settings.get("total_visits") != 0:
        fail("'Unlimited' should store total_visits=0")
    else:
        ok("'Unlimited' stores total_visits=0 (run until stopped)")

btn_check = getattr(page, "btn_check_target", None)
if btn_check is None:
    fail("'CHECK TARGET' button is missing from the dashboard")
else:
    # cget reads the widget's stored option, so it still works after destroy
    ok("'CHECK TARGET' button present (text=%r)" % btn_check.cget("text"))
    if not hasattr(app, "check_target_health_clicked"):
        fail("app.check_target_health_clicked handler is missing")
    else:
        ok("target health check handler is wired to the button")

# amount box + progress readout
entry_amount = getattr(page, "entry_visits", None)
if entry_amount is None:
    fail("'or type amount' box is missing from the dashboard")
else:
    ok("amount box present (placeholder=%r)" % entry_amount.cget("placeholder_text"))

progress_label = getattr(page, "lbl_visit_progress", None)
if progress_label is None:
    fail("delivered-progress label is missing")
else:
    ok("delivered-progress label present: %r" % progress_label.cget("text"))

if per_ip_menu is not None:
    page.on_clicks_per_ip_change("20")
    if app.settings.get("clicks_per_ip") != 20:
        fail("clicks-per-IP selector did not store clicks_per_ip (got %r)"
             % app.settings.get("clicks_per_ip"))
    else:
        ok("choosing 20 clicks per IP stores clicks_per_ip=20")
    page.on_clicks_per_ip_change("Every click")
    if app.settings.get("clicks_per_ip") != 1:
        fail("'Every click' should store clicks_per_ip=1")
    else:
        ok("'Every click' stores clicks_per_ip=1 (rotate on every click)")

print("[9] Tor IP source panel")
tor_controls = {
    "source selector": "menu_proxy_source",
    "timer selector": "menu_rotate_seconds",
    "max-IPs selector": "menu_max_ips",
    "SOCKS port entry": "entry_tor_socks",
    "control port entry": "entry_tor_control",
    "GET TOR button": "btn_get_tor",
    "START TOR button": "btn_start_tor",
    "TEST/ROTATE button": "btn_test_tor",
}
for label, attribute in tor_controls.items():
    widget = getattr(page, attribute, None)
    if widget is None:
        fail("%s is missing from the dashboard" % label)
    else:
        ok("%s present" % label)

if getattr(page, "menu_proxy_source", None) is not None:
    ok("IP source choices: %s" % ", ".join(page.menu_proxy_source.cget("values")))
    page.on_proxy_source_change("Tor (free)")
    if app.settings.get("proxy_source") != "tor":
        fail("choosing Tor did not store proxy_source=tor")
    else:
        ok("choosing Tor stores proxy_source=tor")
    page.on_proxy_source_change("My proxies")
    if app.settings.get("proxy_source") != "list":
        fail("switching back did not store proxy_source=list")
    else:
        ok("switching back stores proxy_source=list")

if getattr(page, "menu_rotate_seconds", None) is not None:
    page.on_rotate_seconds_change("60s")
    if app.settings.get("rotate_interval_seconds") != 60:
        fail("timer selector did not store rotate_interval_seconds")
    else:
        ok("timer selector stores rotate_interval_seconds=60")
    page.on_rotate_seconds_change("Off")
    if app.settings.get("rotate_interval_seconds") != 0:
        fail("'Off' should store rotate_interval_seconds=0")
    else:
        ok("'Off' stores rotate_interval_seconds=0")

if getattr(page, "menu_max_ips", None) is not None:
    page.on_max_ips_change("25")
    if app.settings.get("max_ip_rotations") != 25:
        fail("max-IPs selector did not store max_ip_rotations")
    else:
        ok("max-IPs selector stores max_ip_rotations=25")

for handler in ("start_tor_clicked", "test_tor_clicked"):
    if not hasattr(app, handler):
        fail("app.%s is missing (Tor buttons would not work)" % handler)
    else:
        ok("app.%s is wired" % handler)

print("\n================ SUMMARY ================")
print("failures:", len(FAILURES))
for item in FAILURES:
    print("  -", item)
print("notes:", len(NOTES))
for item in NOTES:
    print("  -", item)
print("RESULT:", "ALL PASS" if not FAILURES else "FAILURES FOUND")
