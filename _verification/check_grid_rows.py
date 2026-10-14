"""Measure how many proxy rows are actually visible, per window/DPI setting.

Usage: check_grid_rows.py <widget_scale> <window_scale> [WxH] [min_rows]
"""
import os
import sys
import time

from pathlib import Path
_HERE = Path(__file__).resolve().parent
PROJECT = str(_HERE.parent)
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)

widget_scale = float(sys.argv[1])
window_scale = float(sys.argv[2])
geometry = sys.argv[3] if len(sys.argv) > 3 else None
if geometry in ("", "default", "-"):
    geometry = None
min_rows = int(sys.argv[4]) if len(sys.argv) > 4 else 6

import customtkinter as ctk  # noqa: E402

ctk.set_widget_scaling(widget_scale)
ctk.set_window_scaling(window_scale)
ctk.set_appearance_mode("dark")

from ui.app import ModernTrafficBot  # noqa: E402

app = ModernTrafficBot()
if geometry:
    app.geometry(geometry)

app.select_page("proxy", animate=False)


def settle(seconds=1.2):
    """Pump the event loop until the grid stops changing size."""
    deadline = time.time() + seconds
    last = None
    stable = 0
    while time.time() < deadline:
        app.update_idletasks()
        app.update()
        try:
            size = (app.proxy_grid.canvas.winfo_width(), app.proxy_grid.canvas.winfo_height())
        except Exception:
            size = None
        if size == last and size and size[0] > 10:
            stable += 1
            if stable > 6:
                break
        else:
            stable = 0
        last = size
        time.sleep(0.02)


settle()

grid = app.proxy_grid
canvas_h = grid.canvas.winfo_height()
row_h = grid.row_h
rows = canvas_h // row_h if row_h else 0
label = "widget=%.2f window=%.2f geom=%s" % (widget_scale, window_scale, geometry or "default")

print("%s" % label)
print("  window=%dx%d page=%dx%d" % (
    app.winfo_width(), app.winfo_height(),
    app.pages["proxy"].winfo_width(), app.pages["proxy"].winfo_height()))
print("  tools_scroll h=%d  grid=%dx%d  canvas_h=%d  row_h=%d  visible_rows=%d" % (
    app.proxy_manager_page._tools_scroll.winfo_height(),
    grid.winfo_width(), grid.winfo_height(), canvas_h, row_h, rows))

problems = []
if not grid.winfo_ismapped():
    problems.append("grid not mapped")
if rows < min_rows:
    problems.append("only %d visible rows (want >= %d)" % (rows, min_rows))
if app.proxy_manager_page._tools_scroll.winfo_height() < 40:
    problems.append("tool cards collapsed to %dpx" % app.proxy_manager_page._tools_scroll.winfo_height())

# nothing else on the page may have vanished
for key in app.pages:
    app.select_page(key, animate=False)
    settle(0.8)
    frame = app.pages[key]
    if frame.winfo_width() <= 1 or frame.winfo_height() <= 1:
        problems.append("page %s collapsed" % key)

print("  PROBLEMS: %s" % (problems if problems else "none"))
app.destroy()
sys.exit(1 if problems else 0)
