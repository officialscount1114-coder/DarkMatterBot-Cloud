"""Build the app and save a PNG of each page, to eyeball the look.

The app builds a real window; PrintWindow asks it to draw itself into a bitmap,
so the capture works even when the window is behind others.

Usage: shot_pages.py <out_dir> [page ...]
"""
import ctypes
import os
import sys
import time
from pathlib import Path

PROJECT = str(Path(__file__).resolve().parent.parent)
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)

out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(PROJECT) / "_shots"
out_dir.mkdir(parents=True, exist_ok=True)
pages = sys.argv[2:] or ["run", "proxy", "master", "settings"]

import customtkinter as ctk  # noqa: E402
from ui.app import ModernTrafficBot  # noqa: E402


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def capture(hwnd: int, path: Path) -> bool:
    """Grab one window with PrintWindow and save it as a PNG."""
    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    rect = ctypes.wintypes.RECT() if hasattr(ctypes, "wintypes") else None
    from ctypes import wintypes

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return False

    window_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)

    # 2 == PW_RENDERFULLCONTENT
    ok = user32.PrintWindow(hwnd, mem_dc, 2)

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # top-down
    header.biPlanes = 1
    header.biBitCount = 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)

    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
    image.convert("RGB").save(path)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, window_dc)
    return bool(ok)


def main() -> int:
    ctk.set_appearance_mode("dark")
    app = ModernTrafficBot()
    app.update_idletasks()
    app.update()
    time.sleep(0.6)

    hwnd = int(app.winfo_id())
    # CTk wraps the real toplevel; walk up to the window that owns the frame
    parent = ctypes.windll.user32.GetParent(hwnd)
    if parent:
        hwnd = parent

    print("window handle:", hwnd, "| title:", app.title())
    for page in pages:
        app.select_page(page, animate=False)
        for _ in range(12):
            app.update_idletasks()
            app.update()
        time.sleep(0.35)
        target = out_dir / ("%s.png" % page)
        ok = capture(hwnd, target)
        print("  %-9s -> %s (PrintWindow=%s, %d bytes)"
              % (page, target.name, ok, target.stat().st_size if target.exists() else 0))

    app.destroy()
    print("saved into", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
