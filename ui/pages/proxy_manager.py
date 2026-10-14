"""Proxy Manager page - scraping, testing, filtering and export.

Four grouped cards, top to bottom: country filter -> proxies/actions ->
tester & system proxy -> live stats, then the grid fills the rest of the page.

The country picker is the first control on the page: one click opens the list
of countries found in the grid, one more filters the view to that country (the
rows cascade in). Filtering only hides rows - nothing is ever deleted, and
``Reset`` brings the whole list back.
"""

from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ..components import CountrySelect, SectionCard, StatCard, VirtualGrid
from ..scaling import get_scaling_factor, scaled
from ..styles import COLORS, RADIUS, font

if TYPE_CHECKING:
    from ..app import ModernTrafficBot


class ProxyManagerPage:
    """Proxy Manager page for proxy operations.

    Handles:
    - Proxy scraping from sources
    - Proxy testing/validation
    - Import/export operations
    - Protocol filtering
    - Country filtering (click-to-open picker)
    - System proxy configuration
    """

    def __init__(self, app: "ModernTrafficBot"):
        """Initialize the proxy manager page.

        Args:
            app: The main application instance for shared state access.
        """
        self.app = app

        # UI widget references
        self.proxy_grid: VirtualGrid = None
        self.progress_bar: ctk.CTkProgressBar = None
        self.lbl_loaded: ctk.CTkLabel = None
        self.lbl_proto_counts: ctk.CTkLabel = None
        self.lbl_bandwidth: ctk.CTkLabel = None
        self.lbl_anon_counts: ctk.CTkLabel = None
        self.entry_test_url: ctk.CTkEntry = None
        self.entry_timeout: ctk.CTkEntry = None
        self.entry_check_threads: ctk.CTkEntry = None
        self.entry_scrape_threads: ctk.CTkEntry = None
        self.entry_system_proxy: ctk.CTkEntry = None
        self.combo_system_proxy_proto: ctk.CTkComboBox = None
        self.chk_http: ctk.CTkCheckBox = None
        self.chk_socks4: ctk.CTkCheckBox = None
        self.chk_socks5: ctk.CTkCheckBox = None
        self.chk_hide_dead: ctk.CTkCheckBox = None
        self.chk_manual_target: ctk.CTkCheckBox = None
        self.chk_proxy_for_scrape: ctk.CTkCheckBox = None
        self.chk_proxy_for_check: ctk.CTkCheckBox = None
        self.chk_export_protocol: ctk.CTkCheckBox = None
        self.btn_test: ctk.CTkButton = None
        self.btn_pause: ctk.CTkButton = None
        self.btn_test_proxy: ctk.CTkButton = None
        # country filter
        self.country_select: CountrySelect = None
        self.lbl_filter_status: ctk.CTkLabel = None
        self.btn_clear_filter: ctk.CTkButton = None
        self.stat_cards: dict[str, StatCard] = {}

    @property
    def settings(self) -> dict[str, Any]:
        """Access app settings."""
        return self.app.settings

    # ------------------------------------------------------------------
    # small builders (keeps the layout code readable)
    # ------------------------------------------------------------------
    def _label(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent, text=text, font=font(scaled(10)), text_color=COLORS["text_mute"]
        )

    def _entry(self, parent, width: int, placeholder: str = "") -> ctk.CTkEntry:
        return ctk.CTkEntry(
            parent,
            width=scaled(width),
            height=scaled(30),
            corner_radius=RADIUS["md"],
            fg_color=COLORS["field"],
            border_color=COLORS["border"],
            placeholder_text=placeholder,
            font=font(scaled(11)),
        )

    def _check(self, parent, text: str, width: int, tone: str = "accent") -> ctk.CTkCheckBox:
        return ctk.CTkCheckBox(
            parent,
            text=text,
            width=scaled(width),
            height=scaled(24),
            checkbox_width=scaled(16),
            checkbox_height=scaled(16),
            corner_radius=RADIUS["sm"],
            border_color=COLORS["border"],
            fg_color=COLORS[tone],
            hover_color=COLORS["accent_hover"] if tone == "accent" else "#B93E52",
            font=font(scaled(11)),
        )

    def _button(self, parent, text: str, width: int, tone: str, command, **kw) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=text,
            width=scaled(width),
            height=scaled(32),
            corner_radius=RADIUS["md"],
            fg_color=COLORS[tone],
            hover_color=COLORS["accent_hover"],
            font=font(scaled(11)),
            command=command,
            **kw,
        )

    def setup(self, parent: ctk.CTkFrame) -> None:
        """Set up the proxy manager UI.

        Args:
            parent: The parent frame to build the UI in.
        """
        # Scrollable toolbar container for small screens
        tools_scroll = ctk.CTkScrollableFrame(
            parent,
            fg_color="transparent",
            scrollbar_button_color=COLORS["surface_2"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        tools_scroll.pack(fill="x", pady=(0, 5))
        tools_scroll.configure(height=scaled(380))
        # keep the toolbar in proportion when the window is resized (see
        # _on_page_resize) - the grid below always keeps a usable share
        self._tools_scroll = tools_scroll
        self._tools_height = scaled(380)
        parent.bind("<Configure>", self._on_page_resize)

        # Build the four groups
        self._setup_country_filter(tools_scroll)
        self._setup_proxies_card(tools_scroll)
        self._setup_stats_row(tools_scroll)
        self._setup_test_config(tools_scroll)

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(
            parent,
            height=scaled(6),
            progress_color=COLORS["accent"],
            fg_color=COLORS["bg"],
            corner_radius=RADIUS["pill"],
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(0, 5))

        # Proxy grid
        self.proxy_grid = VirtualGrid(
            parent, columns=["Address", "Proto", "Country", "Status", "Ping", "Anon"]
        )
        self.proxy_grid.pack(fill="both", expand=True)

    def _on_page_resize(self, event=None):
        """Split the page between the tool cards and the proxy grid.

        The tool cards live in a scrollable frame, so they can safely give space
        back; the grid cannot - it is the list the user came here to read. The
        old rule handed the cards a flat 66% of the page, which pushed the grid
        down to a couple of visible rows on smaller windows and on high-DPI
        screens (where every widget is drawn larger but the window is not).

        The grid now keeps a fixed slice of the page, expressed in CTk units so
        the number of visible rows stays the same at every DPI setting.
        """
        height = getattr(event, "height", 0) or 0
        if height <= 0:
            return

        factor = get_scaling_factor() or 1.0
        logical_height = height / factor  # page height in CTk units
        # Reserve room for the grid (plus the progress bar) and give the cards
        # whatever is left, clamped so neither side collapses.
        target = int(
            min(scaled(442), max(scaled(150), logical_height - scaled(300)))
        )
        if abs(target - self._tools_height) < 6:
            return
        self._tools_height = target
        try:
            self._tools_scroll.configure(height=target)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 1. Country filter  (click -> list opens -> pick -> grid filters)
    # ------------------------------------------------------------------
    def _setup_country_filter(self, parent: ctk.CTkFrame) -> None:
        """Set up the click-to-open country picker and the filter status line."""
        card = SectionCard(parent, "Country filter")
        card.pack(fill="x", pady=(0, 6))

        row = ctk.CTkFrame(card.body, fg_color="transparent")
        row.pack(fill="x")

        self.country_select = CountrySelect(
            row, on_pick=self.app.on_country_picked, width=scaled(220)
        )
        self.country_select.pack(side="left")

        self.btn_clear_filter = ctk.CTkButton(
            row,
            text="Reset",
            width=scaled(72),
            height=scaled(34),
            corner_radius=RADIUS["md"],
            fg_color="transparent",
            hover_color=COLORS["surface_2"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text_mute"],
            font=font(scaled(11)),
            command=self.app.clear_country_filter,
        )
        self.btn_clear_filter.pack(side="left", padx=8)

        self.lbl_filter_status = ctk.CTkLabel(
            row,
            text="",
            text_color=COLORS["text_dim"],
            font=font(scaled(11)),
            anchor="w",
        )
        self.lbl_filter_status.pack(side="left", fill="x", expand=True, padx=(4, 0))

    # ------------------------------------------------------------------
    # 2. Actions + protocol filters
    # ------------------------------------------------------------------
    def _setup_proxies_card(self, parent: ctk.CTkFrame) -> None:
        """Actions row plus the protocol / hide-dead filters in one card."""
        card = SectionCard(parent, "Proxies")
        card.pack(fill="x", pady=(0, 6))

        r1 = ctk.CTkFrame(card.body, fg_color="transparent")
        r1.pack(fill="x")

        self._button(
            r1, "Bulk Add", 104, "accent_2", self.app.open_bulk_add, text_color="#04252B"
        ).pack(side="left", padx=(0, 6))

        for text, width, command in [
            ("Scrape New", 100, self.app.run_scraper),
            ("Load File", 92, self.app.load_proxy_file),
            ("Clipboard", 82, self.app.import_from_clipboard),
        ]:
            self._button(r1, text, width, "accent", command).pack(side="left", padx=(0, 6))

        self._button(
            r1, "Clear Dead", 88, "warning", self.app.clear_dead_proxies,
            text_color="#1A1206",
        ).pack(side="right", padx=(6, 0))
        self._button(
            r1, "Clear All", 78, "danger", self.app.clear_proxies, text_color="#2A0A11"
        ).pack(side="right", padx=(6, 0))

        self._setup_export_buttons(r1)

        proto = ctk.CTkFrame(card.body, fg_color="transparent")
        proto.pack(fill="x", pady=(8, 0))

        self.chk_http = self._check(proto, "HTTP/S", 80)
        if self.settings.get("use_http", True):
            self.chk_http.select()
        self.chk_http.pack(side="left", padx=(0, 14))

        self.chk_socks4 = self._check(proto, "SOCKS4", 80)
        if self.settings.get("use_socks4", True):
            self.chk_socks4.select()
        self.chk_socks4.pack(side="left", padx=(0, 14))

        self.chk_socks5 = self._check(proto, "SOCKS5", 80)
        if self.settings.get("use_socks5", True):
            self.chk_socks5.select()
        self.chk_socks5.pack(side="left", padx=(0, 14))

        self.chk_hide_dead = self._check(proto, "Hide Dead", 84, tone="danger")
        if self.settings.get("hide_dead", True):
            self.chk_hide_dead.select()
        self.chk_hide_dead.pack(side="right")

    def _setup_export_buttons(self, parent: ctk.CTkFrame) -> None:
        """Set up the export buttons."""
        export_frame = ctk.CTkFrame(parent, fg_color="transparent")
        export_frame.pack(side="right", padx=(0, 8))

        self._label(export_frame, "Export").pack(side="left", padx=(0, 4))

        self.chk_export_protocol = self._check(export_frame, "Proto", 54)
        if self.settings.get("export_with_protocol", True):
            self.chk_export_protocol.select()
        self.chk_export_protocol.pack(side="left", padx=(0, 4))

        for text, width, tone, kind in [
            ("All", 34, "warning", "all"),
            ("HTTP", 42, "info", "http"),
            ("HTTPS", 48, "accent", "https"),
            ("SOCKS", 48, "accent_2", "socks"),
        ]:
            ctk.CTkButton(
                export_frame,
                text=text,
                width=scaled(width),
                height=scaled(24),
                corner_radius=RADIUS["sm"],
                fg_color=COLORS[tone],
                hover_color=COLORS["accent_hover"],
                text_color="#08111A",
                font=font(scaled(10), "bold"),
                command=lambda k=kind: self.app.export_proxies(k),
            ).pack(side="left", padx=1)

    # ------------------------------------------------------------------
    # 3. Live stats
    # ------------------------------------------------------------------
    def _stat_text(
        self, parent: ctk.CTkFrame, column: int, caption: str, value: str, tone: str
    ) -> ctk.CTkLabel:
        """A caption/value pair for the long protocol and anonymity lines.

        These two strings are ~40 characters wide, so they get a wide text column
        instead of a metric tile (a tile clips them to "P: 8 | HTTPS: ...").
        """
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.grid(row=0, column=column, sticky="ew", padx=(4, 0))
        ctk.CTkLabel(
            group,
            text=caption,
            font=font(scaled(9)),
            text_color=COLORS["text_mute"],
            anchor="w",
        ).pack(fill="x")
        value_label = ctk.CTkLabel(
            group,
            text=value,
            font=font(scaled(11), "bold"),
            text_color=COLORS[tone],
            anchor="w",
        )
        value_label.pack(fill="x")
        return value_label

    def _setup_stats_row(self, parent: ctk.CTkFrame) -> None:
        """Metric tiles plus two full-width text lines.

        The protocol/anonymity strings are ~45 characters; on a high-DPI Windows
        screen they no longer fit next to two tiles, so they get a row of their
        own instead of being squeezed into a narrow column (which clipped them).
        """
        card = SectionCard(parent, "Live stats")
        card.pack(fill="x", pady=(0, 6))

        tiles = ctk.CTkFrame(card.body, fg_color="transparent")
        tiles.pack(fill="x")

        loaded = StatCard(tiles, "TOTAL LOADED", "0", "text")
        loaded.pack(side="left", fill="x", expand=True, padx=(0, 6))
        traffic = StatCard(tiles, "TRAFFIC", "0.00 Mbps", "success")
        traffic.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.stat_cards["loaded"] = loaded
        self.stat_cards["bandwidth"] = traffic

        # app.py updates these labels with .configure(text=...) - they ARE the
        # value labels, so one assignment feeds the tile and the line alike.
        self.lbl_loaded = loaded.value
        self.lbl_bandwidth = traffic.value

        self.lbl_proto_counts = ctk.CTkLabel(
            card.body,
            text="HTTP: 0 | HTTPS: 0 | SOCKS4: 0 | SOCKS5: 0",
            font=font(scaled(10), "bold"),
            text_color=COLORS["info"],
            anchor="w",
        )
        self.lbl_proto_counts.pack(fill="x", pady=(6, 0))

        self.lbl_anon_counts = ctk.CTkLabel(
            card.body,
            text="Elite: 0 | Anonymous: 0 | Transparent: 0 | Unknown: 0",
            font=font(scaled(10), "bold"),
            text_color=COLORS["accent_glow"],
            anchor="w",
        )
        self.lbl_anon_counts.pack(fill="x", pady=(2, 0))

    # ------------------------------------------------------------------
    # 4. Tester + system proxy
    # ------------------------------------------------------------------
    def _setup_test_config(self, parent: ctk.CTkFrame) -> None:
        """Test configuration and the system-proxy row in one card."""
        card = SectionCard(parent, "Tester & system proxy")
        card.pack(fill="x", pady=(0, 4))

        # ---- tester -----------------------------------------------------
        r2 = ctk.CTkFrame(card.body, fg_color="transparent")
        r2.pack(fill="x")

        r2_left = ctk.CTkFrame(r2, fg_color="transparent")
        r2_left.pack(side="left", fill="x", expand=True)

        self.chk_manual_target = self._check(r2_left, "Manual", 74)
        if self.settings.get("use_manual_target", True):
            self.chk_manual_target.select()
        self.chk_manual_target.configure(command=self.app.toggle_manual_target)
        self.chk_manual_target.pack(side="left", padx=(0, 6))

        self.entry_test_url = self._entry(r2_left, 200, "Test URL (or use validators)")
        self.entry_test_url.configure(width=scaled(200))
        self.entry_test_url.insert(0, self.settings.get("proxy_test_url", ""))
        self.entry_test_url.pack(side="left", fill="x", expand=True, padx=(0, 8))

        for label, attr, default in [
            ("Timeout", "entry_timeout", self.settings.get("proxy_timeout", 3000)),
            ("Threads", "entry_check_threads", self.settings.get("proxy_check_threads", 50)),
            ("Scrape", "entry_scrape_threads", self.settings.get("proxy_scrape_threads", 20)),
        ]:
            self._label(r2_left, label).pack(side="left", padx=(0, 3))
            entry = self._entry(r2_left, 52)
            entry.insert(0, str(default))
            entry.pack(side="left", padx=(0, 8))
            setattr(self, attr, entry)

        r2_right = ctk.CTkFrame(r2, fg_color="transparent")
        r2_right.pack(side="right")

        self.btn_pause = ctk.CTkButton(
            r2_right,
            text="PAUSE",
            width=scaled(84),
            height=scaled(32),
            corner_radius=RADIUS["md"],
            fg_color=COLORS["surface_2"],
            hover_color=COLORS["surface"],
            text_color=COLORS["text_dim"],
            font=font(scaled(11), "bold"),
            command=self.app.toggle_pause_test,
            state="disabled",
        )
        self.btn_pause.pack(side="left", padx=(0, 6))

        # live test progress: which country/proxy is being checked right now
        self.lbl_test_status = ctk.CTkLabel(
            card.body,
            text="No test running.",
            font=font(scaled(10)),
            text_color=COLORS["text_dim"],
            anchor="w",
            justify="left",
        )
        self.lbl_test_status.pack(fill="x", pady=(6, 0))

        self.btn_test = ctk.CTkButton(
            r2_right,
            text="TEST ALL",
            width=scaled(94),
            height=scaled(32),
            corner_radius=RADIUS["md"],
            fg_color=COLORS["success"],
            hover_color="#2BB586",
            text_color="#04170F",
            font=font(scaled(11), "bold"),
            command=self.app.toggle_test,
        )
        self.btn_test.pack(side="left")

        # ---- system proxy ----------------------------------------------
        r3 = ctk.CTkFrame(card.body, fg_color="transparent")
        r3.pack(fill="x", pady=(8, 0))

        self._label(r3, "System proxy").pack(side="left", padx=(0, 6))

        self.combo_system_proxy_proto = ctk.CTkComboBox(
            r3,
            values=["http", "socks4", "socks5"],
            width=scaled(86),
            height=scaled(30),
            corner_radius=RADIUS["md"],
            fg_color=COLORS["field"],
            border_color=COLORS["border"],
            button_color=COLORS["surface_2"],
            button_hover_color=COLORS["accent"],
            font=font(scaled(11)),
            dropdown_font=font(scaled(11)),
        )
        self.combo_system_proxy_proto.set(
            self.settings.get("system_proxy_protocol", "http")
        )
        self.combo_system_proxy_proto.pack(side="left", padx=(0, 6))

        self.btn_test_proxy = ctk.CTkButton(
            r3,
            text="TEST",
            width=scaled(58),
            height=scaled(30),
            corner_radius=RADIUS["md"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=font(scaled(11), "bold"),
            command=self.app.test_system_proxy,
        )
        self.btn_test_proxy.pack(side="right", padx=(6, 0))

        self.chk_proxy_for_check = self._check(r3, "Check", 66)
        if self.settings.get("use_proxy_for_check", False):
            self.chk_proxy_for_check.select()
        self.chk_proxy_for_check.pack(side="right", padx=(4, 0))

        self.chk_proxy_for_scrape = self._check(r3, "Scrape", 74)
        if self.settings.get("use_proxy_for_scrape", False):
            self.chk_proxy_for_scrape.select()
        self.chk_proxy_for_scrape.pack(side="right", padx=(4, 0))

        self.entry_system_proxy = self._entry(r3, 160, "user:pass@ip:port")
        self.entry_system_proxy.insert(0, self.settings.get("system_proxy", ""))
        self.entry_system_proxy.pack(side="left", fill="x", expand=True, padx=(0, 6))
