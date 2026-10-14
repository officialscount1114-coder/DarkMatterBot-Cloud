"""Is the 200-page a real anti-proxy wall, or a false positive?"""
import sys, json
sys.path.insert(0, r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL")
from core.constants import BLOCK_PAGE_MARKERS, is_tracker_url
from playwright.sync_api import sync_playwright

TARGET = "https://deshilink.my/HfAUamDL"

def probe(label, proxy=None):
    kw = {}
    if proxy:
        kw["proxy"] = {"server": proxy}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 768}, locale="en-US", **kw)
        page = ctx.new_page()
        reqs = []
        page.on("request", lambda r: reqs.append(r.url))
        status = None
        try:
            resp = page.goto(TARGET, wait_until="domcontentloaded", timeout=90000)
            status = resp.status if resp else None
        except Exception as exc:
            print("[%s] goto failed: %s" % (label, str(exc)[:110]))
        try:
            page.wait_for_timeout(8000)
        except Exception:
            pass
        try:
            content = page.content().lower()
        except Exception:
            content = ""
        print("--- %s ---" % label)
        print("  status :", status, " final:", page.url[:100])
        try:
            print("  title  :", (page.title() or "")[:90])
        except Exception:
            pass
        print("  html len:", len(content))
        hits = [reason for marker, reason in BLOCK_PAGE_MARKERS if marker in content]
        for marker, reason in BLOCK_PAGE_MARKERS:
            if marker in content:
                idx = content.find(marker)
                print("  MARKER HIT: %-22s -> %s" % (marker, reason))
                print("      context:", content[max(0, idx - 70):idx + 70].replace("\n", " "))
        if not hits:
            print("  no block markers found")
        beacons = [u for u in dict.fromkeys(reqs) if is_tracker_url(u)]
        print("  beacons:", len(beacons))
        b.close()

probe("DIRECT")
probe("TOR", "socks5://127.0.0.1:9050")
