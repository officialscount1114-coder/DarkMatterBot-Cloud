"""Dashboard page - Traffic campaign configuration and monitoring."""

from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ..components import DraggableSash
from ..scaling import scaled
from ..styles import COLORS, RADIUS

if TYPE_CHECKING:
    from ..app import ModernTrafficBot


class DashboardPage:
    """Dashboard page for traffic campaign control.

    Handles:
    - Campaign configuration (URL, threads, duration)
    - Engine mode selection (curl/browser)
    - Burst mode settings
    - Real-time stats display
    - Activity logging
    """

    def __init__(self, app: "ModernTrafficBot"):
        """Initialize the dashboard page.

        Args:
            app: The main application instance for shared state access.
        """
        self.app = app

        # UI widget references (populated during setup)
        self.lbl_stats: dict[str, ctk.CTkLabel] = {}
        self.entry_url: ctk.CTkEntry = None
        self.mode_selector: ctk.CTkSegmentedButton = None
        self.slider_threads: ctk.CTkSlider = None
        self.slider_view_min: ctk.CTkSlider = None
        self.slider_view_max: ctk.CTkSlider = None
        self.slider_burst_size: ctk.CTkSlider = None
        self.slider_burst_sleep: ctk.CTkSlider = None
        self.lbl_threads: ctk.CTkLabel = None
        self.lbl_viewtime: ctk.CTkLabel = None
        self.lbl_burst: ctk.CTkLabel = None
        self.chk_burst_mode: ctk.CTkCheckBox = None
        self.btn_attack: ctk.CTkButton = None
        self.btn_reset: ctk.CTkButton = None
        self.browser_stats_frame: ctk.CTkFrame = None
        self.lbl_browser_type: ctk.CTkLabel = None
        self.lbl_browser_contexts: ctk.CTkLabel = None
        self.lbl_captcha_stats: ctk.CTkLabel = None
        self.lbl_balance_2captcha: ctk.CTkLabel = None
        self.lbl_balance_anticaptcha: ctk.CTkLabel = None
        self.lbl_protection_stats: ctk.CTkLabel = None
        self.lbl_protection_event: ctk.CTkLabel = None
        self.log_box: ctk.CTkTextbox = None

        # Internal state. The config area now holds three cards (attack config,
        # campaign size, IP source), so it starts tall enough to show them without
        # a clipped row; it still scrolls, so a small window is fine too.
        self._config_height = scaled(430)
        self._config_min = scaled(170)
        # A small log floor leaves the cards more of a short window; the log is
        # still fully usable and can be dragged bigger with the sash.
        self._log_min = scaled(96)

    @property
    def settings(self) -> dict[str, Any]:
        """Access app settings."""
        return self.app.settings

    def setup(self, parent: ctk.CTkFrame) -> None:
        """Set up the dashboard UI.

        Args:
            parent: The parent frame to build the UI in.
        """
        # Use grid for scalable layout with draggable sash
        parent.grid_rowconfigure(0, weight=0)  # Scrollable config area
        parent.grid_rowconfigure(1, weight=0)  # Draggable sash
        parent.grid_rowconfigure(2, weight=1)  # Activity log expands
        parent.grid_columnconfigure(0, weight=1)

        # Scrollable container for config (enables scroll on small screens)
        config_scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        config_scroll.grid(row=0, column=0, sticky="nsew", pady=(0, 0))
        config_scroll.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=0, minsize=self._config_height)
        self._config_parent = parent
        self._config_scroll = config_scroll
        # Fit the config area once the page's real size is known, so every control
        # is visible instead of clipped. The user can still re-split with the sash.
        self._config_fitted = False
        parent.bind("<Configure>", self._on_config_first_layout, add="+")
        parent.after(1500, self._on_config_first_layout)

        # Draggable sash between config and log
        def on_sash_drag(delta):
            new_height = self._config_height + delta
            parent_height = parent.winfo_height()
            max_config = parent_height - self._log_min - scaled(10)
            new_height = max(self._config_min, min(new_height, max_config))
            if new_height != self._config_height:
                self._config_height = new_height
                parent.grid_rowconfigure(0, weight=0, minsize=int(self._config_height))

        sash = DraggableSash(
            parent,
            on_drag=on_sash_drag,
            min_above=self._config_min,
            min_below=self._log_min,
        )
        sash.grid(row=1, column=0, sticky="ew", pady=(0, 0))

        # Build UI sections
        self._setup_stats_row(config_scroll)
        self._setup_config_card(config_scroll)
        self._setup_browser_stats(config_scroll)
        self._setup_activity_log(parent)

        # Initial visibility state
        self._update_browser_stats_visibility()

    def _on_config_first_layout(self, _event=None) -> None:
        """Fit the config area as soon as the page size is known.

        Re-runs when the window is resized by a meaningful amount, so a taller
        window drops the inner scrollbar entirely instead of keeping the one the
        first layout produced. The 40px threshold stops a resize/relayout loop.
        """
        try:
            parent = self._config_parent
            height = parent.winfo_height()
            if height < scaled(250):
                return
            if getattr(self, "_config_fitted", False):
                if abs(height - getattr(self, "_fit_parent_h", 0)) <= scaled(40):
                    return
            if self._fit_config_area():
                self._config_fitted = True
                self._fit_parent_h = height
        except Exception:
            return

    def _fit_config_area(self) -> bool:
        """Size the config area to what it actually contains. Returns success.

        The area used to be clamped to a fixed height, so on a short window (615px
        on the operator's screen) the last cards were cut in half and the panel
        grew its own scrollbar - which reads as a broken, half-rendered tool. Now
        the row is given exactly the height the content asks for when the window
        has room, so no scrollbar appears at all; only a genuinely too-small
        window falls back to scrolling.
        """
        try:
            parent = self._config_parent
            height = parent.winfo_height()
            if height < scaled(250):  # still not laid out
                return False
            available = max(self._config_min, height - self._log_min - scaled(14))
            content = 0
            try:
                content = int(self._config_scroll.winfo_reqheight())
            except Exception:
                content = 0
            need = content if content > scaled(120) else scaled(690)
            target = max(self._config_min, min(need, available))
            if abs(target - self._config_height) > 4:
                self._config_height = target
                parent.grid_rowconfigure(0, weight=0, minsize=int(target))
            return True
        except Exception:
            return False

    def _setup_stats_row(self, parent: ctk.CTkFrame) -> None:
        """Set up the primary stats display row."""
        stats_frame = ctk.CTkFrame(parent, fg_color="transparent")
        stats_frame.pack(fill="x", pady=(0, 4))
        stats_frame.grid_columnconfigure(
            (0, 1, 2, 3, 4), weight=1, uniform="stats"
        )

        # "Attempts" and "Delivered" are deliberately separate. Attempts is how
        # many requests the engine fired (incremented before the request goes
        # out); Delivered is how many actually came back from the target. A
        # campaign that shows 100 attempts may still have delivered far fewer -
        # the delivery card is the number the target's own stats should match.
        stat_configs = [
            ("req", "Attempts", COLORS["text"]),
            ("success", "Delivered", COLORS["success"]),
            ("fail", "Failed", COLORS["danger"]),
            ("rate", "Delivery", COLORS["accent_2"]),
            ("proxies", "Proxies", COLORS["accent"]),
        ]
        for col, (key, title, color) in enumerate(stat_configs):
            card = ctk.CTkFrame(
                stats_frame, fg_color=COLORS["card"], corner_radius=RADIUS["lg"],
                border_width=1, border_color=COLORS["border_soft"],
            )
            card.grid(row=0, column=col, sticky="nsew", padx=4, pady=2)
            card.grid_columnconfigure(0, weight=1)
            # thin colour strip so each metric is identifiable at a glance
            ctk.CTkFrame(card, fg_color=color, height=3, corner_radius=2).pack(
                fill="x", padx=12, pady=(9, 0)
            )
            ctk.CTkLabel(
                card, text=title, font=("Roboto", 10), text_color=COLORS["text_dim"]
            ).pack(pady=(6, 0))
            lbl = ctk.CTkLabel(
                card, text="0", font=("Roboto", 20, "bold"), text_color=color
            )
            lbl.pack(pady=(0, 10))
            self.lbl_stats[key] = lbl

    def _setup_config_card(self, parent: ctk.CTkFrame) -> None:
        """Set up the main configuration card."""
        cfg_frame = ctk.CTkFrame(parent, fg_color=COLORS["card"])
        cfg_frame.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            cfg_frame, text="Attack Configuration", font=("Roboto", 14, "bold")
        ).pack(anchor="w", padx=16, pady=7)

        # Target URL
        self.entry_url = ctk.CTkEntry(
            cfg_frame, placeholder_text="https://target.com", height=scaled(32)
        )
        self.entry_url.pack(fill="x", padx=20, pady=(0, 10))
        self.entry_url.insert(0, self.settings.get("target_url", ""))

        # Engine Mode Selector
        self._setup_engine_selector(cfg_frame)

        # Sliders
        self._setup_sliders(cfg_frame)

        # How many visits to take, and how many clicks each IP serves
        self._setup_campaign_size(cfg_frame)

        # IP source: own proxy list or the free Tor network
        self._setup_ip_source(cfg_frame)

        # Button Row
        self._setup_buttons(cfg_frame)

    #: Visit targets offered in the "Visits to take" selector. "Unlimited" keeps
    #: the campaign running until it is stopped by hand.
    VISIT_CHOICES = ["Unlimited", "50", "100", "250", "500", "1000", "2500", "5000"]
    #: Clicks one IP serves before the engine switches to the next IP.
    CLICKS_PER_IP_CHOICES = ["Every click", "10", "20", "30", "50"]

    def _setup_campaign_size(self, parent: ctk.CTkFrame) -> None:
        """Set up the "visits to take" and "clicks per IP" selectors."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(3, 0))
        row.grid_columnconfigure((0, 1), weight=1, uniform="csize")

        # How many visits this campaign should deliver
        visits_frame = ctk.CTkFrame(row, fg_color="transparent")
        visits_frame.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkLabel(
            visits_frame, text="Visits to take:", font=("Roboto", scaled(11))
        ).pack(anchor="w")
        self.menu_visits = ctk.CTkOptionMenu(
            visits_frame,
            values=self.VISIT_CHOICES,
            command=self.on_visits_change,
            font=("Roboto", scaled(11)),
            fg_color=COLORS["field"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        saved_visits = int(self.settings.get("total_visits", 0) or 0)
        self.menu_visits.set(
            str(saved_visits) if str(saved_visits) in self.VISIT_CHOICES else "Unlimited"
        )
        self.menu_visits.pack(fill="x", pady=2)

        # Type any amount you like - the preset list cannot cover every number.
        amount_row = ctk.CTkFrame(visits_frame, fg_color="transparent")
        amount_row.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(
            amount_row,
            text="or type amount:",
            font=("Roboto", scaled(9)),
            text_color=COLORS["text_mute"],
        ).pack(side="left")
        self.entry_visits = ctk.CTkEntry(
            amount_row,
            placeholder_text="e.g. 1000",
            height=scaled(24),
            width=scaled(90),
            font=("Roboto", scaled(10)),
            fg_color=COLORS["field"],
            border_width=1,
            border_color=COLORS["border_soft"],
        )
        if saved_visits and str(saved_visits) not in self.VISIT_CHOICES:
            self.entry_visits.insert(0, str(saved_visits))
        self.entry_visits.pack(side="left", padx=(6, 0))
        # The box only takes effect when Apply is pressed. Writing the setting on
        # every keystroke meant a half-typed number became the campaign size.
        self.entry_visits.bind("<Return>", lambda _e: self.on_apply_amount())
        ctk.CTkButton(
            amount_row,
            text="Apply",
            width=scaled(54),
            height=scaled(24),
            font=("Roboto", scaled(10), "bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self.on_apply_amount,
        ).pack(side="left", padx=(6, 0))

        # Live progress towards the amount that was asked for.
        self.lbl_visit_progress = ctk.CTkLabel(
            visits_frame,
            text="Delivered 0  (no limit)   counted by site 0   attempts 0",
            font=("Roboto", scaled(9), "bold"),
            text_color=COLORS["accent_2"],
            anchor="w",
            justify="left",
        )
        self.lbl_visit_progress.pack(anchor="w", fill="x", pady=(3, 3))

        # How many clicks one IP serves before the engine switches IP
        per_ip_frame = ctk.CTkFrame(row, fg_color="transparent")
        per_ip_frame.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ctk.CTkLabel(
            per_ip_frame, text="Clicks per IP:", font=("Roboto", scaled(11))
        ).pack(anchor="w")
        self.menu_clicks_per_ip = ctk.CTkOptionMenu(
            per_ip_frame,
            values=self.CLICKS_PER_IP_CHOICES,
            command=self.on_clicks_per_ip_change,
            font=("Roboto", scaled(11)),
            fg_color=COLORS["field"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        saved_per_ip = int(self.settings.get("clicks_per_ip", 20) or 0)
        if saved_per_ip <= 1:
            self.menu_clicks_per_ip.set("Every click")
        elif str(saved_per_ip) in self.CLICKS_PER_IP_CHOICES:
            self.menu_clicks_per_ip.set(str(saved_per_ip))
        else:
            self.menu_clicks_per_ip.set("20")
        self.menu_clicks_per_ip.pack(fill="x", pady=2)


    #: Timer-based rotation choices (used with the Tor source).
    ROTATE_SECONDS_CHOICES = ["Off", "30s", "60s", "120s", "300s", "600s"]
    #: How many IPs a campaign may use before it stops.
    MAX_IP_CHOICES = ["Unlimited", "10", "25", "50", "100", "250", "500"]

    def _setup_ip_source(self, parent: ctk.CTkFrame) -> None:
        """Set up the IP source selector and the Tor panel.

        Tor is the free fallback for when there is no proxy list: the engine talks
        to Tor's local SOCKS port and asks for a new circuit (a new exit IP) when
        the rotation rule is met.
        """
        card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=RADIUS["md"])
        card.pack(fill="x", padx=16, pady=(6, 0))

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            head, text="IP source", font=("Roboto", scaled(12), "bold"),
            text_color=COLORS["text"],
        ).pack(side="left")

        source_row = ctk.CTkFrame(card, fg_color="transparent")
        source_row.pack(fill="x", padx=14)
        source_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="src")

        self.menu_proxy_source = ctk.CTkOptionMenu(
            source_row,
            values=["My proxies", "Tor (free)"],
            command=self.on_proxy_source_change,
            font=("Roboto", scaled(11)),
            fg_color=COLORS["field"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        saved_source = str(self.settings.get("proxy_source", "list")).lower()
        self.menu_proxy_source.set("Tor (free)" if saved_source == "tor" else "My proxies")
        self.menu_proxy_source.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(2, 6))

        self.menu_rotate_seconds = ctk.CTkOptionMenu(
            source_row,
            values=self.ROTATE_SECONDS_CHOICES,
            command=self.on_rotate_seconds_change,
            font=("Roboto", scaled(11)),
            fg_color=COLORS["field"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        saved_seconds = int(self.settings.get("rotate_interval_seconds", 0) or 0)
        self.menu_rotate_seconds.set(
            f"{saved_seconds}s" if f"{saved_seconds}s" in self.ROTATE_SECONDS_CHOICES else "Off"
        )
        self.menu_rotate_seconds.grid(row=0, column=1, sticky="ew", padx=6, pady=(2, 6))

        self.menu_max_ips = ctk.CTkOptionMenu(
            source_row,
            values=self.MAX_IP_CHOICES,
            command=self.on_max_ips_change,
            font=("Roboto", scaled(11)),
            fg_color=COLORS["field"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        saved_max = int(self.settings.get("max_ip_rotations", 0) or 0)
        self.menu_max_ips.set(
            str(saved_max) if str(saved_max) in self.MAX_IP_CHOICES else "Unlimited"
        )
        self.menu_max_ips.grid(row=0, column=2, sticky="ew", padx=(6, 0), pady=(2, 6))

        labels = ctk.CTkFrame(card, fg_color="transparent")
        # above the selectors they describe
        labels.pack(fill="x", padx=14, before=source_row)
        labels.grid_columnconfigure((0, 1, 2), weight=1, uniform="srcl")
        for col, text in enumerate(
            ("Source", "New IP every (timer)", "Max IPs to use")
        ):
            ctk.CTkLabel(
                labels, text=text, font=("Roboto", scaled(9)),
                text_color=COLORS["text_mute"],
            ).grid(row=0, column=col, sticky="w", padx=(0, 6))

        # --- mode panels: only what the chosen IP source needs ---------------
        # Proxy mode: nothing to configure here, the proxies live in Proxy Manager.
        self.frame_proxy_note = ctk.CTkFrame(card, fg_color="transparent")
        self.lbl_proxy_note = ctk.CTkLabel(
            self.frame_proxy_note,
            text="Using the proxies loaded in Proxy Manager (the count is shown in "
            "the grid below it). Press CHECK TARGET, then START CAMPAIGN.",
            font=("Roboto", scaled(9)),
            text_color=COLORS["text_mute"],
            justify="left",
            wraplength=scaled(560),
        )
        self.lbl_proxy_note.pack(anchor="w", padx=14, pady=(8, 12))

        # Tor mode: everything Tor needs to run the campaign.
        self.frame_tor_controls = ctk.CTkFrame(card, fg_color="transparent")

        tor_row = ctk.CTkFrame(self.frame_tor_controls, fg_color="transparent")
        tor_row.pack(fill="x", padx=14, pady=(8, 4))

        ctk.CTkLabel(
            tor_row, text="Tor SOCKS", font=("Roboto", scaled(9)),
            text_color=COLORS["text_mute"],
        ).pack(side="left")
        self.entry_tor_socks = ctk.CTkEntry(
            tor_row, width=scaled(58), height=scaled(26), font=("Roboto", scaled(10))
        )
        self.entry_tor_socks.insert(0, str(self.settings.get("tor_socks_port", 9050)))
        self.entry_tor_socks.pack(side="left", padx=(4, 10))

        ctk.CTkLabel(
            tor_row, text="Control", font=("Roboto", scaled(9)),
            text_color=COLORS["text_mute"],
        ).pack(side="left")
        self.entry_tor_control = ctk.CTkEntry(
            tor_row, width=scaled(58), height=scaled(26), font=("Roboto", scaled(10))
        )
        self.entry_tor_control.insert(
            0, str(self.settings.get("tor_control_port", 9051))
        )
        self.entry_tor_control.pack(side="left", padx=(4, 10))

        # Tor Browser is NOT required: this fetches the official tor daemon
        # (the small "tor expert bundle") and starts it.
        self.btn_get_tor = ctk.CTkButton(
            tor_row, text="GET TOR", width=scaled(80), height=scaled(26),
            font=("Roboto", scaled(10), "bold"), fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self.app.get_tor_clicked(),
        )
        self.btn_get_tor.pack(side="left", padx=(0, 6))

        self.btn_start_tor = ctk.CTkButton(
            tor_row, text="START TOR", width=scaled(88), height=scaled(26),
            font=("Roboto", scaled(10), "bold"), fg_color=COLORS["field"],
            hover_color=COLORS["card"],
            command=lambda: self.app.start_tor_clicked(),
        )
        self.btn_start_tor.pack(side="left", padx=(0, 6))

        self.btn_test_tor = ctk.CTkButton(
            tor_row, text="TEST / ROTATE", width=scaled(110), height=scaled(26),
            font=("Roboto", scaled(10), "bold"), fg_color=COLORS["field"],
            hover_color=COLORS["card"],
            command=lambda: self.app.test_tor_clicked(),
        )
        self.btn_test_tor.pack(side="left")

        # Kept as a single short line: the long explanation ate the vertical space
        # the controls need on a short screen, and the outcome is in the log anyway.
        self.lbl_tor_hint = ctk.CTkLabel(
            self.frame_tor_controls,
            text="Tor: free, no proxy list - press START TOR once (exit IPs are "
            "blocked by some ad networks).",
            font=("Roboto", scaled(9)),
            text_color=COLORS["text_mute"],
            justify="left",
            anchor="w",
        )
        self.lbl_tor_hint.pack(anchor="w", fill="x", padx=14, pady=(3, 2))

        # Show only the panel that belongs to the selected IP source
        self._apply_source_mode()

    def _apply_source_mode(self) -> None:
        """Show only the settings that belong to the selected IP source."""
        tor_mode = str(self.settings.get("proxy_source", "list")).lower() == "tor"
        try:
            if tor_mode:
                self.frame_proxy_note.pack_forget()
                self.frame_tor_controls.pack(fill="x")
            else:
                self.frame_tor_controls.pack_forget()
                self.frame_proxy_note.pack(fill="x")
        except Exception:
            pass

    def on_proxy_source_change(self, value: str) -> None:
        """Switch between the proxy list and the free Tor network."""
        source = "tor" if value.startswith("Tor") else "list"
        self.settings["proxy_source"] = source
        self.app.settings["proxy_source"] = source
        self._apply_source_mode()
        if source == "tor":
            self.app.log(
                "IP source: Tor (free). No proxy list needed - press START TOR once "
                "if Tor is not running yet, then START CAMPAIGN."
            )
        else:
            self.app.log(
                "IP source: my proxy list (Proxy Manager). Load and test proxies "
                "there first."
            )

    def on_rotate_seconds_change(self, value: str) -> None:
        """Store the timer-based IP rotation interval."""
        try:
            seconds = 0 if value == "Off" else int(value.rstrip("s"))
        except (TypeError, ValueError):
            seconds = 0
        self.settings["rotate_interval_seconds"] = seconds
        self.app.settings["rotate_interval_seconds"] = seconds
        if seconds:
            self.app.log(f"IP will also change every {seconds} seconds.")
        else:
            self.app.log("Timer rotation off - the IP changes after the click batch.")

    def on_max_ips_change(self, value: str) -> None:
        """Store how many IPs a campaign may use."""
        try:
            max_ips = 0 if value == "Unlimited" else int(value)
        except (TypeError, ValueError):
            max_ips = 0
        self.settings["max_ip_rotations"] = max_ips
        self.app.settings["max_ip_rotations"] = max_ips
        if max_ips:
            self.app.log(f"Campaign will use at most {max_ips} IPs, then stop.")
        else:
            self.app.log("No IP limit - rotation continues until you stop it.")

    def _typed_amount(self) -> str:
        """Digits currently in the amount box ("" when it is empty)."""
        try:
            typed = (self.entry_visits.get() or "").strip()
        except Exception:
            return ""
        return "".join(ch for ch in typed if ch.isdigit())

    def visits_target(self) -> int:
        """The amount of traffic asked for, in visits (0 = unlimited).

        A typed number wins over the preset - but only while a number is actually
        in the box, because choosing a preset clears it. That matters: the box is
        restored from the saved setting, so a leftover value used to keep winning
        in the background and a campaign set to 5000 (or Unlimited) silently ran
        and stopped at the number left over from an earlier run.
        """
        digits = self._typed_amount()
        if digits:
            # "0" means unlimited here as it does everywhere else - it used to be
            # clamped to 1, which stopped a campaign after a single visit.
            return int(digits)
        try:
            choice = self.menu_visits.get()
        except Exception:
            return 0
        return 0 if choice == "Unlimited" else int(choice or 0)

    def on_visits_change(self, value: str) -> None:
        """A preset was picked (or Apply pressed): that is the campaign size now.

        The amount box is cleared so the preset cannot be overridden by an old
        number sitting in it.
        """
        try:
            choice = value if value is not None else self.menu_visits.get()
            total = 0 if choice == "Unlimited" else int(choice)
        except (TypeError, ValueError):
            choice, total = "Unlimited", 0
        # Keep the selector's own display in step: visits_target() reads back what
        # the selector shows, so if they disagree the engine is configured with a
        # different amount than the one that was just chosen.
        try:
            self.menu_visits.set(choice)
        except Exception:
            pass
        self._clear_amount_box()
        self._set_campaign_size(total)

    def on_apply_amount(self) -> None:
        """Apply button: the number typed in the box becomes the campaign size."""
        digits = self._typed_amount()
        if not digits:
            self.app.log("Type an amount first, or pick one of the presets above.")
            return
        self._set_campaign_size(int(digits))

    def _clear_amount_box(self) -> None:
        try:
            self.entry_visits.delete(0, "end")
        except Exception:
            pass

    def _set_campaign_size(self, total: int) -> None:
        """Record and announce the campaign size (0 = unlimited)."""
        self.settings["total_visits"] = total
        self.app.settings["total_visits"] = total
        if total:
            self.app.log(
                f"Campaign size set: {total} visits (the campaign stops once that many "
                f"visits have actually been delivered)."
            )
        else:
            self.app.log("Campaign size set: unlimited (runs until you stop it).")
        self.update_visit_progress()

    def update_visit_progress(self) -> None:
        """Show delivered-visits progress against the amount that was asked for."""
        label = getattr(self, "lbl_visit_progress", None)
        if label is None:
            return
        try:
            delivered = int(self.app.stats.get("success", 0))
            attempts = int(self.app.stats.get("req", 0))
            counted = int(self.app.engine.stats.tracked) if self.app.engine else 0
            target = self.visits_target()
            if target:
                head = "Delivered %d / %d  (%d%%)" % (
                    delivered,
                    target,
                    int(delivered * 100 / target),
                )
            else:
                head = "Delivered %d  (no limit)" % delivered
            label.configure(
                text="%s   counted by site %d   attempts %d" % (head, counted, attempts)
            )
        except Exception:
            pass

    def on_clicks_per_ip_change(self, value: str) -> None:
        """Store how many clicks one IP should serve before switching."""
        try:
            per_ip = 1 if value == "Every click" else int(value)
        except (TypeError, ValueError):
            per_ip = 20
        self.settings["clicks_per_ip"] = per_ip
        self.app.settings["clicks_per_ip"] = per_ip
        self.app.log(
            f"Clicks per IP set: {per_ip} - the engine keeps one IP for {per_ip} "
            f"click(s) before rotating to the next IP."
        )

    def _setup_engine_selector(self, parent: ctk.CTkFrame) -> None:
        """Set up the engine mode selector."""
        mode_frame = ctk.CTkFrame(parent, fg_color="transparent")
        mode_frame.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(mode_frame, text="Engine:", font=("Roboto", 12)).pack(
            side="left", padx=(0, 10)
        )

        self.mode_selector = ctk.CTkSegmentedButton(
            mode_frame,
            values=["Fast (curl)", "Browser (stealth)"],
            command=self.on_mode_change,
            font=("Roboto", 11),
            fg_color=COLORS["card"],
            selected_color=COLORS["accent"],
            selected_hover_color=COLORS["accent_hover"],
        )
        current_mode = self.settings.get("engine_mode", "curl")
        self.mode_selector.set(
            "Fast (curl)" if current_mode == "curl" else "Browser (stealth)"
        )
        self.mode_selector.pack(side="left", fill="x", expand=True)

    def _setup_sliders(self, parent: ctk.CTkFrame) -> None:
        """Set up the configuration sliders."""
        slider_row = ctk.CTkFrame(parent, fg_color="transparent")
        slider_row.pack(fill="x", padx=15, pady=10)
        slider_row.grid_columnconfigure(0, weight=2)  # Threads
        slider_row.grid_columnconfigure(1, weight=3)  # Duration
        slider_row.grid_columnconfigure(2, weight=3)  # Burst mode

        # Threads slider
        self._setup_threads_slider(slider_row)

        # Duration sliders
        self._setup_duration_sliders(slider_row)

        # Burst mode controls
        self._setup_burst_controls(slider_row)

    def _setup_threads_slider(self, parent: ctk.CTkFrame) -> None:
        """Set up the threads slider."""
        t_frame = ctk.CTkFrame(parent, fg_color="transparent")
        t_frame.grid(row=0, column=0, sticky="nsew", padx=5)

        self.lbl_threads = ctk.CTkLabel(
            t_frame, text=f"Threads: {self.settings.get('threads', 5)}"
        )
        self.lbl_threads.pack(anchor="w")

        self.slider_threads = ctk.CTkSlider(
            t_frame,
            from_=1,
            to=100,
            number_of_steps=99,
            command=self.update_thread_lbl,
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        self.slider_threads.set(self.settings.get("threads", 5))
        self.slider_threads.pack(fill="x", pady=5)

    def _setup_duration_sliders(self, parent: ctk.CTkFrame) -> None:
        """Set up the view duration sliders."""
        v_frame = ctk.CTkFrame(parent, fg_color="transparent")
        v_frame.grid(row=0, column=1, sticky="nsew", padx=5)

        self.lbl_viewtime = ctk.CTkLabel(
            v_frame,
            text=f"Duration: {self.settings.get('viewtime_min', 5)}s - {self.settings.get('viewtime_max', 10)}s",
        )
        self.lbl_viewtime.pack(anchor="w")

        duration_sliders = ctk.CTkFrame(v_frame, fg_color="transparent")
        duration_sliders.pack(fill="x")
        duration_sliders.grid_columnconfigure((0, 1), weight=1)

        # Min slider
        min_frame = ctk.CTkFrame(duration_sliders, fg_color="transparent")
        min_frame.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkLabel(min_frame, text="Min:", font=("Roboto", 10)).pack(anchor="w")
        self.slider_view_min = ctk.CTkSlider(
            min_frame,
            from_=1,
            to=60,
            number_of_steps=59,
            command=self.update_view_lbl,
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        self.slider_view_min.set(self.settings.get("viewtime_min", 5))
        self.slider_view_min.pack(fill="x", pady=2)

        # Max slider
        max_frame = ctk.CTkFrame(duration_sliders, fg_color="transparent")
        max_frame.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ctk.CTkLabel(max_frame, text="Max:", font=("Roboto", 10)).pack(anchor="w")
        self.slider_view_max = ctk.CTkSlider(
            max_frame,
            from_=1,
            to=60,
            number_of_steps=59,
            command=self.update_view_lbl,
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        self.slider_view_max.set(self.settings.get("viewtime_max", 10))
        self.slider_view_max.pack(fill="x", pady=2)

    def _setup_burst_controls(self, parent: ctk.CTkFrame) -> None:
        """Set up the burst mode controls."""
        burst_frame = ctk.CTkFrame(parent, fg_color="transparent")
        burst_frame.grid(row=0, column=2, sticky="nsew", padx=5)

        # Burst enable checkbox and label
        burst_header = ctk.CTkFrame(burst_frame, fg_color="transparent")
        burst_header.pack(fill="x")
        self.chk_burst_mode = ctk.CTkCheckBox(
            burst_header,
            text="",
            width=scaled(20),
            command=self.on_burst_toggle,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
        )
        self.chk_burst_mode.pack(side="left")
        if self.settings.get("burst_mode", False):
            self.chk_burst_mode.select()

        self.lbl_burst = ctk.CTkLabel(
            burst_header,
            text=f"Burst: {self.settings.get('burst_requests', 10)} req, "
            f"{self.settings.get('burst_sleep_min', 2)}-{self.settings.get('burst_sleep_max', 5)}s sleep",
        )
        self.lbl_burst.pack(side="left", padx=(2, 0))

        # Burst size and sleep sliders
        burst_sliders = ctk.CTkFrame(burst_frame, fg_color="transparent")
        burst_sliders.pack(fill="x")
        burst_sliders.grid_columnconfigure((0, 1), weight=1)

        # Burst size
        size_frame = ctk.CTkFrame(burst_sliders, fg_color="transparent")
        size_frame.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkLabel(size_frame, text="Requests:", font=("Roboto", scaled(10))).pack(
            anchor="w"
        )
        self.slider_burst_size = ctk.CTkSlider(
            size_frame,
            from_=5,
            to=50,
            number_of_steps=45,
            command=self.update_burst_lbl,
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        self.slider_burst_size.set(self.settings.get("burst_requests", 10))
        self.slider_burst_size.pack(fill="x", pady=2)

        # Sleep duration
        sleep_frame = ctk.CTkFrame(burst_sliders, fg_color="transparent")
        sleep_frame.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ctk.CTkLabel(sleep_frame, text="Sleep (s):", font=("Roboto", scaled(10))).pack(
            anchor="w"
        )
        self.slider_burst_sleep = ctk.CTkSlider(
            sleep_frame,
            from_=1,
            to=30,
            number_of_steps=29,
            command=self.update_burst_lbl,
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        self.slider_burst_sleep.set(self.settings.get("burst_sleep_max", 5))
        self.slider_burst_sleep.pack(fill="x", pady=2)

    def _setup_buttons(self, parent: ctk.CTkFrame) -> None:
        """Set up the control buttons."""
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(10, 10))

        self.btn_attack = ctk.CTkButton(
            btn_row,
            text="START CAMPAIGN",
            height=scaled(40),
            fg_color=COLORS["success"],
            font=("Roboto", scaled(13), "bold"),
            command=self.app.toggle_attack,
        )
        self.btn_attack.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.btn_reset = ctk.CTkButton(
            btn_row,
            text="RESET",
            height=scaled(40),
            fg_color=COLORS["warning"],
            font=("Roboto", scaled(11), "bold"),
            width=scaled(70),
            command=self.app.reset_stats,
        )
        self.btn_reset.pack(side="right")

        # Opens the target once and reports whether clicks can be counted on it
        # (a Cloudflare 526-style origin error cannot be bypassed, so it is better
        # to know before spending the proxy pool).
        self.btn_check_target = ctk.CTkButton(
            btn_row,
            text="CHECK TARGET",
            height=scaled(40),
            fg_color=COLORS["field"],
            hover_color=COLORS["card"],
            font=("Roboto", scaled(11), "bold"),
            width=scaled(120),
            command=self.app.check_target_health_clicked,
        )
        self.btn_check_target.pack(side="right", padx=(0, 8))

    def _setup_browser_stats(self, parent: ctk.CTkFrame) -> None:
        """Browser / captcha / protection readout, as one compact strip.

        These were three tall cards taking ~100px of a short window, which pushed
        the attack controls below the fold and made the page look half rendered.
        The same numbers fit on one line, so the dashboard fits without scrolling.
        """
        self.browser_stats_frame = ctk.CTkFrame(parent, fg_color=COLORS["card"])
        self.browser_stats_frame.pack(fill="x", pady=(0, 4))

        row = ctk.CTkFrame(self.browser_stats_frame, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(5, 2))

        def pair(caption, value, colour, pad=9):
            ctk.CTkLabel(
                row, text=caption, font=("Roboto", scaled(9)),
                text_color=COLORS["text_dim"],
            ).pack(side="left", padx=(0, 3))
            label = ctk.CTkLabel(
                row, text=value, font=("Roboto", scaled(11), "bold"),
                text_color=colour,
            )
            label.pack(side="left", padx=(0, pad))
            return label

        self.lbl_browser_type = pair("Browser:", "--", COLORS["accent"])
        self.lbl_browser_contexts = pair("Contexts", "0/0", COLORS["text_dim"])
        self.lbl_captcha_stats = pair("Captcha", "0/0/0", COLORS["success"])
        self.lbl_protection_stats = pair("Protection", "0/0", COLORS["warning"])
        self.lbl_balance_2captcha = pair("2Cap:", "--", COLORS["text_dim"])
        self.lbl_balance_anticaptcha = pair("Anti:", "--", COLORS["text_dim"], pad=0)

        self.lbl_protection_event = ctk.CTkLabel(
            self.browser_stats_frame,
            text="No protection events.",
            font=("Roboto", scaled(9)),
            text_color=COLORS["text_dim"],
            anchor="w",
        )
        self.lbl_protection_event.pack(fill="x", padx=10, pady=(0, 4))

    def _setup_activity_log(self, parent: ctk.CTkFrame) -> None:
        """Set up the activity log panel."""
        log_frame = ctk.CTkFrame(parent, fg_color="transparent")
        log_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 0))
        log_frame.grid_rowconfigure(1, weight=1, minsize=self._log_min)
        log_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            log_frame,
            text="Activity Log",
            font=("Roboto", scaled(11)),
            text_color=COLORS["text_dim"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.log_box = ctk.CTkTextbox(
            log_frame, fg_color=COLORS["card"], height=scaled(100)
        )
        self.log_box.grid(row=1, column=0, sticky="nsew")
        self.log_box.configure(state="disabled")

    # Event handlers

    def update_thread_lbl(self, value) -> None:
        """Update the threads label."""
        self.lbl_threads.configure(text=f"Threads: {int(value)}")

    def update_view_lbl(self, value) -> None:
        """Update the view duration label."""
        mn = int(self.slider_view_min.get())
        mx = int(self.slider_view_max.get())
        self.lbl_viewtime.configure(text=f"Duration: {mn}s - {mx}s")

    def update_burst_lbl(self, value=None) -> None:
        """Update burst mode label with current settings."""
        burst_size = int(self.slider_burst_size.get())
        sleep_max = int(self.slider_burst_sleep.get())
        sleep_min = max(1, sleep_max // 2)
        self.lbl_burst.configure(
            text=f"Burst: {burst_size} req, {sleep_min}-{sleep_max}s sleep"
        )

    def on_burst_toggle(self) -> None:
        """Handle burst mode toggle."""
        enabled = self.chk_burst_mode.get()
        self.settings["burst_mode"] = enabled
        state = "enabled" if enabled else "disabled"
        self.app.log(f"Burst mode {state}")

    def on_mode_change(self, value) -> None:
        """Handle engine mode change."""
        is_browser = "Browser" in value
        self.settings["engine_mode"] = "browser" if is_browser else "curl"

        # Adjust thread slider limits based on mode
        if is_browser:
            self.slider_threads.configure(from_=1, to=10, number_of_steps=9)
            current = int(self.slider_threads.get())
            if current > 10:
                self.slider_threads.set(5)
                self.lbl_threads.configure(text="Threads: 5")
        else:
            self.slider_threads.configure(from_=1, to=100, number_of_steps=99)

        self._update_browser_stats_visibility()
        self.app.log(
            f"Engine mode: {'Browser (stealth)' if is_browser else 'Fast (curl)'}"
        )

    def _update_browser_stats_visibility(self) -> None:
        """Show/hide browser stats row based on engine mode."""
        is_browser = self.settings.get("engine_mode", "curl") == "browser"
        if is_browser:
            self.browser_stats_frame.pack(fill="x", pady=(0, 4))
        else:
            self.browser_stats_frame.pack_forget()

    def log(self, message: str) -> None:
        """Log a message to the activity log."""
        import time

        timestamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{timestamp}] {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
