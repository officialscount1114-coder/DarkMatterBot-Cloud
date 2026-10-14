import os, sys
from pathlib import Path
HERE = Path(r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL\_verification")
PROJECT = str(HERE.parent); os.chdir(PROJECT); sys.path.insert(0, PROJECT)
from ui.app import ModernTrafficBot
app = ModernTrafficBot(); app.geometry("1360x730")
for _ in range(8):
    app.update_idletasks(); app.update()
page = app.dashboard_page
cfg = page._config_scroll
card = cfg.winfo_children()[1]
print("config card req:", card.winfo_reqheight())
for i, child in enumerate(card.winfo_children()):
    txt = ""
    try:
        txt = (child.cget("text") or "")[:44]
    except Exception:
        pass
    print("  %2d %-14s req=%4d  %s" % (i, child.__class__.__name__,
                                       child.winfo_reqheight(), txt))
app.destroy()
