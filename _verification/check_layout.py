import os, sys
from pathlib import Path
HERE = Path(r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL\_verification")
PROJECT = str(HERE.parent); os.chdir(PROJECT); sys.path.insert(0, PROJECT)
from ui.app import ModernTrafficBot
from PIL import ImageGrab

for geo in ("1158x616", "1360x730"):
    app = ModernTrafficBot()
    app.geometry(geo)
    for _ in range(8):
        app.update_idletasks(); app.update()
    page = app.dashboard_page
    parent = page._config_parent
    cfg = page._config_scroll
    for _ in range(6):
        parent.event_generate("<Configure>"); app.update_idletasks(); app.update()
    need = cfg.winfo_reqheight()
    row = parent.grid_rowconfigure(0)["minsize"]
    log_h = page.log_box.winfo_height() if page.log_box else -1
    print("%s  page=%4d  content needs=%4d  row given=%4d  log=%3d  scroll needed by %d px"
          % (geo, parent.winfo_height(), need, row, log_h, max(0, need - row)))
    app.update()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    img = ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()))
    out = PROJECT + "\\_layout_" + geo.replace("x", "x") + ".png"
    img.save(out)
    app.destroy()
print("screenshots saved")
