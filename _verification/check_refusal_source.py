"""Who serves the refusal page - the shortener or the offer behind it?

If the refusal comes from the offer, the shortener has already counted the click,
so the visit is not wasted. If the shortener itself refuses, the click never
reaches the counter. The final URL and the page text tell the two apart.

Run with the interpreter that has the project's dependencies (Python 3.13).
"""
import sys
from urllib.parse import urlparse

sys.path.insert(0, r"C:\Users\pc\Documents\ALL SOFTWARE\DarkMatterBot-FULL")

from core.constants import block_page_reason, is_tracker_url     # noqa: E402
from playwright.sync_api import sync_playwright                   # noqa: E402

TARGET = sys.argv[1] if len(sys.argv) > 1 else "https://deshilink.my/VmTZnigp"

SKIP = {"media", "font", "image", "imageset"}


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            proxy={"server": "socks5://127.0.0.1:9050"},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )

        def handler(route):
            try:
                if is_tracker_url(route.request.url):
                    route.continue_()
                    return
                if route.request.resource_type in SKIP:
                    route.abort()
                    return
            except Exception:
                pass
            try:
                route.continue_()
            except Exception:
                pass

        context.route("**/*", handler)
        page = context.new_page()
        requests = []
        page.on("request", lambda r: requests.append(r.url))

        status = None
        try:
            response = page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)
            status = response.status if response else None
        except Exception as exc:
            print("NAVIGATION FAILED:", str(exc)[:140])
            browser.close()
            return

        page.wait_for_timeout(5000)
        final = page.url
        try:
            visible = page.evaluate(
                "() => (document.body ? document.body.innerText : '') || ''"
            )
        except Exception:
            visible = ""
        reason = block_page_reason(visible, page.title())

        print("status      :", status)
        print("start host  :", urlparse(TARGET).netloc)
        print("final url   :", final[:120])
        print("final host  :", urlparse(final).netloc)
        print("redirected  :", urlparse(final).netloc != urlparse(TARGET).netloc)
        print("title       :", (page.title() or "")[:80])
        print("refusal     :", reason)
        print("beacons     :", len({u for u in requests if is_tracker_url(u)}))
        print("visible text:", " ".join((visible or "").split())[:220])

        hosts = {}
        for url in requests:
            host = urlparse(url).netloc
            hosts[host] = hosts.get(host, 0) + 1
        print("top hosts   :", sorted(hosts.items(), key=lambda kv: -kv[1])[:6])
        browser.close()


if __name__ == "__main__":
    main()
