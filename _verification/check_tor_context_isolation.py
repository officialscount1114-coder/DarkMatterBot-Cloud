"""Can each browser context leave Tor by its own exit?

Tor isolates circuits per SOCKS username, and curl proved it (three usernames,
three different exits). If Chromium can carry that username too, the campaign can
run several visits at once - each from its own IP - which is exactly "one counted
click per IP" without waiting for one visit at a time.

Run with the interpreter that has the project's dependencies (Python 3.13).
"""
import sys

from playwright.sync_api import sync_playwright

SERVER = "socks5://127.0.0.1:9050"


def probe(username=None):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        proxy = {"server": SERVER}
        if username:
            proxy["username"] = username
            proxy["password"] = "x"
        context = browser.new_context(proxy=proxy)
        page = context.new_page()
        try:
            page.goto("https://api.ipify.org?format=json", timeout=45000)
            text = page.inner_text("body")
        except Exception as exc:
            text = "FAILED: " + str(exc)[:90]
        browser.close()
    return text


for name in (None, "ctx1", "ctx2", "ctx3"):
    print(f"{str(name):>5} -> {probe(name)}", flush=True)
