import os, sys
from pathlib import Path
HERE = Path(r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL\_verification")
PROJECT = str(HERE.parent); os.chdir(PROJECT); sys.path.insert(0, PROJECT)
from ui.app import ModernTrafficBot
app = ModernTrafficBot()
app.geometry("1360x730")
for _ in range(8):
    app.update_idletasks(); app.update()
page = app.dashboard_page
cfg = page._config_scroll
for child in cfg.winfo_children():
    try:
        print("%-28s req=%4d actual=%4d" % (child.__class__.__name__, child.winfo_reqheight(), child.winfo_height()))
    except Exception as e:
        print("child error", e)
print("inner total req:", cfg.winfo_reqheight())

