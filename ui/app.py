import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import threading
import time
from queue import Empty, Queue
from typing import Any
from tkinter import filedialog, messagebox

import customtkinter as ctk
import requests

# Configure customtkinter for proper scaling
ctk.set_appearance_mode("dark")
# ui/theme.json mirrors the palette in ui/styles.py, so widgets that do not pass
# their own fg_color/text_color (a good part of the older pages) still match the
# new theme instead of falling back to customtkinter's stock dark-blue.
try:
    ctk.set_default_color_theme(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "theme.json")
    )
except Exception:  # a broken/missing theme file must not stop the app
    ctk.set_default_color_theme("dark-blue")

from core.config_builder import TrafficConfigBuilder  # noqa: E402
from core.engine import AsyncTrafficEngine  # noqa: E402
from core.engine_factory import EngineFactory  # noqa: E402
from core.models import (  # noqa: E402
    EngineMode,
    ProxyCheckResult,
    ProxyConfig,
    TrafficStats,
)
from core.proxy_manager import ThreadedProxyManager  # noqa: E402
from core.session_manager import SessionManager  # noqa: E402
from core.settings_keys import Defaults, SettingsKeys  # noqa: E402
from core.tor_manager import exit_ip_country, exit_ip_geo  # noqa: E402
from core.validators import DEFAULT_VALIDATORS, pick_working_target  # noqa: E402

from .pages import (  # noqa: E402
    DashboardPage,
    MasterControlPage,
    ProxyManagerPage,
    SettingsPage,
    StressTestPage,
)
from .scaling import scaled  # noqa: E402

#: Tried in order when a test URL cannot be reached directly. httpbin.org (the
#: old default) is frequently down, and when the target is down every proxy is
#: reported dead - which looks exactly like "the test does nothing".
DEFAULT_TEST_TARGETS = [
    "https://api.ipify.org",
    "https://www.cloudflare.com/cdn-cgi/trace",
    "https://www.google.com/generate_204",
    "http://httpbin.org/get",
]
from .styles import COLORS, MOTION, RADIUS, ease_out, font, mix  # noqa: E402
from .utils import Utils  # noqa: E402


class ModernTrafficBot(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.settings = Utils.load_settings()
        self.title("MR ADS")
        # Open as large as the screen allows: the dashboard carries a lot of
        # controls, and on a short screen a fixed 780-high window cut the lower
        # cards in half. The work area is used so the taskbar is never covered.
        try:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            width = max(1160, min(screen_w - 40, 1500))
            height = max(700, min(screen_h - 80, 1000))
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 3)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            self.geometry("1160x780")
        self.minsize(860, 560)  # Minimum window size for usability
        self.configure(fg_color=COLORS["bg"])
        self._set_window_icon()

        self.testing = False
        self.testing_paused = False
        self.running = False
        self.proxies = []
        self.checked_proxies = []  # Stores checked proxy results for persistence
        self.active_proxy_count = 0
        self.buffer = Queue()  # Thread-safe queue for proxy results
        self.stats = {"req": 0, "success": 0, "fail": 0}
        self.engine: AsyncTrafficEngine = None
        self.engine_thread: threading.Thread = None
        # Last result of the "Check target" probe (see check_target_health_clicked)
        self._last_target_health = None
        # Page entrance slide: opt-in only. Animating page geometry re-lays out
        # the whole page every frame and made heavy pages look torn mid-switch.
        self._page_slide_enabled = bool(
            self.settings.get("page_slide_animation", False)
        )
        # Tor manager, created on demand when the Tor IP source is used
        self.tor_manager = None
        self._tor_busy = False

        # Stress test state
        self.stress_running = False
        self.stress_paused = False
        self.stress_engine = None
        self.stress_thread: threading.Thread = None
        self.stress_stats = {
            "requests": 0,
            "success": 0,
            "failed": 0,
            "rps": 0.0,
            "latency": 0.0,
            "proxies_used": 0,
        }

        try:
            self.real_ip = requests.get("https://api.ipify.org", timeout=2).text
        except (requests.RequestException, OSError):
            self.real_ip = "0.0.0.0"

        # country filter bookkeeping (the picker only rebuilds when this changes)
        self._country_sig = None
        self._proxy_key_cache = None   # host:port dedupe cache for bulk imports
        self._pages_ready = False   # guards save_cfg while the pages are built
        self._active_page = None
        self._nav_indicator_job = None
        self._page_anim_job = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.setup_sidebar()
        self._setup_content_host()
        self.setup_pages()
        self.select_page("run", animate=False)
        self.load_saved_proxies()  # Restore proxies from last session
        self._check_runtime_deps()  # say up-front if a package is missing
        self.update_gui_loop()

    # ========== WINDOW CHROME ==========

    def _check_runtime_deps(self):
        """Log any missing runtime package at startup.

        A missing module is the difference between "the app opens" and "testing
        silently does nothing", so it is reported in the Activity Log right away.
        """
        import importlib.util

        required = [
            ("customtkinter", "GUI framework"),
            ("curl_cffi", "HTTP engine used for proxy checking"),
            ("PIL", "image support - comes with customtkinter (pillow)"),
            ("darkdetect", "dark mode detection - comes with customtkinter"),
            ("requests", "HTTP client for the dashboard"),
            ("psutil", "system metrics for Master Control"),
            ("geoip2", "country lookup"),
            ("maxminddb", "reads the GeoLite2 database"),
            ("websockets", "stress test WebSocket mode"),
            ("aiohttp", "async HTTP for the stress test"),
            ("fake_useragent", "user-agent rotation"),
        ]
        missing = []
        for name, why in required:
            try:
                if importlib.util.find_spec(name) is None:
                    missing.append(f"{name} ({why})")
            except Exception:
                missing.append(f"{name} ({why})")
        if missing:
            self.log("Missing packages - install them with:")
            self.log("    python -m pip install -r requirements.txt")
            for item in missing:
                self.log(f"  - {item}")
        return missing

    def _set_window_icon(self):
        """Set the taskbar/window icon on every platform.

        ``iconbitmap`` only understands .ico and only on Windows/X11-with-imlib;
        on Linux it raises TclError on this file, so the PNG is used there via
        Pillow, and a failure of both is ignored (the icon is cosmetic).
        """
        resources = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "resources"
        )
        ico = os.path.join(resources, "favicon.ico")
        png = os.path.join(resources, "favicon.png")

        if sys.platform.startswith("win"):
            try:
                self.iconbitmap(ico)
                return
            except Exception:
                pass
        try:
            from PIL import Image, ImageTk

            image = Image.open(png)
            image.thumbnail((64, 64))
            self._icon_image = ImageTk.PhotoImage(image)
            self.iconphoto(True, self._icon_image)
            return
        except Exception:
            pass
        try:
            self.iconbitmap(ico)
        except Exception:
            pass

    def _setup_content_host(self):
        """Right-hand column: a header strip over the page host."""
        self.content = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)

        self.header = ctk.CTkFrame(
            self.content,
            fg_color=COLORS["nav"],
            corner_radius=0,
            height=scaled(62),
            border_width=0,
        )
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.grid_propagate(False)
        self.header.grid_columnconfigure(1, weight=1)

        # hairline under the header
        self._header_line = ctk.CTkFrame(
            self.content, height=1, fg_color=COLORS["border"], corner_radius=0
        )
        self._header_line.grid(row=0, column=0, sticky="sew")

        left = ctk.CTkFrame(self.header, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=(18, 10), pady=10)

        self.lbl_page_title = ctk.CTkLabel(
            left, text="Dashboard", font=font(scaled(17), "bold"), text_color=COLORS["text"]
        )
        self.lbl_page_title.pack(anchor="w")

        self.lbl_page_sub = ctk.CTkLabel(
            left, text="", font=font(scaled(10)), text_color=COLORS["text_mute"]
        )
        self.lbl_page_sub.pack(anchor="w")

        right = ctk.CTkFrame(self.header, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e", padx=(10, 18))

        self.dot = ctk.CTkFrame(
            right, width=8, height=8, corner_radius=99, fg_color=COLORS["text_mute"]
        )
        self.dot.pack(side="left", padx=(0, 6))

        self.lbl_header_status = ctk.CTkLabel(
            right,
            text="Idle",
            font=font(scaled(11)),
            text_color=COLORS["text_dim"],
        )
        self.lbl_header_status.pack(side="left")

        self.lbl_header_meta = ctk.CTkLabel(
            right,
            text="",
            font=font(scaled(10), "bold"),
            text_color=COLORS["accent_glow"],
        )
        self.lbl_header_meta.pack(side="left", padx=(12, 0))

        self.page_host = ctk.CTkFrame(self.content, fg_color="transparent")
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)

        # Save proxies on window close
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    # Labels are deliberately glyph-free: emoji need a colour-emoji font that
    # plain X11/Tk boxes do not have, and they would render as empty boxes there.
    NAV_ITEMS = [
        ("run", "Dashboard"),
        ("proxy", "Proxy Manager"),
        ("stress", "Stress Test"),
        ("master", "Master Control"),
        ("settings", "Settings"),
    ]

    PAGE_META = {
        "run": ("Dashboard", "Campaign control and live traffic statistics"),
        "proxy": ("Proxy Manager", "Scrape, test and filter proxies by country"),
        "stress": ("Stress Test", "Authorized load testing with your own proxy list"),
        "master": ("Master Control", "Master/slave relay and remote workers"),
        "settings": ("Settings", "Engines, browser, captcha and validators"),
    }

    def setup_sidebar(self):
        self.sidebar = ctk.CTkFrame(
            self, width=scaled(198), corner_radius=0, fg_color=COLORS["nav"]
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(len(self.NAV_ITEMS) + 1, weight=1)
        self.sidebar.grid_propagate(False)  # Maintain width

        # --- brand block -------------------------------------------------
        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=scaled(14), pady=(scaled(16), scaled(14)))

        tile = ctk.CTkFrame(
            brand,
            width=scaled(34),
            height=scaled(34),
            corner_radius=RADIUS["md"],
            fg_color=COLORS["accent"],
        )
        tile.pack(side="left")
        tile.pack_propagate(False)
        ctk.CTkLabel(
            tile, text="MR", font=font(scaled(13), "bold"), text_color="#FFFFFF"
        ).place(relx=0.5, rely=0.5, anchor="center")

        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=(scaled(10), 0))
        ctk.CTkLabel(
            names, text="MR ADS", font=font(scaled(14), "bold"), text_color=COLORS["text"]
        ).pack(anchor="w")
        ctk.CTkLabel(
            names,
            text="TRAFFIC SUITE · v3.8.0",
            font=font(scaled(8)),
            text_color=COLORS["accent_2"],
        ).pack(anchor="w")

        ctk.CTkFrame(
            self.sidebar, height=1, fg_color=COLORS["border_soft"], corner_radius=0
        ).grid(row=0, column=0, sticky="sew", padx=scaled(12))

        # --- nav items ---------------------------------------------------
        self.nav_btns = {}
        self._nav_hover_jobs = {}
        for i, (key, text) in enumerate(self.NAV_ITEMS):
            btn = ctk.CTkButton(
                self.sidebar,
                text=f"      {text}",
                fg_color="transparent",
                text_color=COLORS["text_dim"],
                anchor="w",
                hover=False,  # hover is animated by hand (see _nav_enter / _nav_leave)
                height=scaled(40),
                corner_radius=RADIUS["pill"],
                font=font(scaled(12)),
                command=lambda k=key: self.select_page(k),
            )
            btn.grid(row=i + 1, column=0, sticky="ew", padx=scaled(10), pady=scaled(3))
            btn.bind("<Enter>", lambda e, k=key: self._nav_enter(k))
            btn.bind("<Leave>", lambda e, k=key: self._nav_leave(k))
            self.nav_btns[key] = btn

        # sliding accent indicator, placed over the sidebar and animated on switch
        self.nav_indicator = ctk.CTkFrame(
            self.sidebar, fg_color=COLORS["accent_2"], width=3, corner_radius=2
        )

        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        footer.grid(row=len(self.NAV_ITEMS) + 2, column=0, sticky="ew", pady=(0, scaled(12)))
        self.lbl_side_active = ctk.CTkLabel(
            footer,
            text="0 active proxies",
            font=font(scaled(9)),
            text_color=COLORS["text_mute"],
        )
        self.lbl_side_active.pack()

    # ---------- nav hover (animated fill, shared by every item) ----------
    def _nav_enter(self, key):
        self._nav_animate(key, 1.0)

    def _nav_leave(self, key):
        # the active page keeps its wash; the others fade back to transparent
        self._nav_animate(key, 1.0 if self._active_page == key else 0.0)

    def _nav_animate(self, key, target):
        """Ease a nav item between transparent and the active wash."""
        job = self._nav_hover_jobs.pop(key, None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        btn = self.nav_btns.get(key)
        if btn is None:
            return
        state = {"t": getattr(btn, "_hover_t", 0.0)}

        def step():
            self._nav_hover_jobs.pop(key, None)
            try:
                if not btn.winfo_exists():
                    return
            except Exception:
                return
            delta = target - state["t"]
            state["t"] = target if abs(delta) < 0.06 else state["t"] + delta * 0.34
            btn._hover_t = state["t"]
            try:
                btn.configure(
                    fg_color=mix(COLORS["nav"], COLORS["surface"], state["t"]),
                    text_color=mix(COLORS["text_dim"], COLORS["text"], state["t"]),
                )
            except Exception:
                return
            if state["t"] != target:
                self._nav_hover_jobs[key] = self.after(MOTION["frame_ms"], step)

        step()

    def _move_nav_indicator(self, key, animate=True):
        """Slide the accent bar to the active nav item."""
        btn = self.nav_btns.get(key)
        if btn is None:
            return
        try:
            self.sidebar.update_idletasks()
            target_y = btn.winfo_y() + scaled(5)
            target_h = max(scaled(12), btn.winfo_height() - scaled(10))
        except Exception:
            return

        if self._nav_indicator_job is not None:
            try:
                self.after_cancel(self._nav_indicator_job)
            except Exception:
                pass
            self._nav_indicator_job = None

        # CTk widgets take size from the constructor/configure, never from place()
        try:
            self.nav_indicator.configure(width=3, height=target_h)
            self.nav_indicator.place(x=0, y=target_y)
            self.nav_indicator.lift()
        except Exception:
            return
        if not animate:
            return

        # grow in from the middle, then settle
        steps = max(1, MOTION["base"] // MOTION["frame_ms"])
        state = {"i": 0}

        def step():
            self._nav_indicator_job = None
            state["i"] += 1
            t = ease_out(state["i"] / steps)
            try:
                self.nav_indicator.configure(height=int(target_h * (0.5 + 0.5 * t)))
                self.nav_indicator.place(x=0, y=int(target_y + (1 - t) * 6))
            except Exception:
                return
            if state["i"] < steps:
                self._nav_indicator_job = self.after(MOTION["frame_ms"], step)

        step()

    def setup_pages(self):
        self.pages = {}

        # Initialize page objects
        self.dashboard_page = DashboardPage(self)
        self.proxy_manager_page = ProxyManagerPage(self)
        self.stress_test_page = StressTestPage(self)
        self.master_control_page = MasterControlPage(self)
        self.settings_page = SettingsPage(self)

        # Create page frames (inside the page host) and setup UI
        self.pages["run"] = ctk.CTkFrame(self.page_host, fg_color="transparent")
        self.dashboard_page.setup(self.pages["run"])
        self._bind_dashboard_widgets()

        self.pages["proxy"] = ctk.CTkFrame(self.page_host, fg_color="transparent")
        self.proxy_manager_page.setup(self.pages["proxy"])
        self._bind_proxy_manager_widgets()
        self.toggle_manual_target()  # Initialize manual target toggle state

        self.pages["stress"] = ctk.CTkFrame(self.page_host, fg_color="transparent")
        self.stress_test_page.setup(self.pages["stress"])
        self._bind_stress_test_widgets()

        self.pages["master"] = ctk.CTkFrame(self.page_host, fg_color="transparent")
        self.master_control_page.setup(self.pages["master"])

        self.pages["settings"] = ctk.CTkFrame(self.page_host, fg_color="transparent")
        self.settings_page.setup(self.pages["settings"])
        self._bind_settings_widgets()

        # every widget save_cfg() reads now exists
        self._pages_ready = True

    def _bind_dashboard_widgets(self):
        """Bind dashboard page widgets to self for backward compatibility."""
        p = self.dashboard_page
        self.lbl_stats = p.lbl_stats
        self.entry_url = p.entry_url
        self.mode_selector = p.mode_selector
        self.slider_threads = p.slider_threads
        self.slider_view_min = p.slider_view_min
        self.slider_view_max = p.slider_view_max
        self.slider_burst_size = p.slider_burst_size
        self.slider_burst_sleep = p.slider_burst_sleep
        self.lbl_threads = p.lbl_threads
        self.lbl_viewtime = p.lbl_viewtime
        self.lbl_burst = p.lbl_burst
        self.chk_burst_mode = p.chk_burst_mode
        self.btn_attack = p.btn_attack
        self.browser_stats_frame = p.browser_stats_frame
        self.lbl_browser_type = p.lbl_browser_type
        self.lbl_browser_contexts = p.lbl_browser_contexts
        self.lbl_captcha_stats = p.lbl_captcha_stats
        self.lbl_balance_2captcha = p.lbl_balance_2captcha
        self.lbl_balance_anticaptcha = p.lbl_balance_anticaptcha
        self.lbl_protection_stats = p.lbl_protection_stats
        self.lbl_protection_event = p.lbl_protection_event
        self.log_box = p.log_box

    def _bind_proxy_manager_widgets(self):
        """Bind proxy manager page widgets to self for backward compatibility."""
        p = self.proxy_manager_page
        self.proxy_grid = p.proxy_grid
        self.progress_bar = p.progress_bar
        self.lbl_loaded = p.lbl_loaded
        self.lbl_proto_counts = p.lbl_proto_counts
        self.lbl_bandwidth = p.lbl_bandwidth
        self.lbl_anon_counts = p.lbl_anon_counts
        self.entry_test_url = p.entry_test_url
        self.entry_timeout = p.entry_timeout
        self.entry_check_threads = p.entry_check_threads
        self.entry_scrape_threads = p.entry_scrape_threads
        self.entry_system_proxy = p.entry_system_proxy
        self.combo_system_proxy_proto = p.combo_system_proxy_proto
        self.chk_http = p.chk_http
        self.chk_socks4 = p.chk_socks4
        self.chk_socks5 = p.chk_socks5
        self.chk_hide_dead = p.chk_hide_dead
        self.chk_manual_target = p.chk_manual_target
        self.chk_proxy_for_scrape = p.chk_proxy_for_scrape
        self.chk_proxy_for_check = p.chk_proxy_for_check
        self.chk_export_protocol = p.chk_export_protocol
        self.lbl_test_status = p.lbl_test_status
        self.btn_test = p.btn_test
        self.btn_pause = p.btn_pause
        self.btn_test_proxy = p.btn_test_proxy

    def _bind_stress_test_widgets(self):
        """Bind stress test page widgets to self for backward compatibility."""
        p = self.stress_test_page
        self.stress_stat_labels = p.stress_stat_labels
        self.stress_entry_url = p.stress_entry_url
        self.stress_attack_type = p.stress_attack_type
        self.stress_method = p.stress_method
        self.stress_proxy_count_label = p.stress_proxy_count_label
        self.stress_slider_threads = p.stress_slider_threads
        self.stress_slider_duration = p.stress_slider_duration
        self.stress_slider_rps = p.stress_slider_rps
        self.stress_lbl_threads = p.stress_lbl_threads
        self.stress_lbl_duration = p.stress_lbl_duration
        self.stress_lbl_rps = p.stress_lbl_rps
        self.btn_stress_start = p.btn_stress_start
        self.btn_stress_pause = p.btn_stress_pause
        self.btn_stress_reset = p.btn_stress_reset
        self.stress_log_box = p.stress_log_box

    def _bind_settings_widgets(self):
        # build card
        self.chk_block_direct = self.settings_page.chk_block_direct
        self.btn_build = self.settings_page.btn_build
        self.lbl_build_status = self.settings_page.lbl_build_status
        self.chk_onefile = self.settings_page.chk_onefile

        """Bind settings page widgets to self for backward compatibility."""
        p = self.settings_page
        self.chk_headless = p.chk_headless
        self.chk_verify_ssl = p.chk_verify_ssl
        self.chk_session_persist = p.chk_session_persist
        self.combo_browser = p.combo_browser
        self.btn_browser_expand = p.btn_browser_expand
        self.browser_paths_frame = p.browser_paths_frame
        self.browser_paths_visible = p.browser_paths_visible
        self.entry_chrome_path = p.entry_chrome_path
        self.entry_chromium_path = p.entry_chromium_path
        self.entry_edge_path = p.entry_edge_path
        self.entry_brave_path = p.entry_brave_path
        self.entry_firefox_path = p.entry_firefox_path
        self.entry_other_path = p.entry_other_path
        self.slider_contexts = p.slider_contexts
        self.lbl_contexts = p.lbl_contexts
        self.chk_stealth = p.chk_stealth
        self.combo_captcha_primary = p.combo_captcha_primary
        self.chk_captcha_fallback = p.chk_captcha_fallback
        self.entry_2captcha_key = p.entry_2captcha_key
        self.entry_anticaptcha_key = p.entry_anticaptcha_key
        self.lbl_captcha_balance = p.lbl_captcha_balance
        self.chk_cloudflare = p.chk_cloudflare
        self.chk_akamai = p.chk_akamai
        self.chk_auto_captcha = p.chk_auto_captcha
        self.entry_cf_wait = p.entry_cf_wait
        self.combo_test_depth = p.combo_test_depth
        self.validator_checkboxes = p.validator_checkboxes

    def select_page(self, key, animate=True):
        """Show one page: slide it in and move the accent indicator to its nav item."""
        if key not in self.pages:
            return
        self._active_page = key

        for name, page in self.pages.items():
            if name != key:
                page.grid_forget()

        for name, btn in self.nav_btns.items():
            active = name == key
            btn._hover_t = 1.0 if active else 0.0
            btn.configure(
                fg_color=COLORS["surface"] if active else "transparent",
                text_color=COLORS["text"] if active else COLORS["text_dim"],
            )

        title, subtitle = self.PAGE_META.get(key, (key.title(), ""))
        self.lbl_page_title.configure(text=title)
        self.lbl_page_sub.configure(text=subtitle)

        page = self.pages[key]
        page.grid(row=0, column=0, sticky="nsew", padx=20, pady=18)
        page.lift()
        self._move_nav_indicator(key, animate=animate)
        # No page-geometry animation by default (see _slide_in): laying the page
        # out on every frame is what made switching pages look broken.
        self._slide_in(page)
        if key == "proxy":
            self.refresh_country_options()

    def _slide_in(self, page):
        """Optional page entrance animation - OFF by default.

        Animating the page by changing its padding re-lays out every widget on
        every frame. On a page as heavy as Proxy Manager (cards + canvas grid)
        that made the page appear half-drawn and torn while sliding in, so the
        slide only runs when it is explicitly switched on in the settings.
        """
        if not getattr(self, "_page_slide_enabled", False):
            return

        if self._page_anim_job is not None:
            try:
                self.after_cancel(self._page_anim_job)
            except Exception:
                pass
            self._page_anim_job = None

        steps = max(1, MOTION["base"] // MOTION["frame_ms"])
        state = {"i": 0}

        def step():
            self._page_anim_job = None
            state["i"] += 1
            t = ease_out(state["i"] / steps)
            try:
                if not page.winfo_exists():
                    return
                page.grid_configure(
                    padx=int(20 + (1 - t) * 26), pady=int(18 + (1 - t) * 12)
                )
            except Exception:
                return
            if state["i"] < steps:
                self._page_anim_job = self.after(MOTION["frame_ms"], step)
            else:
                try:
                    page.grid_configure(padx=20, pady=18)  # always land in place
                except Exception:
                    pass

        step()

    # ========== COUNTRY FILTER ==========

    def on_country_picked(self, code, name):
        """Picker callback: show only that country's proxies (``None`` = all)."""
        if not code:
            self.clear_country_filter()
            return
        shown = self.proxy_grid.set_country_filter(code, name)
        self.proxy_manager_page.country_select.set_active(code, name, shown)
        self.update_filter_status()
        self.log(f"Country filter → {name or code}: {shown} proxies on screen.")

    def clear_country_filter(self):
        """Drop the country filter and show every proxy again."""
        self.proxy_grid.set_country_filter(None)
        self.proxy_manager_page.country_select.set_active(None)
        self.update_filter_status()
        self.log("Country filter cleared - showing all proxies.")

    def refresh_country_options(self, force=False):
        """Rebuild the picker's country list from the grid (cheap, change-guarded).

        Guarded twice: by a 1 s throttle (a scan adds thousands of rows a second,
        and recounting per row would burn the CPU the tester needs) and by a
        signature check, so an idle page costs nothing.
        """
        now = time.time()
        if not force and now - getattr(self, "_country_refresh_at", 0.0) < 1.0:
            return
        grid = self.proxy_grid
        sig = (len(grid.data), grid.filter_code)
        if not force and sig == self._country_sig:
            return
        self._country_refresh_at = now
        self._country_sig = sig
        page = self.proxy_manager_page
        page.country_select.set_options(grid.country_summary(), len(grid.data))
        if grid.filter_code:
            # The selection is kept even when that country has no proxies (yet):
            # the grid shows its own empty state rather than silently dropping the
            # country the user just picked.
            page.country_select.set_active(
                grid.filter_code, grid.filter_name, grid.visible_count()
            )
        self.update_filter_status()

    def update_filter_status(self):
        """Keep the "Showing X of Y" line and the Reset button in sync."""
        grid = self.proxy_grid
        page = self.proxy_manager_page
        total = len(grid.data)
        if grid.filter_code:
            label = grid.filter_name or grid.filter_code
            text = f"Showing {grid.visible_count()} of {total} proxies · {label}"
            page.btn_clear_filter.configure(state="normal", text_color=COLORS["accent_glow"])
        else:
            text = f"Showing all {total} proxies"
            page.btn_clear_filter.configure(state="disabled", text_color=COLORS["text_mute"])
        try:
            page.lbl_filter_status.configure(text=text)
        except Exception:
            pass

    def _update_header_status(self):
        """Header strip: a breathing dot plus the current activity and totals."""
        active = self.testing or self.running or self.stress_running
        if self.testing:
            label = "Testing proxies"
        elif self.stress_running:
            label = "Stress test running"
        elif self.running:
            label = "Traffic running"
        else:
            label = "Idle"

        if active:
            import math

            phase = (time.time() * 1.4) % 1.0
            glow = 0.5 + 0.5 * math.sin(phase * 2 * math.pi)
            color = mix(COLORS["success"], COLORS["accent_2"], glow)
            text_color = COLORS["text"]
        else:
            color = COLORS["text_mute"]
            text_color = COLORS["text_dim"]

        try:
            self.dot.configure(fg_color=color)
            self.lbl_header_status.configure(text=label, text_color=text_color)
            tally = getattr(self, "_test_tally", None)
            if tally and getattr(self, "lbl_test_status", None) is not None:
                checked = tally.get("checked", 0)
                total = tally.get("total") or checked or 1
                line = (
                    f"Checked {checked}/{total} · "
                    f"{tally.get('active', 0)} active · {tally.get('dead', 0)} dead"
                )
                if tally.get("hidden"):
                    line += f" · {tally['hidden']} hidden (Hide Dead is on)"
                if tally.get("last"):
                    line += f"\nlast: {tally['last']}"
                if checked and checked >= total and not self.testing:
                    line += "\nFinished."
                    if not tally.get("active"):
                        line += " Nothing came back - check the internet, the Test URL, or untick Hide Dead."
                self.lbl_test_status.configure(text=line)
            self.lbl_header_meta.configure(
                text=f"{self.active_proxy_count} ACTIVE · {len(self.proxy_grid.data) if hasattr(self, 'proxy_grid') else 0} LOADED"
            )
            if hasattr(self, "lbl_side_active"):
                self.lbl_side_active.configure(
                    text=f"{len(self.proxy_grid.data)} proxies in the grid"
                    if hasattr(self, "proxy_grid")
                    else ""
                )
        except Exception:
            pass

    # ========== STRESS TEST PAGE METHODS ==========

    def stress_log(self, message: str):
        """Log a message to the stress test log box."""
        timestamp = time.strftime("%H:%M:%S")
        self.stress_log_box.insert("end", f"[{timestamp}] {message}\n")
        self.stress_log_box.see("end")

    def stress_log_safe(self, message: str):
        """Thread-safe stress log."""
        self.after(0, lambda: self.stress_log(message))

    def update_stress_proxy_count(self):
        """Update the HTTP proxy count label."""
        all_active = (
            self.proxy_grid.get_active_objects() if hasattr(self, "proxy_grid") else []
        )
        http_count = sum(1 for p in all_active if p.get("type", "").upper() == "HTTP")
        self.stress_proxy_count_label.configure(text=f"HTTP Proxies: {http_count:,}")

    def toggle_stress_test(self):
        """Start or stop the stress test."""
        if self.stress_running:
            self.stress_running = False
            self.btn_stress_start.configure(
                text="START STRESS TEST", fg_color=COLORS["success"]
            )
            if self.stress_engine:
                self.stress_engine.stop()
            self.stress_log("Stopping stress test...")
        else:
            # Confirmation dialog
            if not messagebox.askyesno(
                "Confirm Stress Test",
                "AUTHORIZED TESTING ONLY\n\n"
                "This will send high-volume traffic to the target.\n"
                "Only use on servers you own or have permission to test.\n\n"
                "Continue?",
                icon="warning",
            ):
                return

            self.stress_running = True
            self.btn_stress_start.configure(
                text="⏹ STOP STRESS TEST", fg_color=COLORS["danger"]
            )

            # Start in separate thread
            self.stress_thread = threading.Thread(
                target=self.run_stress_engine, daemon=True
            )
            self.stress_thread.start()

    def toggle_stress_pause(self):
        """Pause or resume the stress test."""
        if not self.stress_running:
            return

        self.stress_paused = not self.stress_paused

        if self.stress_paused:
            self.btn_stress_pause.configure(text="RESUME", fg_color=COLORS["success"])
            if self.stress_engine:
                self.stress_engine.pause()
            self.stress_log("Stress test PAUSED")
        else:
            self.btn_stress_pause.configure(text="PAUSE", fg_color=COLORS["warning"])
            if self.stress_engine:
                self.stress_engine.resume()
            self.stress_log("Stress test RESUMED")

    def reset_stress_stats(self):
        """Reset stress test statistics."""
        self.stress_stats = {
            "requests": 0,
            "success": 0,
            "failed": 0,
            "rps": 0.0,
            "latency": 0.0,
            "proxies_used": 0,
        }
        for key, lbl in self.stress_stat_labels.items():
            if key in ("rps", "latency"):
                lbl.configure(text="0.0")
            else:
                lbl.configure(text="0")
        self.stress_log("Statistics reset.")

    def run_stress_engine(self):
        """Run the stress test engine."""
        from core.stress_engine import (
            AttackType,
            RequestMethod,
            StressConfig,
            StressEngine,
        )

        target_url = self.stress_entry_url.get().strip()

        # Validate URL
        if not target_url:
            self.stress_log_safe("Error: Please enter a target URL")
            self.stress_running = False
            self.after(
                0,
                lambda: self.btn_stress_start.configure(
                    text="START STRESS TEST", fg_color=COLORS["success"]
                ),
            )
            return

        # Enforce HTTP only for stress testing
        if target_url.startswith("https://"):
            self.stress_log_safe(
                "Warning: HTTPS detected. HTTP proxies cannot tunnel HTTPS. Converting to HTTP..."
            )
            target_url = target_url.replace("https://", "http://", 1)
        elif not target_url.startswith("http://"):
            target_url = "http://" + target_url

        # Map attack type
        attack_type_map = {
            "HTTP Flood": AttackType.HTTP_FLOOD,
            "Slowloris": AttackType.SLOWLORIS,
            "RUDY": AttackType.RUDY,
            "Randomized": AttackType.RANDOMIZED,
        }
        attack_type = attack_type_map.get(
            self.stress_attack_type.get(), AttackType.HTTP_FLOOD
        )

        # Map method
        method_map = {
            "GET": RequestMethod.GET,
            "POST": RequestMethod.POST,
            "HEAD": RequestMethod.HEAD,
            "PUT": RequestMethod.PUT,
            "DELETE": RequestMethod.DELETE,
        }
        method = method_map.get(self.stress_method.get(), RequestMethod.GET)

        threads = Utils.safe_int(
            self.stress_slider_threads.get(), default=100, min_val=10, max_val=1000
        )
        duration = Utils.safe_int(
            self.stress_slider_duration.get(), default=60, min_val=10, max_val=300
        )
        rps_limit = Utils.safe_int(
            self.stress_slider_rps.get(), default=0, min_val=0, max_val=10000
        )

        # Get HTTP proxies only
        all_active = self.proxy_grid.get_active_objects()
        engine_proxies = []

        for p in all_active:
            if p.get("type", "").upper() == "HTTP":
                try:
                    creds = self._credential_map().get(f"{p['ip']}:{p['port']}", ("", ""))
                    engine_proxies.append(
                        ProxyConfig(
                            host=p["ip"],
                            port=int(p["port"]),
                            protocol="http",
                            username=creds[0] or None,
                            password=creds[1] or None,
                        )
                    )
                except (ValueError, KeyError):
                    continue

        if not engine_proxies:
            self.stress_log_safe(
                "Error: No HTTP proxies available. Load and test proxies first."
            )
            self.stress_running = False
            self.after(
                0,
                lambda: self.btn_stress_start.configure(
                    text="START STRESS TEST", fg_color=COLORS["success"]
                ),
            )
            return

        self.stress_log_safe(
            f"Found {len(engine_proxies)} HTTP proxies for stress testing"
        )

        # Build config
        config = StressConfig(
            target_url=target_url,
            attack_type=attack_type,
            method=method,
            threads=threads,
            duration_seconds=duration,
            rps_limit=rps_limit,
        )

        # Create and run engine
        self.stress_engine = StressEngine(
            config=config,
            proxies=engine_proxies,
            on_stats_update=self.on_stress_stats_update,
            on_log=self.stress_log_safe,
        )

        # Run async loop
        asyncio.run(self.stress_engine.run())

        self.stress_running = False
        self.after(
            0,
            lambda: self.btn_stress_start.configure(
                text="START STRESS TEST", fg_color=COLORS["success"]
            ),
        )
        self.stress_log_safe("Stress test completed.")

    def on_stress_stats_update(self, stats):
        """Callback for stress engine stats updates."""

        def update_on_main():
            self.stress_stat_labels["requests"].configure(
                text=f"{stats.requests_sent:,}"
            )
            self.stress_stat_labels["success"].configure(
                text=f"{stats.requests_success:,}"
            )
            self.stress_stat_labels["failed"].configure(
                text=f"{stats.requests_failed:,}"
            )
            self.stress_stat_labels["rps"].configure(text=f"{stats.current_rps:.1f}")
            self.stress_stat_labels["latency"].configure(
                text=f"{stats.avg_latency_ms:.0f}ms"
            )
            self.stress_stat_labels["proxies"].configure(text=f"{stats.proxies_used:,}")

        self.after(0, update_on_main)

    # ========== SETTINGS PAGE METHODS ==========

    def browse_browser_path(self, entry_widget):
        """Open file dialog to select browser executable for a specific entry."""
        path = filedialog.askopenfilename(
            title="Select Browser Executable",
            filetypes=[("Executable", "*.exe"), ("All Files", "*.*")],
        )
        if path:
            entry_widget.delete(0, "end")
            entry_widget.insert(0, path)
            self.log(f"Browser path set: {path}")

    def detect_all_browsers(self):
        """Auto-detect all installed browsers and populate paths."""
        from core.browser_manager import BrowserManager

        browsers = BrowserManager.detect_browsers()

        if not browsers:
            self.log("No browsers detected.")
            messagebox.showwarning(
                "Browser Detection",
                "No browsers found.\n\nPlease install Chrome, Chromium, Edge, Brave, or Firefox,\nor run: playwright install chromium",
            )
            return

        # Map browser names to entry widgets
        entry_map = {
            "Chrome": self.entry_chrome_path,
            "Chromium": self.entry_chromium_path,
            "Edge": self.entry_edge_path,
            "Brave": self.entry_brave_path,
            "Firefox": self.entry_firefox_path,
        }

        detected_names = []
        for browser in browsers:
            if browser.name in entry_map:
                entry = entry_map[browser.name]
                entry.delete(0, "end")
                entry.insert(0, browser.path)
                detected_names.append(browser.name)

        # Show paths section
        if not self.browser_paths_visible:
            self.toggle_browser_paths()

        self.log(f"Detected browsers: {', '.join(detected_names)}")
        messagebox.showinfo(
            "Browser Detection",
            f"Found {len(browsers)} browser(s):\n\n"
            + "\n".join([f"• {b.name}: {b.path}" for b in browsers]),
        )

    def check_captcha_balance(self):
        """Check captcha service balance for all configured providers."""
        key_2cap = self.entry_2captcha_key.get().strip()
        key_anti = self.entry_anticaptcha_key.get().strip()

        if not key_2cap and not key_anti:
            self.lbl_captcha_balance.configure(text="Enter at least one API key")
            return

        self.lbl_captcha_balance.configure(text="Checking...")

        def _check():
            try:
                from core.captcha_manager import CaptchaManager
                from core.models import CaptchaConfig, CaptchaProvider

                config = CaptchaConfig(
                    twocaptcha_key=key_2cap,
                    anticaptcha_key=key_anti,
                    primary_provider=CaptchaProvider.AUTO,
                )

                manager = CaptchaManager(config)
                balances = asyncio.run(manager.get_balances())

                # Format balance display
                parts = []
                for name, bal in balances.items():
                    if bal >= 0:
                        parts.append(f"{name}: ${bal:.2f}")
                    else:
                        parts.append(f"{name}: error")

                balance_text = " | ".join(parts) if parts else "No balances"
                has_positive = any(b > 0 for b in balances.values())

                self.after(
                    0,
                    lambda: self.lbl_captcha_balance.configure(
                        text=balance_text,
                        text_color=(
                            COLORS["success"] if has_positive else COLORS["warning"]
                        ),
                    ),
                )
            except ImportError:
                self.after(
                    0,
                    lambda: self.lbl_captcha_balance.configure(
                        text="aiohttp not installed", text_color=COLORS["danger"]
                    ),
                )
            except Exception as e:
                self.after(
                    0,
                    lambda err=e: self.lbl_captcha_balance.configure(
                        text=f"Balance: Error - {err}"
                    ),
                )

        threading.Thread(target=_check, daemon=True).start()

    def log(self, msg):
        """Log a message to the activity log with auto-scroll.

        Never raises: a log line arriving while the window is closing (or from a
        worker thread right after shutdown) must not take the whole app down -
        the message still goes to the Python logger in that case.
        """
        try:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.log_box.configure(state="disabled")
            # Schedule see() after GUI updates to ensure proper scroll
            self.log_box.after(10, lambda: self.log_box.see("end"))
        except Exception:
            logging.info(msg)

    def log_safe(self, msg):
        """Thread-safe logging - use this when calling from worker threads."""
        self.after(0, lambda: self.log(msg))

    def load_proxy_file(self):
        f = filedialog.askopenfilename()
        if f:
            try:
                self.proxies = []
                self.buffer = Queue()  # Reset with fresh queue
                self.proxy_grid.clear()

                with open(f) as file:
                    for line in file:
                        line = line.strip()
                        if not line:
                            continue
                        # Normalize: Add http:// if no protocol specified (standardizes for dedup)
                        if "://" not in line:
                            line = "http://" + line
                        self.proxies.append(line)

                self.proxies = Utils.deduplicate_proxies(self.proxies)

                self.update_proxy_stats()
                self.log(
                    f"Cleared previous data. Loaded {len(self.proxies)} unique proxies."
                )
            except (OSError, UnicodeDecodeError) as e:
                self.log(f"Error loading proxy file: {e}")

    def import_from_clipboard(self):
        """Import proxies from clipboard (one per line, IP:PORT format)."""
        try:
            clipboard_text = self.clipboard_get()
        except Exception:
            return self.log("Clipboard is empty or unavailable.")

        if not clipboard_text or not clipboard_text.strip():
            return self.log("Clipboard is empty.")

        # Parse lines - supports IP:PORT or protocol://IP:PORT format
        lines = clipboard_text.strip().split("\n")
        new_proxies = []

        # Build set of existing proxies to skip duplicates
        existing_keys = set()
        for p in self.checked_proxies:
            existing_keys.add(f"{p.get('host', '')}:{p.get('port', '')}")
        for p in self.proxy_grid.data:
            existing_keys.add(f"{p.get('ip', '')}:{p.get('port', '')}")

        added = 0
        skipped = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Normalize: Add http:// if no protocol specified
            if "://" not in line:
                line = "http://" + line

            # Extract host:port for dedup check
            try:
                if "://" in line:
                    addr = line.split("://", 1)[1]
                else:
                    addr = line
                if "@" in addr:
                    addr = addr.split("@", 1)[1]

                if addr in existing_keys:
                    skipped += 1
                    continue

                new_proxies.append(line)
                existing_keys.add(addr)
                added += 1
            except (ValueError, IndexError):
                new_proxies.append(line)
                added += 1

        if new_proxies:
            self.proxies.extend(new_proxies)
            self.proxies = Utils.deduplicate_proxies(self.proxies)
            self.update_proxy_stats()

        msg = f"Clipboard: Added {added} proxies"
        if skipped > 0:
            msg += f", skipped {skipped} duplicates"
        self.log(msg)

    def export_proxies(self, filter_type: str = "all"):
        """
        Export active proxies to files, filtered by type.

        Args:
            filter_type: "all", "http", "https", or "socks"
        """
        active_objs = self.proxy_grid.get_active_objects()

        if not active_objs:
            return self.log("No active proxies to export.")

        # Check if export folder is set, if not prompt user
        export_folder = self.settings.get("export_folder", "")
        if not export_folder or not os.path.exists(export_folder):
            export_folder = filedialog.askdirectory(
                title="Select Export Folder",
                initialdir=os.path.join(os.getcwd(), "resources"),
            )
            if not export_folder:
                return self.log("Export cancelled - no folder selected.")

            # Save the selection to settings
            self.settings["export_folder"] = export_folder
            Utils.save_settings(self.settings)
            self.log(f"Export folder set to: {export_folder}")

        # Check if protocol prefix should be included
        include_protocol = self.chk_export_protocol.get()
        self.settings["export_with_protocol"] = include_protocol
        Utils.save_settings(self.settings)

        # Categorize proxies
        http_list = []
        https_list = []
        socks_list = []

        for p in active_objs:
            p_type = p.get("type", "").upper()
            p_str = f"{p['ip']}:{p['port']}"

            if "SOCKS" in p_type:
                if include_protocol:
                    socks_list.append(
                        f"socks5://{p_str}" if "5" in p_type else f"socks4://{p_str}"
                    )
                else:
                    socks_list.append(p_str)
            elif p_type == "HTTPS":
                if include_protocol:
                    https_list.append(f"https://{p_str}")
                else:
                    https_list.append(p_str)
            else:
                if include_protocol:
                    http_list.append(f"http://{p_str}")
                else:
                    http_list.append(p_str)

        exported_count = 0
        try:
            if filter_type == "all":
                # Export all to separate files
                if http_list:
                    with open(os.path.join(export_folder, "http.txt"), "w") as f:
                        f.write("\n".join(http_list))
                    exported_count += len(http_list)
                if https_list:
                    with open(os.path.join(export_folder, "https.txt"), "w") as f:
                        f.write("\n".join(https_list))
                    exported_count += len(https_list)
                if socks_list:
                    with open(os.path.join(export_folder, "socks.txt"), "w") as f:
                        f.write("\n".join(socks_list))
                    exported_count += len(socks_list)
                # Also export combined file
                all_list = http_list + https_list + socks_list
                if all_list:
                    with open(os.path.join(export_folder, "all_proxies.txt"), "w") as f:
                        f.write("\n".join(all_list))
                self.log(
                    f"Exported All: {len(http_list)} HTTP, {len(https_list)} HTTPS, {len(socks_list)} SOCKS to {export_folder}"
                )

            elif filter_type == "http":
                if not http_list:
                    return self.log("No HTTP proxies to export.")
                with open(os.path.join(export_folder, "http.txt"), "w") as f:
                    f.write("\n".join(http_list))
                self.log(
                    f"Exported {len(http_list)} HTTP proxies to {export_folder}/http.txt"
                )

            elif filter_type == "https":
                if not https_list:
                    return self.log("No HTTPS proxies to export.")
                with open(os.path.join(export_folder, "https.txt"), "w") as f:
                    f.write("\n".join(https_list))
                self.log(
                    f"Exported {len(https_list)} HTTPS proxies to {export_folder}/https.txt"
                )

            elif filter_type == "socks":
                if not socks_list:
                    return self.log("No SOCKS proxies to export.")
                with open(os.path.join(export_folder, "socks.txt"), "w") as f:
                    f.write("\n".join(socks_list))
                self.log(
                    f"Exported {len(socks_list)} SOCKS proxies to {export_folder}/socks.txt"
                )

        except Exception as e:
            self.log(f"Export Error: {e}")

    def load_saved_proxies(self):
        """Load proxies saved from previous session."""
        saved = Utils.load_proxies()
        if not saved:
            return

        # Populate checked_proxies list and display in grid
        self.checked_proxies = saved
        active_count = 0

        for p in saved:
            # Only show active proxies
            if p.get("status") == "Active":
                active_count += 1
                item = {
                    "ip": p.get("host", ""),
                    "port": str(p.get("port", "")),
                    "type": p.get("type", ""),
                    "country": p.get("country", ""),
                    "country_code": p.get("country_code", ""),
                    "city": p.get("city", ""),
                    "status": p.get("status", "Unknown"),
                    "speed": p.get("speed", 0),
                    "anonymity": p.get("anonymity", "Unknown"),
                }
                self.proxy_grid.add(item)

            # Also rebuild self.proxies string list for campaigns
            host = p.get("host", "")
            port = p.get("port", "")
            protocol = p.get("protocol", "http")
            if host and port:
                self.proxies.append(f"{protocol}://{host}:{port}")

        if active_count > 0:
            self.update_proxy_stats()
            self.refresh_country_options(force=True)
            self.log(f"Restored {active_count} active proxies from last session.")

    def save_checked_proxies(self):
        """Save current checked proxies to file for persistence."""
        if not self.checked_proxies:
            return
        # Only save active proxies
        active = [p for p in self.checked_proxies if p.get("status") == "Active"]
        if active:
            Utils.save_proxies(active)
            self.log(f"Saved {len(active)} active proxies.")

    def clear_proxies(self):
        self._proxy_key_cache = None

        """Clear all proxies from memory and delete saved file."""
        self.proxies = []
        self.checked_proxies = []
        self.proxy_grid.clear()
        # the country filter no longer has anything to point at
        self.proxy_grid.set_country_filter(None)
        Utils.clear_saved_proxies()
        self.update_proxy_stats()
        self.refresh_country_options(force=True)
        self.log("All proxies cleared.")

    def clear_dead_proxies(self):
        """Clear only dead proxies from memory and grid, keeping active ones."""
        # Count before clearing
        total_before = len(self.proxy_grid.data)

        # Filter out dead proxies from the grid data
        active_data = [p for p in self.proxy_grid.data if p.get("status") == "Active"]

        # Filter checked_proxies list
        self.checked_proxies = [
            p for p in self.checked_proxies if p.get("status") == "Active"
        ]

        # Clear and repopulate grid with only active proxies
        self.proxy_grid.clear()
        for proxy in active_data:
            self.proxy_grid.add(proxy)
        self.proxy_grid.flush()

        # Update saved proxies file with only active ones
        if active_data:
            Utils.save_proxies(active_data)
        else:
            Utils.clear_saved_proxies()

        dead_count = total_before - len(active_data)
        self.update_proxy_stats()
        self.log(
            f"Cleared {dead_count} dead proxies. {len(active_data)} active proxies remaining."
        )

    def on_closing(self):
        """Handle window close - save proxies and cleanup."""
        # Stop any running operations
        self.testing = False
        self.running = False
        if self.engine:
            self.engine.stop()

        # Stop stress test if running
        self.stress_running = False
        if self.stress_engine:
            self.stress_engine.stop()

        # Save proxies before exit
        if self.checked_proxies:
            self.save_checked_proxies()

        # Also save any proxies currently in the grid
        grid_proxies = self.proxy_grid.get_active_objects()
        if grid_proxies and not self.checked_proxies:
            Utils.save_proxies(grid_proxies)

        # Stop master server if running
        if hasattr(self, "master_control_page"):
            self.master_control_page.cleanup()

        self.destroy()

    def run_scraper(self):
        scrape_threads = Utils.safe_int(
            self.entry_scrape_threads.get(), default=20, min_val=1, max_val=100
        )

        system_proxy = self.entry_system_proxy.get().strip()
        proto = self.combo_system_proxy_proto.get()
        use_for_scrape = self.chk_proxy_for_scrape.get()

        if use_for_scrape and system_proxy:
            # If no scheme is provided, prepend the selected protocol
            if "://" not in system_proxy:
                system_proxy = f"{proto}://{system_proxy}"
        else:
            system_proxy = None

        protos = []
        if self.chk_http.get():
            protos.append("http")
        if self.chk_socks4.get():
            protos.append("socks4")
        if self.chk_socks5.get():
            protos.append("socks5")

        # Resolve sources file path relative to app directory
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sources_file = os.path.join(app_dir, self.settings["sources"])

        # Ensure sources file exists with defaults
        if not os.path.exists(sources_file):
            os.makedirs(os.path.dirname(sources_file), exist_ok=True)
            with open(sources_file, "w") as f:
                f.write(
                    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt\n"
                )
                f.write(
                    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt\n"
                )
                f.write(
                    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http,socks4,socks5&timeout=10000&country=all\n"
                )

        def _job():
            self.log_safe(
                f"Scraping started (Async) using proxy: {system_proxy if system_proxy else 'Direct'}..."
            )
            try:
                with open(sources_file) as f:
                    urls = [
                        line.strip()
                        for line in f
                        if line.strip() and not line.startswith("#")
                    ]

                manager = ThreadedProxyManager()

                # Bandwidth tracking
                total_bytes = 0
                last_time = time.time()

                def on_scrape_progress(bytes_count):
                    nonlocal total_bytes, last_time
                    total_bytes += bytes_count

                    now = time.time()
                    delta = now - last_time
                    if delta >= 1.0:
                        # Mbps = (bytes * 8) / 1024 / 1024 / delta
                        # We calculate usage over the LAST delta interval for "current speed"?
                        # Or cumulative average? "Current speed" is better.
                        # But here total_bytes is cumulative.
                        # I need diff.
                        pass  # Logic inside ThreadedProxyManager returns bytes per call? Yes.

                # Improved bandwidth tracking with closure state
                scrape_bytes_buffer = 0
                scrape_total_bytes = 0

                def on_progress_wrapper(b):
                    nonlocal scrape_bytes_buffer, scrape_total_bytes, last_time
                    scrape_bytes_buffer += b
                    scrape_total_bytes += b
                    now = time.time()
                    if now - last_time >= 1.0:
                        mbps = (
                            (scrape_bytes_buffer * 8) / 1024 / 1024 / (now - last_time)
                        )
                        total_mb = scrape_total_bytes / (1024 * 1024)
                        self.after(
                            0,
                            lambda m=mbps, t=total_mb: self.lbl_bandwidth.configure(
                                text=f"Scrape: {m:.2f} Mbps ({t:.1f} MB)"
                            ),
                        )
                        last_time = now
                        scrape_bytes_buffer = 0

                # Run threaded scraper
                results = manager.scrape(
                    urls,
                    protos,
                    max_threads=scrape_threads,
                    scraper_proxy=system_proxy,
                    on_progress=on_progress_wrapper,
                )

                # Convert back to string format for the UI list for now (or update UI to hold objects)
                # The UI currently expects simple strings in self.proxies
                found_strings = [p.to_curl_cffi_format() for p in results]

                # Build set of already-checked proxy keys (host:port) to exclude duplicates
                checked_keys = set()
                for p in self.checked_proxies:
                    key = f"{p.get('host', '')}:{p.get('port', '')}"
                    checked_keys.add(key)

                # Also include proxies already in the grid
                for p in self.proxy_grid.data:
                    key = f"{p.get('ip', '')}:{p.get('port', '')}"
                    checked_keys.add(key)

                # Filter out already-checked proxies from new scrape
                new_proxies = []
                skipped = 0
                for p_str in found_strings:
                    # Extract host:port from proxy string
                    try:
                        if "://" in p_str:
                            addr = p_str.split("://", 1)[1]
                        else:
                            addr = p_str
                        if "@" in addr:
                            addr = addr.split("@", 1)[1]
                        # addr is now host:port
                        if addr in checked_keys:
                            skipped += 1
                            continue
                        new_proxies.append(p_str)
                    except (ValueError, IndexError):
                        new_proxies.append(p_str)

                if skipped > 0:
                    self.log_safe(f"Skipped {skipped} already-checked proxies.")

                self.proxies.extend(new_proxies)
                self.proxies = Utils.deduplicate_proxies(self.proxies)

                self.after(0, self.update_proxy_stats)
                self.log_safe(f"Scrape complete. Found {len(results)} proxies.")

                # Show notification
                total = len(self.proxies)
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Scrape Complete",
                        f"Found {len(results)} new proxies.\nTotal proxies loaded: {total}",
                    ),
                )
            except Exception as e:
                self.log_safe(f"Scrape Error: {e}")

        threading.Thread(target=_job, daemon=True).start()

    def update_proxy_stats(self):
        total = len(self.proxies)
        self.lbl_loaded.configure(text=f"Total: {total}")

        # Single-pass stats collection (more efficient than separate calls)
        counts, anon_counts = self.proxy_grid.get_all_stats()
        self.lbl_proto_counts.configure(
            text=f"HTTP: {counts['HTTP']} | HTTPS: {counts['HTTPS']} | SOCKS4: {counts['SOCKS4']} | SOCKS5: {counts['SOCKS5']}"
        )
        self.lbl_anon_counts.configure(
            text=f"Elite: {anon_counts['Elite']} | Anonymous: {anon_counts['Anonymous']} | Transparent: {anon_counts['Transparent']} | Unknown: {anon_counts['Unknown']}"
        )

        # Update stress test HTTP proxy count
        if hasattr(self, "stress_proxy_count_label"):
            self.stress_proxy_count_label.configure(
                text=f"HTTP Proxies: {counts['HTTP']:,}"
            )

        # Keep the country picker and the "Showing X of Y" line current
        self.refresh_country_options()
        self.update_filter_status()

    def toggle_manual_target(self):
        """Toggle between manual test URL and validators-based testing."""
        if self.chk_manual_target.get():
            self.entry_test_url.configure(state="normal", fg_color=COLORS["card"])
        else:
            self.entry_test_url.configure(state="disabled", fg_color=COLORS["nav"])

    def test_system_proxy(self):
        system_proxy = self.entry_system_proxy.get().strip()
        proto = self.combo_system_proxy_proto.get()
        if not system_proxy:
            return self.log("Enter a proxy first.")

        if "://" not in system_proxy:
            system_proxy = f"{proto}://{system_proxy}"

        self.btn_test_proxy.configure(
            text="...", state="disabled", fg_color=COLORS["warning"]
        )

        def _test():
            self.log(f"Testing system proxy: {system_proxy}...")
            try:
                proxies = {"http": system_proxy, "https": system_proxy}
                r = requests.get(
                    "https://api.ipify.org?format=json", proxies=proxies, timeout=10
                )
                if r.status_code == 200:
                    self.log(f"System Proxy OK! Exit IP: {r.json().get('ip')}")
                    self.after(
                        0,
                        lambda: self.btn_test_proxy.configure(
                            text="OK", fg_color=COLORS["success"]
                        ),
                    )
                else:
                    self.log(f"System Proxy Failed: HTTP {r.status_code}")
                    self.after(
                        0,
                        lambda: self.btn_test_proxy.configure(
                            text="FAIL", fg_color=COLORS["danger"]
                        ),
                    )
            except Exception as e:
                self.log(f"System Proxy Error: {e}")
                self.after(
                    0,
                    lambda: self.btn_test_proxy.configure(
                        text="ERR", fg_color=COLORS["danger"]
                    ),
                )

            # Reset button after 2 seconds
            def reset():
                self.btn_test_proxy.configure(
                    text="TEST", state="normal", fg_color=COLORS["accent"]
                )

            self.after(2000, reset)

        threading.Thread(target=_test, daemon=True).start()

    def toggle_pause_test(self):
        if not self.testing:
            return
        self.testing_paused = not self.testing_paused
        if self.testing_paused:
            self.btn_pause.configure(text="RESUME", fg_color=COLORS["success"])
            self.log(
                "Testing PAUSED - You can change settings now. Click RESUME to continue."
            )
        else:
            self.btn_pause.configure(text="PAUSE", fg_color=COLORS["warning"])
            self.log("Testing RESUMED.")

    def toggle_test(self):
        if self.testing:
            self.testing = False
            self.testing_paused = False
            self.btn_test.configure(text="TEST ALL", fg_color=COLORS["success"])
            self.btn_pause.configure(
                state="disabled", text="PAUSE", fg_color=COLORS["warning"]
            )
            # Save proxies when user stops testing
            self.save_checked_proxies()
            self.log("Saved checked proxies.")
        else:
            if not self.proxies:
                return self.log("Load proxies first.")
            self.testing = True
            self.testing_paused = False
            self.btn_test.configure(text="STOP", fg_color=COLORS["danger"])
            self.btn_pause.configure(
                state="normal", text="PAUSE", fg_color=COLORS["warning"]
            )

            self.proxy_grid.clear()
            self.checked_proxies = []  # Clear for new test run
            threading.Thread(target=self.tester_thread, daemon=True).start()

    def tester_thread(self):
        # Progress bar is always visible (pinned), just reset to start
        self.after(0, lambda: self.progress_bar.set(0))
        try:
            use_manual_target = bool(self.chk_manual_target.get())

            # Get enabled validators from settings (needed early for URL determination)
            validators_enabled = self.settings.get("validators_enabled", {})
            active_validators = []
            for v in DEFAULT_VALIDATORS:
                if validators_enabled.get(v.name, v.enabled):
                    v.enabled = True
                    active_validators.append(v)
                else:
                    v.enabled = False

            # Determine test URL based on mode
            if use_manual_target:
                url = self.entry_test_url.get().strip()
                if not Utils.validate_url(url):
                    self.log_safe(
                        "Invalid test URL. Must start with http:// or https://"
                    )
                    self.testing = False
                    self.after(0, lambda: self.progress_bar.set(0))
                    self.after(
                        0,
                        lambda: self.btn_test.configure(
                            text="TEST ALL", fg_color=COLORS["success"]
                        ),
                    )
                    return
                self.log_safe(f"Using manual target: {url}")
            else:
                # Use first enabled validator as test URL
                if not active_validators:
                    self.log_safe(
                        "No validators enabled! Enable at least one validator in Settings."
                    )
                    self.testing = False
                    self.after(0, lambda: self.progress_bar.set(0))
                    self.after(
                        0,
                        lambda: self.btn_test.configure(
                            text="TEST ALL", fg_color=COLORS["success"]
                        ),
                    )
                    return
                url = active_validators[0].url
                self.log_safe(
                    f"Using validators (primary: {active_validators[0].name})"
                )

            # A dead test target makes every proxy look dead, and with "Hide Dead"
            # on the grid then stays empty with nothing explaining why. Swap in a
            # reachable target before the run starts.
            working = pick_working_target(url, log=self.log_safe)
            if not working:
                self.log_safe(
                    "Testing aborted: no reachable test target. Check your "
                    "internet connection or set a working Test URL."
                )
                self.testing = False
                self.after(0, lambda: self.progress_bar.set(0))
                self.after(0, lambda: self.btn_test.configure(
                    text="TEST ALL", fg_color=COLORS["success"]))
                return
            url = working

            timeout_ms = Utils.safe_int(
                self.entry_timeout.get(), default=3000, min_val=100, max_val=30000
            )
            check_threads = Utils.safe_int(
                self.entry_check_threads.get(), default=50, min_val=1, max_val=10000
            )
            hide_dead = self.chk_hide_dead.get()

            # ---- live counters the GUI shows while this runs ------------------
            self._test_tally = {
                "checked": 0,
                "active": 0,
                "dead": 0,
                "hidden": 0,
                "total": 0,
                "last": "starting",
            }


            # Check if system proxy should be used for checking
            use_proxy_for_check = self.chk_proxy_for_check.get()
            system_proxy = (
                self.entry_system_proxy.get().strip() if use_proxy_for_check else None
            )
            if system_proxy:
                proto = self.combo_system_proxy_proto.get()
                if "://" not in system_proxy:
                    system_proxy = f"{proto}://{system_proxy}"
                self.log_safe(f"Using system proxy for checking: {system_proxy}")
                # Note: Full proxy chaining (hiding IP from proxy operators) requires SOCKS5 tunneling
                # Currently, this routes initial connection through system proxy

            # Prepare ProxyConfig objects from self.proxies (strings)
            # self.proxies contains strings like "http://1.2.3.4:80" or just "1.2.3.4:80"
            check_configs = []
            allowed_protos = []
            if self.chk_http.get():
                allowed_protos.append("http")
            if self.chk_socks4.get():
                allowed_protos.append("socks4")
            if self.chk_socks5.get():
                allowed_protos.append("socks5")

            for p_str in self.proxies:
                try:
                    if "://" in p_str:
                        parts = p_str.split("://")
                        scheme = parts[0].lower()
                        addr = parts[1]

                        # Treat https as http for validation against allowed_protos
                        check_scheme = "http" if scheme == "https" else scheme

                        if check_scheme not in allowed_protos:
                            continue
                    else:
                        # Ambiguous proxy, try all allowed protocols?
                        # Or default to HTTP. For checking, we usually want to know what it is.
                        # The old logic checked all allowed.
                        addr = p_str
                        scheme = "ambiguous"

                    if ":" in addr:
                        # One parser for every format the user may paste:
                        # ip:port, ip:port:user:pass, user:pass@ip:port,
                        # proto://user:pass@ip:port. Credentials must survive: an
                        # authenticated provider returns 407 without them and the
                        # proxy would look dead.
                        from core.proxy_parser import to_proxy_config

                        if scheme == "ambiguous":
                            for proto in allowed_protos:
                                config = to_proxy_config(p_str, proto)
                                if config is not None:
                                    check_configs.append(config)
                        else:
                            protocol = "http" if scheme == "https" else scheme
                            config = to_proxy_config(p_str, protocol)
                            if config is not None:
                                check_configs.append(config)
                except (ValueError, IndexError, AttributeError):
                    continue  # Skip malformed proxy strings

        except Exception as e:
            self.log_safe(f"Config Error: {e}")
            return

        self.log_safe(f"Testing {len(check_configs)} proxies (Threaded/TLS)...")

        manager = ThreadedProxyManager()

        last_save_count = 0  # Track when we last saved
        last_save_time = time.time()  # Time-based save throttling
        last_ui_update = time.time()  # Throttle UI updates
        import threading as _threading

        ui_lock = _threading.Lock()  # Prevent concurrent UI scheduling

        # Bandwidth tracking with actual bytes
        total_bytes = [0]  # Use list for mutable closure
        last_bytes = [0]
        last_bandwidth_time = [time.time()]

        # Scale progress update interval with thread count to prevent callback flooding
        # Higher threads = less frequent updates to avoid GUI freeze
        progress_interval = max(50, min(500, check_threads // 10))

        # We need a callback that is thread-safe
        def on_progress(res: ProxyCheckResult, idx, total):
            nonlocal last_save_count, last_save_time, last_ui_update
            if not self.testing:
                return  # Stop signal

            # Track actual bytes transferred
            total_bytes[0] += res.bytes_transferred

            # Store result for persistence (only active proxies)
            if res.status == "Active":
                proxy_data = {
                    "host": res.proxy.host,
                    "port": res.proxy.port,
                    "protocol": res.proxy.protocol,
                    "status": res.status,
                    "speed": res.speed,
                    "type": res.type,
                    "country": res.country,
                    "country_code": res.country_code,
                    "city": res.city,
                    "anonymity": res.anonymity,
                }
                self.checked_proxies.append(proxy_data)

                # Auto-save with time-based throttling to prevent file contention
                # Only save if: 30+ seconds since last save AND 100+ new proxies
                active_count = len(self.checked_proxies)
                time_since_save = time.time() - last_save_time
                if (active_count - last_save_count >= 100) and (time_since_save >= 30):
                    last_save_count = active_count
                    last_save_time = time.time()
                    self.save_checked_proxies()
                    self.log_safe(f"Auto-saved {active_count} proxies...")

            # ---- live tally + a readable log line per result ----------------
            tally = getattr(self, "_test_tally", None)
            if tally is not None:
                tally["checked"] = tally.get("checked", 0) + 1
                tally["total"] = total
                if res.status == "Active":
                    tally["active"] = tally.get("active", 0) + 1
                else:
                    tally["dead"] = tally.get("dead", 0) + 1
                where = next(
                    (x for x in (res.country_code, res.city, res.country) if x), "unknown location"
                )
                tally["last"] = f"{res.proxy.host}:{res.proxy.port} {where} {res.status}"
                checked = tally["checked"]
                if res.status == "Active" or total <= 60 or checked % 10 == 0 or checked == total:
                    self.log_safe(f"[{checked}/{total}] {tally['last']} - {res.speed} ms")

            if hide_dead and res.status != "Active":
                if tally is not None:
                    tally["hidden"] = tally.get("hidden", 0) + 1
            else:
                item = {
                    "ip": res.proxy.host,
                    "port": str(res.proxy.port),
                    "type": res.type,
                    "country": res.country,
                    "country_code": res.country_code,
                    "city": res.city,
                    "status": res.status,
                    "speed": res.speed,
                    "anonymity": res.anonymity,
                }
                self.buffer.put(item)  # Thread-safe put

            # Throttled UI updates - time-based to prevent callback flooding
            # Only schedule new callbacks if 200ms+ since last update
            with ui_lock:
                now = time.time()
                should_update_ui = (now - last_ui_update >= 0.2) or idx == total

                if should_update_ui and (idx % progress_interval == 0 or idx == total):
                    delta = now - last_bandwidth_time[0]
                    if delta >= 0.5:  # Bandwidth calc every 500ms
                        # Calculate actual bandwidth from bytes transferred
                        bytes_diff = total_bytes[0] - last_bytes[0]
                        # Convert to Mbps: (bytes * 8 bits) / (1024 * 1024) / seconds
                        mbps = (bytes_diff * 8) / (1024 * 1024) / delta
                        # Also show total transferred
                        total_mb = total_bytes[0] / (1024 * 1024)
                        self.after(
                            0,
                            lambda m=mbps, t=total_mb: self.lbl_bandwidth.configure(
                                text=f"Traffic: {m:.2f} Mbps ({t:.1f} MB)"
                            ),
                        )
                        last_bandwidth_time[0] = now
                        last_bytes[0] = total_bytes[0]

                    # Single progress update
                    progress = idx / total
                    self.after(0, lambda p=progress: self.progress_bar.set(p))
                    last_ui_update = now

        test_depth = self.settings.get("validator_test_depth", "quick")

        # A crash inside the check worker used to be invisible: the grid simply
        # stayed empty. Catch it and say what happened.
        try:
            pass
        except Exception:
            pass

        # Run threaded check with validators
        manager.check_proxies(
            check_configs,
            url,
            timeout_ms,
            self.real_ip,
            on_progress,
            concurrency=check_threads,
            pause_checker=lambda: self.testing_paused,
            validators=active_validators if active_validators else None,
            test_depth=test_depth,
            system_proxy=system_proxy,
        )

        self.testing = False
        self.after(0, lambda: self.progress_bar.set(1.0))  # Show complete, then fade
        self.after(500, lambda: self.progress_bar.set(0))  # Reset after brief delay
        self.after(
            0,
            lambda: self.btn_test.configure(
                text="TEST ALL", fg_color=COLORS["success"]
            ),
        )
        self.after(
            0,
            lambda: self.btn_pause.configure(
                state="disabled", text="PAUSE", fg_color=COLORS["warning"]
            ),
        )

        # Save checked proxies for persistence
        self.save_checked_proxies()
        if not self.checked_proxies:
            self.log_safe(
                f"{len(check_configs)} proxies tested, 0 responded. If they all failed, "
                f"the test target or your internet is the problem rather than the proxies "
                f"(target: {url}). Untick 'Hide Dead' to see the individual results."
            )
        self.log_safe("Testing complete.")

    def toggle_attack(self):
        if self.running:
            self.running = False
            self.btn_attack.configure(text="START CAMPAIGN", fg_color=COLORS["success"])
            if self.engine:
                self.engine.stop()
            self.log("Stopping campaign...")
        else:
            self.running = True
            self.btn_attack.configure(text="STOP CAMPAIGN", fg_color=COLORS["danger"])

            # Start Async Engine in a separate thread
            self.engine_thread = threading.Thread(
                target=self.run_async_engine, daemon=True
            )
            self.engine_thread.start()

    # ---------- Tor as the IP source ----------
    def _tor_settings(self):
        """Build TorSettings from the saved settings + dashboard fields."""
        from core.tor_manager import TorSettings

        socks_port = int(self.settings.get("tor_socks_port", 9050) or 9050)
        control_port = int(self.settings.get("tor_control_port", 9051) or 9051)
        try:
            socks_port = int(self.dashboard_page.entry_tor_socks.get() or socks_port)
            control_port = int(
                self.dashboard_page.entry_tor_control.get() or control_port
            )
        except (AttributeError, TypeError, ValueError):
            pass
        return TorSettings(
            socks_port=socks_port,
            control_port=control_port,
            password=str(self.settings.get("tor_password", "") or ""),
            auto_start=bool(self.settings.get("tor_auto_start", True)),
            tor_exe=str(self.settings.get("tor_exe", "") or ""),
            max_ips=int(self.settings.get("max_ip_rotations", 0) or 0),
        )

    def _ensure_tor(self):
        """Return a running TorManager, or None (with the reason logged)."""
        from core.tor_manager import TorManager

        if self.tor_manager is not None:
            self.tor_manager.settings = self._tor_settings()
        else:
            self.tor_manager = TorManager(self._tor_settings())

        status = self.tor_manager.start()
        if status.running and status.authenticated:
            # Tor answers the control port before it has finished building a
            # circuit, so give the exit IP a moment instead of reporting "unknown".
            if not status.exit_ip:
                self.log_safe("Tor is up - waiting for its first circuit ...")
                self.tor_manager.wait_for_exit_ip(timeout=90)
            self.log_safe(self.tor_manager.describe())
            return self.tor_manager

        # One clear explanation, not the same line twice (describe() returns the
        # same text while Tor is down).
        not_found = "not found on this computer" in (status.message or "")
        if not_found:
            self.log_safe(
                "Tor is not installed yet. Press GET TOR on the dashboard: it "
                "downloads the official Tor daemon (about 21 MB, no browser needed) "
                "and starts it for you."
            )
        else:
            self.log_safe(
                status.message
                or "Tor is not reachable. Press START TOR, and check the SOCKS / "
                "control ports."
            )
        self.log_safe(
            "Campaign NOT started: the IP source is set to Tor, and without a "
            "running Tor there is no IP to send traffic through. Switch the IP "
            "source to 'My proxies' to use the proxy list instead."
        )
        return None

    def get_tor_clicked(self):
        """Fetch the official Tor daemon and start it - no Tor Browser needed.

        MR ADS only needs the ``tor`` program (the Tor Project ships it as the
        small "tor expert bundle"); the browser front-end is not required. Having
        the app fetch it here removes the manual download step.
        """

        def worker():
            from core.tor_manager import TorManager, download_tor_bundle

            # Look for an install first. Downloading again while Tor is running
            # cannot even unpack (the running tor.exe is locked), which is what
            # produced the confusing "Could not unpack ... Permission denied"
            # error next to a Tor that was working fine.
            already = TorManager.find_tor_executable(
                str(self.settings.get("tor_exe", "") or "")
            )
            if already:
                self.log_safe(f"Tor is already installed: {already}")
                self.settings["tor_exe"] = already
                self.dashboard_page.settings["tor_exe"] = already
                with contextlib.suppress(Exception):
                    Utils.save_settings(self.settings)
                manager = self._ensure_tor()
                if manager is not None:
                    self.log_safe("Tor is running - ready to send traffic.")
                return

            dest = os.path.join(os.path.expanduser("~"), ".mr_ads_tor")
            self.log_safe("Fetching the official Tor daemon from the Tor Project ...")
            exe = download_tor_bundle(dest, log=self.log_safe)
            if not exe:
                self.log_safe(
                    "Could not fetch Tor automatically. You can also install Tor "
                    "Browser or the Tor Expert Bundle yourself and point Settings at "
                    "its tor.exe."
                )
                return

            self.settings["tor_exe"] = exe
            self.dashboard_page.settings["tor_exe"] = exe
            with contextlib.suppress(Exception):
                Utils.save_settings(self.settings)
            self.log_safe(f"Tor installed: {exe}")

            manager = self._ensure_tor()
            if manager is not None:
                self.log_safe(
                    f"Tor ready. Exit IP: {manager.status.exit_ip or 'unknown'}"
                )

        threading.Thread(target=worker, daemon=True).start()

    def start_tor_clicked(self):
        """Start (or attach to) Tor and report the state."""
        if self._tor_busy:
            self.log("Tor is already starting - give it a few seconds.")
            return

        def worker():
            self._tor_busy = True
            try:
                self.log("Starting Tor / connecting to the control port ...")
                manager = self._ensure_tor()
                if manager is not None:
                    self.log(
                        f"Tor ready. Current exit IP: "
                        f"{manager.status.exit_ip or 'unknown'}"
                    )
            finally:
                self._tor_busy = False

        threading.Thread(target=worker, daemon=True).start()

    def test_tor_clicked(self):
        """Show the current Tor exit IP (and rotate once, to prove it works)."""

        def worker():
            manager = self.tor_manager or self._ensure_tor()
            if manager is None:
                return
            if not manager.status.exit_ip:
                manager.wait_for_exit_ip(timeout=60)
            before = manager.status.exit_ip
            self.log(f"Tor exit IP: {manager.exit_label()}")
            self.log("Asking Tor for a new circuit (this is the IP rotation) ...")
            after = manager.rotate(force=True)
            if after and after != before:
                self.log(f"IP changed: {before} -> {manager.exit_label()}")
            elif after:
                self.log(
                    f"Tor returned the same exit IP ({after}). Tor reuses a circuit "
                    f"for a while - try again in ~{manager.settings.min_rotate_seconds}s, "
                    f"or raise the rotation interval."
                )
            else:
                self.log("Could not confirm a new exit IP - check the Tor log.")

        threading.Thread(target=worker, daemon=True).start()

    def _make_tor_rotate_hook(self, manager):
        """Async hook the engine calls when it wants a new IP."""
        notice = {"at": 0.0}  # rate limit for the "no new exit yet" message

        async def hook():
            before = manager.status.exit_ip
            # force=True: the engine only calls this when its own rule (clicks /
            # timer / a run of failed visits) says the IP must change. Without it
            # Tor's own rate limit silently skipped the request, the exit stayed
            # the same, and the log still claimed a switch - so a campaign could
            # sit on one blocked exit for its whole run.
            new_ip = await asyncio.to_thread(lambda: manager.rotate(force=True))

            country, city = exit_ip_geo(manager.status.exit_ip)
            for proxy in getattr(self.engine, "proxies", []) or []:
                # city is assigned even when empty: keeping the previous exit's
                # city made the log read "The Netherlands Dresden".
                proxy.country = country or proxy.country
                proxy.city = city

            changed = bool(new_ip) and new_ip != before
            if changed:
                self.log_safe(
                    f"IP switched - now going out through {manager.exit_label()}"
                )
                notice["at"] = 0.0
            else:
                # Tor refuses a new circuit sooner than every ~10s, so the engine
                # is told the truth - but only now and then, otherwise a campaign
                # that rotates on every counted click would fill the log with it.
                now = time.time()
                if now - notice["at"] >= 30:
                    notice["at"] = now
                    self.log_safe(
                        f"Tor has not issued a new exit yet - still on "
                        f"{manager.exit_label()}. Tor only allows a new circuit "
                        f"every ~{manager.settings.min_rotate_seconds}s; the engine "
                        f"will ask again."
                    )

            # A quick hint, not a gate. The check below is an HTTP client; the
            # campaign visits with a real browser, and on this very destination the
            # shortener's own dashboard shows visits from datacenter/Tor-like
            # addresses being counted (71 clicks, 21 unique IPs, 0 bot hits) while
            # the HTTP check said "refused". Waiting for a check that can never pass
            # left the campaign sending nothing at all - so one look is taken, it is
            # reported honestly, and the visits go out either way.
            if not self._tor_exit_retries_enabled():
                return
            try:
                from core.source_check import check_source

                target = getattr(getattr(self, "engine", None), "config", None)
                target_url = getattr(target, "target_url", "") or ""
                if not target_url:
                    return
                socks = (
                    f"socks5://{manager.settings.socks_host}:"
                    f"{manager.settings.socks_port}"
                )
                verdict = await check_source(
                    target_url, socks, timeout=10, label="Tor"
                )
                if verdict.usable:
                    self.log_safe(
                        f"Tor exit {manager.status.exit_ip} is accepted by the "
                        f"destination."
                    )
                else:
                    self.log_safe(
                        f"Quick check could not confirm the new Tor exit "
                        f"({verdict.detail[:60]}) - the browser visit still goes out, "
                        f"and if the destination refuses it the engine rotates again "
                        f"straight away."
                    )
            except Exception as exc:
                logging.debug(f"Tor exit pre-check skipped: {exc}")

        return hook

    def _tor_exit_retries_enabled(self) -> bool:
        """Only worth pre-checking when Tor is the campaign's only way out."""
        try:
            return int(self.settings.get("tor_exit_retries", 3) or 0) > 0
        except (TypeError, ValueError):
            return False

    def _ensure_accepted_tor_exit(self, manager, target_url: str) -> str:
        """Find an exit the destination will actually serve before any visit is sent.

        Without this the first visits of a Tor campaign were spent on whatever exit
        happened to be current - the destination refused them and the operator saw
        "failed" piling up before the engine had any chance to rotate. Returns a
        short word for the log.
        """
        if not target_url or not self._tor_exit_retries_enabled():
            return ""
        try:
            from core.source_check import check_source

            socks = (
                f"socks5://{manager.settings.socks_host}:{manager.settings.socks_port}"
            )
            # One look only. The engine re-checks (and rotates) on every visit
            # anyway, so looping here just delayed the campaign's first click.
            verdict = asyncio.run(
                check_source(target_url, socks, timeout=10, label="Tor")
            )
            if verdict.usable:
                self.log_safe(
                    f"Tor exit {manager.status.exit_ip} is accepted by the "
                    f"destination."
                )
                return "accepted"
            self.log_safe(
                f"Quick check could not confirm Tor exit "
                f"{manager.status.exit_ip} ({verdict.detail[:60]}) - the browser "
                f"visit goes out anyway and the engine rotates from there."
            )
            return "refused"
        except Exception as exc:
            logging.debug(f"Tor exit search skipped: {exc}")
            return ""

    def check_target_health_clicked(self):
        """Check the target once and report whether clicks can be counted on it."""
        url = self.entry_url.get().strip()
        if not Utils.validate_url(url):
            self.log("Invalid URL. Must start with http:// or https://")
            return
        self.log(f"Checking target: {url} ...")
        threading.Thread(
            target=self._run_target_health_check, args=(url,), daemon=True
        ).start()

    def _run_target_health_check(self, url: str) -> str:
        """Probe the target (blocking) and log the verdict. Returns the summary."""
        try:
            from core.target_health import check_target_health, format_report

            health = asyncio.run(check_target_health(url))
            for line in format_report(health).splitlines():
                self.log_safe(line)
            self._last_target_health = health
            return health.summary()
        except Exception as exc:  # never let a probe take the app down
            self.log_safe(f"Target check failed: {exc}")
            self._last_target_health = None
            return ""

    def _target_is_broken(self, url: str) -> bool:
        """True when the target is serving a Cloudflare error page.

        A Cloudflare error such as 526 (invalid origin certificate) is created by
        Cloudflare's edge while talking to the origin: no client can bypass it, and
        every visitor - and therefore the site's own analytics - sees the error. So
        starting a campaign would only burn proxies and produce a 0% delivery. The
        campaign is refused instead, with the exact fix.
        """
        try:
            from core.target_health import check_target_health, format_report

            health = asyncio.run(check_target_health(url))
        except Exception as exc:
            self.log_safe(f"Target pre-check skipped ({exc}).")
            return False

        self._last_target_health = health
        if health.ok:
            self.log_safe(f"Target pre-check: OK (HTTP {health.status}). Starting.")
            return False

        for line in format_report(health).splitlines():
            self.log_safe(line)
        if health.is_cloudflare_error:
            self.log_safe(
                "Campaign NOT started: this error comes from the origin server, so "
                "the bot cannot get past it and nothing would be counted. Fix the "
                "target first, then press CHECK TARGET and start again."
            )
            return True
        # A 404 is the link itself being dead, expired or mistyped. Unlike a
        # timeout or a 403 (which another IP might get past), every source gets
        # the same "no such page" - so a campaign would only burn attempts and
        # show 0 delivered, which is exactly how it looks when the link has
        # expired. Stop and say so.
        if health.status == 404:
            # SmartLink-style targets sometimes answer 404 for one request and
            # serve the page on the next, so confirm before refusing to run: a
            # single 404 is not enough evidence to block a campaign.
            try:
                confirm = asyncio.run(check_target_health(url))
            except Exception:
                confirm = None
            if confirm is None or confirm.status == 404:
                self.log_safe(
                    "Campaign NOT started: the target answers 404 (page not found) "
                    "twice in a row. The link is dead, expired or mistyped - no "
                    "source can load it, so nothing would be counted. Get a fresh "
                    "link from your ad network, press CHECK TARGET, then start again."
                )
                return True
            self.log_safe(
                f"Target answered 404 once but HTTP {confirm.status} on the second "
                "try, so the campaign starts."
            )

        # Anything else (timeout, odd status) is only a warning: proxies may differ.
        self.log_safe("Target pre-check warning - starting anyway.")
        return False

    def run_async_engine(self):
        url = self.entry_url.get().strip()

        # Validate URL
        if not Utils.validate_url(url):
            self.log_safe("Invalid URL. Must start with http:// or https://")
            self.running = False
            self.after(
                0,
                lambda: self.btn_attack.configure(
                    text="START CAMPAIGN", fg_color=COLORS["success"]
                ),
            )
            return

        # Refuse to spend the proxy pool on a target that is serving a Cloudflare
        # error page (see _target_is_broken).
        if self._target_is_broken(url):
            self.running = False
            self.after(
                0,
                lambda: self.btn_attack.configure(
                    text="START CAMPAIGN", fg_color=COLORS["success"]
                ),
            )
            return

        threads = Utils.safe_int(
            self.slider_threads.get(), default=1, min_val=1, max_val=500
        )
        v_min = Utils.safe_int(
            self.slider_view_min.get(), default=5, min_val=1, max_val=300
        )
        v_max = Utils.safe_int(
            self.slider_view_max.get(), default=10, min_val=1, max_val=300
        )
        if v_min > v_max:
            v_min, v_max = v_max, v_min

        # NOTE: the "Starting ..." line is written after every check below passes,
        # so an aborted run never looks like it started.

        # Prepare Proxies
        all_active = self.proxy_grid.get_active_objects()
        allowed = []
        if self.chk_http.get():
            allowed.append("HTTP")
        if self.chk_http.get():
            allowed.append("HTTPS")
        if self.chk_socks4.get():
            allowed.append("SOCKS4")
        if self.chk_socks5.get():
            allowed.append("SOCKS5")

        engine_proxies = []
        for p in all_active:
            if any(a in p["type"] for a in allowed):
                # Convert grid dict to ProxyConfig
                # Grid item: {'ip': '1.2.3.4', 'port': '80', 'type': 'HTTP', ...}
                try:
                    proto = p["type"].lower()
                    if "socks4" in proto:
                        protocol = "socks4"
                    elif "socks5" in proto:
                        protocol = "socks5"
                    else:
                        protocol = "http"

                    creds = self._credential_map().get(f"{p['ip']}:{p['port']}", ("", ""))
                    where = self._location_map().get(f"{p['ip']}:{p['port']}", ("", ""))
                    engine_proxies.append(
                        ProxyConfig(
                            host=p["ip"],
                            port=int(p["port"]),
                            protocol=protocol,
                            username=creds[0] or None,
                            password=creds[1] or None,
                            country=where[0],
                            city=where[1],
                        )
                    )
                except (ValueError, KeyError, TypeError):
                    continue  # Skip malformed proxy entries

        # Tor as the IP source: free, no proxy list needed. The engine talks to
        # Tor's local SOCKS port and asks for a new circuit when the rotation rule
        # says so (see core/tor_manager.py).
        use_tor = str(self.settings.get("proxy_source", "list")).lower() == "tor"
        tor_manager = None
        if use_tor:
            tor_manager = self._ensure_tor()
            if tor_manager is None:
                self.running = False
                self.after(
                    0,
                    lambda: self.btn_attack.configure(
                        text="START CAMPAIGN", fg_color=COLORS["success"]
                    ),
                )
                return
            tor_settings = tor_manager.settings
            # The exit country goes on the proxy entry itself, so every per-request
            # log line can say which country the click left through.
            tor_country, tor_city = exit_ip_geo(tor_manager.status.exit_ip)
            tor_proxy = ProxyConfig(
                host=tor_settings.socks_host,
                port=int(tor_settings.socks_port),
                protocol="socks5",
                country=tor_country or "TOR",
                city=tor_city or "exit node",
            )
            engine_proxies = [tor_proxy]
            self.log_safe(
                f"IP source: Tor ({tor_settings.socks_url}) - traffic leaves through "
                f"{tor_manager.exit_label()}"
            )

        if not engine_proxies and all_active:
            self.log_safe("Warning: Proxies active but filtered by protocol.")
        elif not engine_proxies:
            # Without a proxy every request carries the user's own IP - which is
            # exactly why "my clicks show my own country" happens. Refuse unless
            # the user turned the block off on purpose.
            if bool(self.settings.get("block_direct_traffic", True)):
                self.log_safe(
                    "BLOCKED: no usable proxy, and direct traffic is disabled. "
                    "Sending traffic now would come from your own IP (that is the "
                    "'clicks show my own country' problem). Test the proxies in "
                    "Proxy Manager (untick 'Hide Dead' so you can see them) and start "
                    "again - or untick 'Block direct traffic' in Settings if you "
                    "really want direct traffic."
                )
                return
            self.log_safe("No active proxies found - running DIRECT (block is off).")

        self.active_proxy_count = len(engine_proxies)
        if engine_proxies:
            self._log_exit_ip(engine_proxies[0])

        # ---- Validate the sources against this target before spending traffic on
        # them. "Alive" is not "can deliver a counted click": a proxy that answers
        # httpbin may still be refused by this target (407, decoy page, block), and
        # ad networks refuse the public Tor exit list outright. Checking each source
        # against the real target first is what stops a campaign from running for
        # hours without a single click being counted.
        if engine_proxies:
            try:
                from core.source_check import (
                    build_proxy_url,
                    check_sources,
                    retry_source_until_it_works,
                    summarize,
                    surviving_urls,
                    usable_proxies,
                )

                source_urls = [build_proxy_url(p) for p in engine_proxies]
                source_urls = [u for u in source_urls if u]
                if source_urls:
                    verdicts = asyncio.run(
                        check_sources(url, source_urls, timeout=25, concurrency=8)
                    )

                    # Tor is a pool of exits, not one address: the destination
                    # filters some exits and accepts others (measured on this
                    # campaign: one exit in three was accepted). A single blocked
                    # exit therefore says nothing, so keep rotating until an exit
                    # works - that is what "Tor must work" actually needs.
                    tor_selected = tor_manager is not None
                    tor_source = next(
                        (u for u in source_urls if "9050" in u), ""
                    )
                    tor_verdict = next(
                        (v for v in verdicts if v.proxy and "9050" in v.proxy), None
                    )
                    if tor_selected and tor_source and tor_verdict and not tor_verdict.usable:
                        self.log_safe(
                            "Tor exit is being filtered by this target - asking Tor "
                            "for other exits before giving up (this is normal: ad "
                            "networks block part of the public exit list)."
                        )

                        def _tor_event(verdict, rotated):
                            if rotated:
                                self.log_safe(
                                    "   new Tor exit %s -> %s"
                                    % (tor_manager.exit_label(), verdict.verdict)
                                )

                        tor_verdict = asyncio.run(
                            retry_source_until_it_works(
                                url,
                                tor_source,
                                rotate=lambda: tor_manager.rotate(force=True),
                                # Two exits is enough at start-up: each attempt has
                                # to wait for Tor to issue a circuit, and the engine
                                # keeps rotating exits while the campaign runs - a
                                # four-exit pre-flight delayed the start by minutes
                                # without changing the outcome.
                                attempts=2,
                                timeout=18,
                                on_event=_tor_event,
                            )
                        )
                        verdicts = [
                            tor_verdict if (v.proxy and "9050" in v.proxy) else v
                            for v in verdicts
                        ]

                    self.log_safe(summarize(verdicts, url))
                    # Which sources may the campaign use? Pairing is positional (see
                    # core/source_check.surviving_urls) so a source that delivers a
                    # counted visit is never dropped by a string comparison.
                    passing = surviving_urls(source_urls, verdicts)
                    keep = set(usable_proxies(verdicts)) | passing
                    working = [
                        p for p in engine_proxies if build_proxy_url(p) in passing
                    ]
                    if not working:
                        # Never stop a campaign without showing exactly which
                        # sources were checked and which of them delivered, so the
                        # operator can see the decision instead of trusting it.
                        self.log_safe(
                            "   checked: %s | delivered: %s"
                            % (", ".join(source_urls) or "-", ", ".join(sorted(keep)) or "none")
                        )
                    # The direct connection is the baseline: if it reaches the
                    # destination's page, the sources - not the link - are the
                    # problem, and running anyway would just burn time.
                    direct_ok = any(v.proxy is None and v.usable for v in verdicts)
                    if working:
                        engine_proxies = working
                        self.log_safe(
                            f"Using {len(working)} source(s) that deliver this target."
                        )
                    elif direct_ok and tor_selected:
                        # Measured on this destination: proxy/VPN/Tor IPs are refused
                        # outright while a normal connection is handed the offer page
                        # and fires its tracker. When the operator has allowed direct
                        # traffic, send the campaign from this machine rather than
                        # spending the whole run on addresses that are refused.
                        if not bool(self.settings.get("block_direct_traffic", True)):
                            self.log_safe(
                                "Every Tor exit tried is refused by this destination "
                                "and your own connection is allowed, so the campaign "
                                "runs on your own connection instead (this is the "
                                "source measured to reach the offer page)."
                            )
                            engine_proxies = []
                            self.active_proxy_count = 0
                        else:
                            # Never strand a running Tor campaign here. The exit keeps
                            # rotating while it runs, so an exit this target accepts
                            # can still turn up; stopping was what made a Tor campaign
                            # look like it "gave up immediately".
                            self.log_safe(
                                "WARNING: every Tor exit tried so far is filtered by "
                                "this target, so few or none of these clicks will be "
                                "counted. The campaign keeps running and the engine "
                                "keeps rotating exits, but do not expect the full "
                                "number."
                            )
                        # Say what actually works, measured on this very target.
                        self.log_safe(
                            "   WHAT WORKS HERE: this destination hands the offer page "
                            "to a normal connection and refuses proxy/VPN/Tor IPs. So "
                            "either add residential/mobile proxies with credentials in "
                            "Proxy Manager (user:pass@ip:port), or untick 'Block direct "
                            "traffic' in Settings to let the campaign use your own "
                            "connection - that is the one source measured to reach the "
                            "offer page and fire the destination's tracker."
                        )
                    elif direct_ok:
                        self.log_safe(
                            "WARNING: proxy pre-flight check had issues reaching the page directly, "
                            "but the engine will proceed with all active configured proxies and "
                            "rotate through them."
                        )
            except Exception as exc:
                logging.debug(f"Source pre-flight skipped: {exc}")

        # Merge current UI values with persistent settings for config building
        runtime_settings = dict(self.settings)
        runtime_settings[SettingsKeys.THREADS] = threads
        runtime_settings[SettingsKeys.VIEWTIME_MIN] = v_min
        runtime_settings[SettingsKeys.VIEWTIME_MAX] = v_max
        runtime_settings[SettingsKeys.BURST_MODE] = bool(self.chk_burst_mode.get())
        runtime_settings[SettingsKeys.BURST_REQUESTS] = int(self.slider_burst_size.get())
        burst_sleep = int(self.slider_burst_sleep.get())
        runtime_settings[SettingsKeys.BURST_SLEEP_MIN] = max(1, burst_sleep // 2)
        runtime_settings[SettingsKeys.BURST_SLEEP_MAX] = burst_sleep
        # Campaign size and the per-IP click batch, straight from the dashboard.
        try:
            # Typed amount wins over the preset list (see visits_target()).
            runtime_settings[SettingsKeys.TOTAL_VISITS] = self.dashboard_page.visits_target()
            per_ip_value = self.dashboard_page.menu_clicks_per_ip.get()
            runtime_settings[SettingsKeys.CLICKS_PER_IP] = (
                1 if per_ip_value == "Every click" else int(per_ip_value)
            )
        except (AttributeError, TypeError, ValueError):
            runtime_settings.setdefault(SettingsKeys.TOTAL_VISITS, 0)
            runtime_settings.setdefault(SettingsKeys.CLICKS_PER_IP, 20)
        # Tor rotation rules (timer / how many IPs to take) come from the
        # dashboard's Tor panel.
        try:
            seconds_choice = self.dashboard_page.menu_rotate_seconds.get()
            runtime_settings[SettingsKeys.ROTATE_SECONDS] = (
                0 if seconds_choice == "Off" else int(seconds_choice.rstrip("s"))
            )
            max_choice = self.dashboard_page.menu_max_ips.get()
            runtime_settings[SettingsKeys.MAX_IPS] = (
                0 if max_choice == "Unlimited" else int(max_choice)
            )
        except (AttributeError, TypeError, ValueError):
            runtime_settings.setdefault(
                SettingsKeys.ROTATE_SECONDS, Defaults.ROTATE_SECONDS
            )
            runtime_settings.setdefault(SettingsKeys.MAX_IPS, Defaults.MAX_IPS)
        if runtime_settings.get(SettingsKeys.ROTATE_SECONDS):
            self.log_safe(
                f"IP rotation: also on a timer every "
                f"{runtime_settings[SettingsKeys.ROTATE_SECONDS]}s."
            )
        if runtime_settings.get(SettingsKeys.MAX_IPS):
            self.log_safe(
                f"IP limit: the campaign stops after "
                f"{runtime_settings[SettingsKeys.MAX_IPS]} IPs."
            )
        if runtime_settings.get(SettingsKeys.TOTAL_VISITS):
            self.log_safe(
                f"Campaign target: {runtime_settings[SettingsKeys.TOTAL_VISITS]} "
                f"delivered visits."
            )
        self.log_safe(
            f"Clicks per IP: {runtime_settings.get(SettingsKeys.CLICKS_PER_IP, 20)} - "
            f"the engine keeps one IP for that many clicks before switching."
        )

        # Build config using centralized builder (eliminates magic strings)
        config = TrafficConfigBuilder.from_settings(runtime_settings, url)

        # One Tor exit at a time. Every visit through Tor leaves by the same exit
        # IP, so parallel visits look to the destination like one address
        # hammering it - and an exit can only yield one counted click anyway.
        # Measured on this target: 0 of 9 visits delivered at 5 threads with bursts,
        # 1 of 9 with a single human-paced visit. The extra threads bought nothing
        # and cost delivery, so Tor runs one at a time.
        if tor_manager is not None:
            if getattr(config, "max_threads", 1) > 1:
                self.log_safe(
                    f"Tor sends every visit from one exit IP, so the campaign runs "
                    f"one visit at a time instead of {config.max_threads}: parallel "
                    f"visits from a single exit read as a bot and are refused."
                )
                config.max_threads = 1
                # The browser engine decides how many visits run at once from the
                # context count, not the thread count - setting only the thread count
                # left five parallel visits leaving through the same exit, which is
                # exactly the shape the destination refuses.
                try:
                    config.browser.max_contexts = 1
                except Exception:
                    pass
            if getattr(config, "burst_mode", False):
                config.burst_mode = False
                self.log_safe(
                    "Burst mode is off for Tor - a burst of requests through one "
                    "exit is an obvious pattern."
                )

        # Create session manager if persistence is enabled
        session_manager = None
        if self.settings.get("session_persistence", False):
            session_manager = SessionManager()
            self.log_safe("Session persistence enabled - cookies will be saved across runs")

        # Create engine using factory pattern (handles fallback gracefully)
        self.log(
            f"Starting engine: {threads} threads on {url}"
            + (" via Tor" if use_tor else "")
        )
        self.engine = EngineFactory.create_engine(
            mode=config.engine_mode,
            config=config,
            proxies=engine_proxies,
            on_update=self.on_engine_update,
            on_log=self.log_safe,
            session_manager=session_manager,
        )

        # Tor drives the IP rotation itself (NEWNYM on the control port).
        if tor_manager is not None:
            self.engine.rotate_hook = self._make_tor_rotate_hook(tor_manager)
            # Make the engine verify (and if need be change) the exit BEFORE its
            # first visit: otherwise the opening visits went out through an exit
            # this destination refuses, which is where the "failed" pile came from.
            try:
                self.engine._pending_rotation = True
            except Exception:
                pass
            self.log_safe(
                "Tor rotation armed: the engine will ask Tor for a new exit IP when "
                "the rotation rule is met."
            )

        # Log which engine was selected
        if config.engine_mode == EngineMode.BROWSER:
            self.log_safe("Starting Browser Engine (stealth mode)...")
        else:
            self.log_safe("Starting Fast Engine (curl)...")

        # With Tor, find an exit this destination will serve *before* spending any
        # visits on it - otherwise the opening minutes of every campaign were a run
        # of refusals that made the "failed" counter climb for nothing.
        if tor_manager is not None:
            self._ensure_accepted_tor_exit(tor_manager, url)

        # Run Async Loop
        asyncio.run(self.engine.run())

        self.running = False
        self.after(
            0,
            lambda: self.btn_attack.configure(
                text="START CAMPAIGN", fg_color=COLORS["success"]
            ),
        )
        self.log_safe("Campaign finished.")

    def reset_stats(self):
        self.stats = {"req": 0, "success": 0, "fail": 0}
        self.active_proxy_count = 0
        self.lbl_stats["req"].configure(text="0")
        self.lbl_stats["success"].configure(text="0")
        self.lbl_stats["fail"].configure(text="0")
        self._update_delivery_rate_label()
        self.log("Campaign statistics reset.")

    def on_engine_update(self, stats: TrafficStats):
        # Callback from async engine - schedule on main GUI thread for thread safety
        # Copy values to avoid race conditions (stats object may change before callback runs)
        req = stats.total_requests
        success = stats.success
        fail = stats.failed
        proxies = stats.active_proxies

        # Browser stats (browser mode only)
        browser_type = stats.browser_type
        active_contexts = stats.active_contexts
        contexts_total = stats.contexts_total

        # Captcha stats
        captcha_solved = stats.captcha_solved
        captcha_failed = stats.captcha_failed
        captcha_pending = stats.captcha_pending
        balance_2captcha = stats.captcha_balance_2captcha
        balance_anticaptcha = stats.captcha_balance_anticaptcha

        # Protection stats
        cf_detected = stats.cloudflare_detected
        cf_bypassed = stats.cloudflare_bypassed
        ak_detected = stats.akamai_detected
        ak_bypassed = stats.akamai_bypassed
        last_event = stats.last_protection_event

        def update_on_main_thread():
            # Core stats
            self.stats["req"] = req
            self.stats["success"] = success
            self.stats["fail"] = fail
            self.active_proxy_count = proxies

            # Browser stats card (browser mode only)
            if browser_type:
                self.lbl_browser_type.configure(text=browser_type)
            self.lbl_browser_contexts.configure(
                text=f"Contexts: {active_contexts}/{contexts_total}"
            )

            # Captcha stats card (solved / failed / pending)
            self.lbl_captcha_stats.configure(
                text=f"{captcha_solved} / {captcha_failed} / {captcha_pending}"
            )

            # Update individual balance labels
            if balance_2captcha >= 0:
                self.lbl_balance_2captcha.configure(
                    text=f"2Cap: ${balance_2captcha:.2f}"
                )
            if balance_anticaptcha >= 0:
                self.lbl_balance_anticaptcha.configure(
                    text=f"Anti: ${balance_anticaptcha:.2f}"
                )

            # Protection stats card (detected / bypassed)
            total_detected = cf_detected + ak_detected
            total_bypassed = cf_bypassed + ak_bypassed
            self.lbl_protection_stats.configure(
                text=f"{total_detected} / {total_bypassed}"
            )
            if last_event:
                # Truncate long event messages
                display_event = (
                    last_event[:30] + "..." if len(last_event) > 30 else last_event
                )
                self.lbl_protection_event.configure(text=display_event)

        self.after(0, update_on_main_thread)

    def _value(self, name: str, default: Any = ""):  # noqa: ANN401
        """Read a widget's value, tolerating a page that is not built yet.

        Some widgets fire their command while the app is still assembling the
        pages (a selector's .set() runs its callback), which used to log
        "Error saving settings: ... has no attribute 'chk_headless'". Missing
        widgets now simply keep their previous value.
        """
        widget = getattr(self, name, None)
        if widget is None:
            return default
        try:
            return widget.get()
        except Exception:
            return default

    # ========== EXE BUILD ==========

    def start_exe_build(self):
        """Build the distributable executable, streaming progress to the log."""
        if getattr(sys, "frozen", False):
            # Running from the packaged exe: PyInstaller is not inside the bundle,
            # so a rebuild has to happen from the source folder. Say so loudly -
            # this used to only appear in the log and looked like "nothing happens".
            message = (
                "This copy is a packaged build, so it cannot build itself.\n\n"
                "To make a new exe:\n"
                "  1. open the source folder (the one with main.py)\n"
                "  2. double-click build.bat  (or run: python build.py)\n\n"
                "The result is written to dist\\.\n"
                "On GitHub, the 'Build Windows EXE' workflow builds it for you."
            )
            self.log("Build EXE must run from the source folder - see the dialog.")
            try:
                messagebox.showinfo("Build the exe from the source folder", message)
            except Exception:
                pass
            return
        thread = getattr(self, "_build_thread", None)
        if thread is not None and thread.is_alive():
            self.log("A build is already running.")
            return

        onefile = True
        try:
            onefile = bool(self.settings_page.chk_onefile.get())
        except Exception:
            pass

        self._build_onefile = onefile
        self._last_build_path = None
        self.settings_page.set_build_state("building")
        self.select_page("settings", animate=False)
        self.log(f"Starting build ({'one file' if onefile else 'one folder'}) - this takes a few minutes.")

        def worker():
            try:
                import importlib.util

                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                spec = importlib.util.spec_from_file_location(
                    "dm_build", os.path.join(root, "build.py")
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                target = module.build_executable(log=self.log_safe, onefile=onefile)
                if target:
                    self._last_build_path = target
                    module.make_release_zip(target, log=self.log_safe)
                    self.after(0, lambda: self.settings_page.set_build_state("done", f"Built: {target}"))
                    self.after(0, lambda: self._build_popup(True, target, ""))
                else:
                    self.after(0, lambda: self.settings_page.set_build_state("failed"))
                    self.after(0, lambda: self._build_popup(False, "", "see build.log"))
            except Exception as exc:
                self.log_safe(f"Build error: {exc}")
                self.after(0, lambda: self.settings_page.set_build_state("failed", f"Build error: {exc}"))
                self.after(0, lambda: self._build_popup(False, "", str(exc)))

        self._build_thread = threading.Thread(target=worker, daemon=True)
        self._build_thread.start()

    def _build_popup(self, ok: bool, path: str, detail: str):
        """Tell the user the build outcome - a silent failure looked like a crash."""
        try:
            if ok:
                messagebox.showinfo(
                    "Build finished",
                    f"Executable created:\n{path}\n\n"
                    f"The release zip is in releases\\ and the full build output "
                    f"is in build.log.",
                )
            else:
                messagebox.showerror(
                    "Build failed",
                    f"The build did not finish.\n\n{detail}\n\n"
                    f"Details are in build.log next to main.py.",
                )
        except Exception:
            pass

    def open_dist_folder(self):
        """Open the dist/ folder in the system file manager."""
        folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist")
        if not os.path.isdir(folder):
            self.log("dist/ does not exist yet - run a build first.")
            return
        try:
            if os.name == "nt":
                os.startfile(folder)  # noqa: S606 - intended shell open
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(
                    ["xdg-open", folder],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as exc:
            self.log(f"Could not open {folder}: {exc}")

    # ========== BULK PROXY IMPORT ==========

    def open_bulk_add(self):
        """Open the bulk add window (paste box + file picker)."""
        try:
            from .bulk_add import BulkAddWindow
        except Exception as exc:
            return self.log(f"Bulk add window unavailable: {exc}")

        window = getattr(self, "bulk_window", None)
        try:
            if window is not None and window.winfo_exists():
                window.deiconify()
                window.lift()
                window.focus_force()
                return
        except Exception:
            pass

        self.bulk_window = BulkAddWindow(self)
        self.bulk_window.lift()

    def _location_map(self) -> dict:
        """host:port -> (country_code, city) from the last test results."""
        cache = getattr(self, "_location_cache", None)
        if cache is not None:
            return cache
        mapping: dict = {}
        try:
            for row in self.proxy_grid.data:
                key = f"{row.get('ip', '')}:{row.get('port', '')}"
                code = (row.get("country_code") or "").strip()
                city = (row.get("city") or "").strip()
                if code or city:
                    mapping[key] = (code, city)
        except Exception:
            pass
        self._location_cache = mapping
        return mapping

    def _log_exit_ip(self, proxy) -> None:
        """State where traffic really exits: through the proxy, or from home.

        One request to an IP-echo service *through the proxy* and the same one
        direct is the only honest answer to "which country do the clicks come
        from" - so the campaign prints both before the first visit.
        """
        def worker():
            url = "https://api.ipify.org?format=json"
            try:
                import requests

                direct = str(getattr(self, "real_ip", "") or "").strip() or "unknown"
            except Exception:
                direct = "unknown"

            try:
                import requests

                scheme = "http" if str(getattr(proxy, "protocol", "http")) in ("http", "https") else proxy.protocol
                auth = f"{proxy.username}:{proxy.password}@" if getattr(proxy, "username", None) else ""
                proxy_url = f"{scheme}://{auth}{proxy.host}:{proxy.port}"
                label = proxy.label().lstrip(" ·") if hasattr(proxy, "label") else ""
                response = requests.get(
                    url, proxies={"http": proxy_url, "https": proxy_url}, timeout=20
                )
                try:
                    exit_ip = str(response.json().get("ip", "")).strip()
                except Exception:
                    exit_ip = response.text.strip()
                if not exit_ip:
                    self.log_safe(f"[exit-ip] no answer through {proxy.host}:{proxy.port}")
                    return
                if exit_ip == direct:
                    self.log_safe(
                        f"LEAK WARNING: through {proxy.host}:{proxy.port} the exit IP is {exit_ip}, "
                        f"the same as your own IP ({direct}) - that traffic is NOT proxied!"
                    )
                else:
                    self.log_safe(
                        f"[exit-ip] {proxy.label().lstrip(' ·') or 'proxy'} {proxy.host}:{proxy.port} "
                        f"-> exit {exit_ip} ({exit_ip_country(exit_ip) or 'country unknown'}) (your own IP: {direct})"
                    )
            except Exception as exc:
                self.log_safe(f"[exit-ip] could not verify through {proxy.host}:{proxy.port}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _credential_map(self) -> dict:
        """host:port -> (username, password) for the raw proxy list.

        The engines rebuild ProxyConfig objects from result dictionaries that do
        not carry credentials, so they are looked up here instead of being lost.
        """
        cache = getattr(self, "_creds_cache", None)
        if cache is not None:
            return cache
        mapping: dict = {}
        try:
            from core.proxy_parser import parse_entry

            for raw in self.proxies:
                record = parse_entry(raw, "http")
                if record and (record.get("user") or record.get("password")):
                    mapping[f"{record['ip']}:{record['port']}"] = (
                        record.get("user") or "",
                        record.get("password") or "",
                    )
        except Exception:
            pass
        self._creds_cache = mapping
        return mapping

    def _proxy_keys(self) -> set:
        """Set of "host:port" already known, so re-imports stay cheap.

        Kept on the app and reused by every import: rebuilding it from a
        million-row grid on each import would be the slowest part of the job.
        """
        keys = getattr(self, "_proxy_key_cache", None)
        if keys is None:
            keys = set()
            for row in self.proxy_grid.data:
                keys.add(f"{row.get('ip', '')}:{row.get('port', '')}")
            self._proxy_key_cache = keys
        return keys

    def cancel_bulk_import(self):
        """Ask the running bulk import to stop (it stops within one batch)."""
        event = getattr(self, "_bulk_cancel", None)
        if event is not None:
            event.set()
            self.log("Cancelling the import ...")

    def start_bulk_import(self, options: dict, window=None):
        """Add any number of proxies at once, on a worker thread."""
        if getattr(self, "_bulk_thread", None) is not None and self._bulk_thread.is_alive():
            self.log("A bulk import is already running.")
            return

        self._bulk_cancel = threading.Event()
        cancel = self._bulk_cancel

        # Both of these must happen here, on the UI thread: the worker reads the
        # key cache and having it swapped out underneath would let duplicates in.
        if options.get("replace"):
            self._bulk_reset()
            self.log("Bulk import: replacing the current list.")
        keys = self._proxy_keys()

        def report(text: str):
            self.log_safe(text)

        def worker():
            from core import proxy_parser

            path = options.get("path") or ""
            text = options.get("text") or ""
            default_protocol = options.get("default_protocol") or proxy_parser.DEFAULT_PROTOCOL
            max_items = int(options.get("max_items") or 0)

            # estimated line count, only used for the progress bar
            if path:
                total = proxy_parser.count_lines(path)
                lines = proxy_parser.iter_lines_from_file(path)
                source = os.path.basename(path)
            else:
                total = text.count("\n") + 1 if text else 0
                lines = proxy_parser.iter_lines_from_text(text)
                source = "clipboard/paste"

            added_total = 0

            def on_batch(records, stats):
                nonlocal added_total
                added_total = stats.added
                self.after(0, lambda: self._bulk_ingest(records))
                if total:
                    fraction = min(1.0, stats.read / max(1, total))
                    message = f"{stats.added:,} added - {fraction * 100:.0f}%"
                else:
                    message = f"{stats.added:,} added"
                if window is not None:
                    self.after(0, lambda: window.set_progress(fraction if total else 0.0, message))

            try:
                stats = proxy_parser.parse_lines(
                    lines,
                    default_protocol=default_protocol,
                    seen_keys=keys,
                    skip_duplicates=bool(options.get("skip_duplicates", True)),
                    max_items=max_items,
                    cancel=cancel,
                    on_batch=on_batch,
                )
            except Exception as exc:
                self.log_safe(f"Bulk import failed: {exc}")
                if window is not None:
                    self.after(0, lambda: window.finish(f"Import failed: {exc}", failed=True))
                return

            self.after(0, self._bulk_finish, stats, source, options, window)

        self._bulk_thread = threading.Thread(target=worker, daemon=True)
        self._bulk_thread.start()

    def _bulk_reset(self):
        """Empty the grid, the string list and the dedupe cache."""
        self.proxy_grid.clear()
        self.proxy_grid.set_country_filter(None)
        self.proxies = []
        self._proxy_key_cache = set()
        self._creds_cache = None
        self._location_cache = None

    def _bulk_ingest(self, records: list):
        """Insert one parsed batch into the grid and the campaign list (UI thread)."""
        grid = self.proxy_grid
        proxies = self.proxies
        for record in records:
            protocol = record["type"]
            host, port = record["ip"], record["port"]
            grid.add(
                {
                    "ip": host,
                    "port": port,
                    "type": protocol,
                    "country": "",
                    "country_code": "",
                    "city": "",
                    "status": "Pending",
                    "speed": 0,
                    "anonymity": "Unknown",
                }
            )
            if record.get("user"):
                proxies.append(f"{protocol}://{record['user']}:{record['password']}@{host}:{port}")
            else:
                proxies.append(f"{protocol}://{host}:{port}")
        grid.flush()
        self.update_filter_status()

    def _bulk_finish(self, stats, source, options, window):
        """Report the result, refresh the stats and optionally start testing."""
        self.update_proxy_stats()
        self.refresh_country_options(force=True)
        summary = f"{source}: {stats.as_line()}"
        self.log(f"Bulk import - {summary}")
        if stats.invalid_examples:
            self.log(f"  ignored (e.g.): {', '.join(stats.invalid_examples[:3])}")
        if window is not None:
            head = "Cancelled - " if stats.cancelled else ""
            try:
                window.finish(f"{head}{stats.added:,} proxies added ({stats.duplicates:,} dup, {stats.invalid:,} invalid)")
            except Exception:
                pass
        if options.get("test_after") and stats.added and not stats.cancelled:
            self.log("Starting the test run for the new proxies ...")
            self.toggle_test()

    def save_cfg(self):
        try:
            if not self._pages_ready:
                # a widget fired its command while the pages were still being
                # assembled (a selector's .set() runs its handler); nothing to save
                return
            target_url = self._value("entry_url").strip()
            test_url = self._value("entry_test_url").strip()

            # Validate URLs before saving
            if target_url and not Utils.validate_url(target_url):
                self.log("Warning: Target URL appears invalid")

            if test_url and not Utils.validate_url(test_url):
                self.log("Warning: Test URL appears invalid")

            self.settings["target_url"] = target_url
            self.settings["threads"] = Utils.safe_int(
                self._value("slider_threads"), default=5, min_val=1, max_val=500
            )
            self.settings["viewtime_min"] = Utils.safe_int(
                self._value("slider_view_min"), default=5, min_val=1, max_val=300
            )
            self.settings["viewtime_max"] = Utils.safe_int(
                self._value("slider_view_max"), default=10, min_val=1, max_val=300
            )
            # Burst mode settings
            self.settings["burst_mode"] = bool(self._value("chk_burst_mode"))
            # Campaign size + per-IP click batch (dashboard selectors)
            try:
                self.settings["total_visits"] = self.dashboard_page.visits_target()
                per_ip_choice = self.dashboard_page.menu_clicks_per_ip.get()
                self.settings["clicks_per_ip"] = (
                    1 if per_ip_choice == "Every click" else int(per_ip_choice)
                )
            except (AttributeError, TypeError, ValueError):
                self.settings.setdefault("total_visits", 0)
                self.settings.setdefault("clicks_per_ip", 20)
            # IP source + Tor rotation settings
            try:
                source_choice = self.dashboard_page.menu_proxy_source.get()
                self.settings["proxy_source"] = (
                    "tor" if source_choice.startswith("Tor") else "list"
                )
                seconds_choice = self.dashboard_page.menu_rotate_seconds.get()
                self.settings["rotate_interval_seconds"] = (
                    0 if seconds_choice == "Off" else int(seconds_choice.rstrip("s"))
                )
                max_choice = self.dashboard_page.menu_max_ips.get()
                self.settings["max_ip_rotations"] = (
                    0 if max_choice == "Unlimited" else int(max_choice)
                )
                self.settings["tor_socks_port"] = int(
                    self.dashboard_page.entry_tor_socks.get() or 9050
                )
                self.settings["tor_control_port"] = int(
                    self.dashboard_page.entry_tor_control.get() or 9051
                )
            except (AttributeError, TypeError, ValueError):
                self.settings.setdefault("proxy_source", "list")
                self.settings.setdefault("rotate_interval_seconds", 0)
                self.settings.setdefault("max_ip_rotations", 0)
                self.settings.setdefault("tor_socks_port", 9050)
                self.settings.setdefault("tor_control_port", 9051)
            # never send traffic from the user's own IP unless they say so
            self.settings["block_direct_traffic"] = bool(
                self._value("chk_block_direct", True)
            )
            self.settings["burst_requests"] = Utils.safe_int(
                self._value("slider_burst_size"), default=10, min_val=5, max_val=50
            )
            self.settings["burst_sleep_max"] = Utils.safe_int(
                self._value("slider_burst_sleep"), default=5, min_val=1, max_val=30
            )
            self.settings["burst_sleep_min"] = max(
                1, self.settings["burst_sleep_max"] // 2
            )
            self.settings["proxy_test_url"] = test_url
            self.settings["proxy_timeout"] = Utils.safe_int(
                self._value("entry_timeout"), default=3000, min_val=100, max_val=30000
            )
            self.settings["proxy_check_threads"] = Utils.safe_int(
                self._value("entry_check_threads"), default=50, min_val=1, max_val=10000
            )
            self.settings["proxy_scrape_threads"] = Utils.safe_int(
                self._value("entry_scrape_threads"), default=20, min_val=1, max_val=100
            )
            self.settings["system_proxy"] = self._value("entry_system_proxy").strip()
            self.settings["system_proxy_protocol"] = self._value("combo_system_proxy_proto")
            self.settings["use_proxy_for_scrape"] = self._value("chk_proxy_for_scrape")
            self.settings["use_proxy_for_check"] = self._value("chk_proxy_for_check")
            self.settings["use_manual_target"] = self._value("chk_manual_target")
            self.settings["use_http"] = self._value("chk_http")
            self.settings["use_socks4"] = self._value("chk_socks4")
            self.settings["use_socks5"] = self._value("chk_socks5")
            self.settings["hide_dead"] = self._value("chk_hide_dead")
            self.settings["headless"] = self._value("chk_headless")
            self.settings["verify_ssl"] = self._value("chk_verify_ssl")
            self.settings["session_persistence"] = self._value("chk_session_persist")

            # Engine mode (from dashboard selector)
            mode_value = self._value("mode_selector")
            self.settings["engine_mode"] = (
                "browser" if "Browser" in mode_value else "curl"
            )

            # Browser settings - individual paths
            self.settings["browser_selected"] = self._value("combo_browser").lower()
            self.settings["browser_chrome_path"] = self._value("entry_chrome_path").strip()
            self.settings["browser_chromium_path"] = (
                self._value("entry_chromium_path").strip()
            )
            self.settings["browser_edge_path"] = self._value("entry_edge_path").strip()
            self.settings["browser_brave_path"] = self._value("entry_brave_path").strip()
            self.settings["browser_firefox_path"] = (
                self._value("entry_firefox_path").strip()
            )
            self.settings["browser_other_path"] = self._value("entry_other_path").strip()
            self.settings["browser_contexts"] = Utils.safe_int(
                self._value("slider_contexts"), default=5, min_val=1, max_val=10
            )
            self.settings["browser_stealth"] = self._value("chk_stealth")
            self.settings["browser_headless"] = self._value("chk_headless")

            # Captcha settings - dual provider support
            captcha_primary = self._value("combo_captcha_primary").lower()
            self.settings["captcha_primary"] = (
                "none" if captcha_primary == "none" else captcha_primary
            )
            self.settings["captcha_2captcha_key"] = (
                self._value("entry_2captcha_key").strip()
            )
            self.settings["captcha_anticaptcha_key"] = (
                self._value("entry_anticaptcha_key").strip()
            )
            self.settings["captcha_fallback_enabled"] = self._value("chk_captcha_fallback")

            # Protection bypass settings
            self.settings["cloudflare_bypass"] = self._value("chk_cloudflare")
            self.settings["akamai_bypass"] = self._value("chk_akamai")
            self.settings["auto_solve_captcha"] = self._value("chk_auto_captcha")
            self.settings["cloudflare_wait"] = Utils.safe_int(
                self._value("entry_cf_wait"), default=10, min_val=1, max_val=60
            )

            # Validator settings
            self.settings["validator_test_depth"] = self._value("combo_test_depth").lower()
            validators_enabled = {}
            for name, chk in self.validator_checkboxes.items():
                validators_enabled[name] = chk.get()
            self.settings["validators_enabled"] = validators_enabled

            Utils.save_settings(self.settings)
            self.log("Settings saved.")
        except Exception as e:
            self.log(f"Error saving settings: {e}")

    def _update_delivery_rate_label(self):
        """Show the share of attempts that actually reached the target.

        This is the number that should match the target's own visitor stats. It
        is kept next to Attempts/Delivered so a low delivery rate (dead or
        blocked proxies) is obvious instead of hiding behind a big attempt count.
        """
        label = self.lbl_stats.get("rate")
        if label is None:
            return
        attempts = int(self.stats.get("req", 0) or 0)
        delivered = int(self.stats.get("success", 0) or 0)
        if attempts <= 0:
            label.configure(text="--", text_color=COLORS["accent_2"])
            return
        rate = delivered / attempts * 100
        if rate >= 70:
            color = COLORS["success"]
        elif rate >= 35:
            color = COLORS["warning"]
        else:
            color = COLORS["danger"]
        label.configure(text=f"{rate:.0f}%", text_color=color)

    def update_gui_loop(self):
        # Drain up to 100 items from the thread-safe queue (batch processing)
        items_processed = 0
        while items_processed < 100:
            try:
                item = self.buffer.get_nowait()
                self.proxy_grid.add(item)  # Deferred - no draw yet
                items_processed += 1
            except Empty:
                break

        # Flush grid changes (single draw for all items added this cycle)
        if items_processed > 0:
            self.proxy_grid.flush()
            self.refresh_country_options()

        # Throttle stats update - only every 500ms to reduce iteration overhead
        if not hasattr(self, "_last_stats_update"):
            self._last_stats_update = 0
        now = time.time()
        if items_processed > 0 and (now - self._last_stats_update >= 0.5):
            self.update_proxy_stats()
            self._last_stats_update = now

        self.lbl_stats["req"].configure(text=str(self.stats["req"]))
        self.lbl_stats["success"].configure(text=str(self.stats["success"]))
        self.lbl_stats["fail"].configure(text=str(self.stats["fail"]))
        self.lbl_stats["proxies"].configure(text=str(self.active_proxy_count))
        self._update_delivery_rate_label()
        # "Delivered 250 / 1000 (25%)" under the amount selector
        try:
            self.dashboard_page.update_visit_progress()
        except Exception:
            pass

        self._update_header_status()

        self.after(100, self.update_gui_loop)
