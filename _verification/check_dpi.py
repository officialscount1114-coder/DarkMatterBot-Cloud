"""Live DPI check: build the app at 150% scaling and measure the proxy grid.

This display reports 100% DPI, so the scaling fix is exercised by forcing
customtkinter's widget scaling to 1.5 before the app is built - the same thing a
high-DPI screen does. Before the fix ui.scaling reported 1.0 in every case, so
the canvas rows/fonts stayed at base size while CTk scaled the chrome around
them (the "fragmented" look).
"""
import os
import sys
import time
import traceback

from pathlib import Path
_HERE = Path(__file__).resolve().parent
PROJECT = str(_HERE.parent)
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)

import customtkinter as ctk  # noqa: E402

TARGET = 1.5
ctk.set_widget_scaling(TARGET)
ctk.set_appearance_mode("dark")

from ui.app import ModernTrafficBot  # noqa: E402
from ui.scaling import get_scaling_factor  # noqa: E402

FAILURES = []


def check(label, good, detail=""):
    print(("  PASS  " if good else "  FAIL  ") + label + ((" -- " + detail) if detail else ""))
    if not good:
        FAILURES.append(label)


print("building the app at %.0f%% widget scaling" % (TARGET * 100))
try:
    app = ModernTrafficBot()
except Exception:
    print(traceback.format_exc())
    check("app builds at %.0f%% scaling" % (TARGET * 100), False)
    raise SystemExit(1)

for _ in range(8):
    app.update_idletasks()
    app.update()

print("  window %dx%d" % (app.winfo_width(), app.winfo_height()))

ctk_factor = ctk.ScalingTracker.get_widget_scaling(app)
comp_factor = get_scaling_factor()
print("  ctk factor=%.3f  ui.scaling factor=%.3f" % (ctk_factor, comp_factor))

check("ui.scaling reports the real factor (not a hard 1.0)",
      abs(comp_factor - TARGET) < 0.001,
      "expected %.2f, got %.3f" % (TARGET, comp_factor))

check("ui.scaling factor equals customtkinter's own factor",
      abs(comp_factor - ctk_factor) < 0.001)

grid = app.proxy_grid
expected_row_h = int(grid._base_row_h * ctk_factor)
check("proxy grid row height scales with DPI",
      grid.row_h == expected_row_h,
      "row_h=%d expected=%d (base %d)" % (grid.row_h, expected_row_h, grid._base_row_h))

# header columns must still line up with the canvas columns they label.
# Wait for the grid to be laid out - a canvas that is still 1px wide would make
# the drift comparison pass trivially and prove nothing.
app.select_page("proxy", animate=False)
width = 1
for _ in range(200):
    app.update_idletasks()
    app.update()
    width = grid.canvas.winfo_width()
    if width > 10:
        break
    time.sleep(0.02)

print("  canvas width=%d headers=%d grid width=%d" % (width, len(grid.header_btns), grid.winfo_width()))
if width <= 10:
    check("proxy grid is laid out so columns can be compared", False,
          "canvas stayed %dpx wide - check inconclusive" % width)
else:
    positions = grid._get_col_positions(width)
    worst = 0.0
    detail = []
    for index, btn in enumerate(grid.header_btns):
        drift = abs(btn.winfo_x() - positions[index])
        worst = max(worst, drift)
        detail.append("%d:%d/%d" % (index, btn.winfo_x(), positions[index]))
    print("  header x / expected x -> " + " ".join(detail))
    check("column headers align at 150%% (<= 3px drift)", worst <= 3.0,
          "worst drift %.1f px" % worst)

    mis_sized = [
        (i, btn.winfo_width(), positions[i + 1] - positions[i])
        for i, btn in enumerate(grid.header_btns)
        if abs(btn.winfo_width() - (positions[i + 1] - positions[i])) > 4
    ]
    check("header cells are the same width as their canvas columns",
          not mis_sized,
          "mismatched (index, header_w, column_w): %s" % mis_sized)

# the grid must not push past the window
check("grid fits inside its container",
      grid.winfo_width() <= grid.master.winfo_width() + 1,
      "grid=%d container=%d" % (grid.winfo_width(), grid.master.winfo_width()))

# every page must still lay out
for key in app.pages:
    app.select_page(key, animate=False)
    for _ in range(4):
        app.update_idletasks()
        app.update()
    frame = app.pages[key]
    check("page %r lays out at 150%%" % key,
          frame.winfo_width() > 1 and frame.winfo_height() > 1,
          "%dx%d" % (frame.winfo_width(), frame.winfo_height()))

app.destroy()
print("\nRESULT:", "ALL PASS" if not FAILURES else ("FAILURES: %s" % FAILURES))
