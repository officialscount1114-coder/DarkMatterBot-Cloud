"""Reusable UI components: VirtualGrid, CountrySelect, ActivityLog, DraggableSash.

The grid is virtualised (only the visible rows are painted on a canvas) and can
be filtered by country without touching the underlying data, so a country pick
never loses proxies - it only changes what is on screen.

Animations are all ``after()``-driven and composited by hand, because tkinter has
no alpha channel: a "fade" walks an item's colour toward its own background and a
"slide" moves its coordinates a few pixels.
"""

import time
import tkinter as tk
from tkinter import Canvas

import customtkinter as ctk

from .countries import COUNTRIES, name_of
from .scaling import get_scaling_factor
from .styles import COLORS, MOTION, RADIUS, ease_out, fade, font, mix


def country_code_of(item: dict) -> str:
    """Normalised 2-letter country code of a proxy row ("??" when unknown)."""
    code = str(item.get("country_code") or "").strip().upper()
    return code if len(code) == 2 else "??"


def country_name_of(item: dict) -> str:
    """Human country name of a proxy row, or "" when it is unknown."""
    name = str(item.get("country") or "").strip()
    return "" if name.lower() in ("", "unknown", "none", "n/a") else name


class DraggableSash(ctk.CTkFrame):
    """
    A horizontal sash/splitter that can be dragged to resize panels above/below.
    Used between config area and activity log to allow user-adjustable heights.
    """

    def __init__(self, master, on_drag=None, min_above=100, min_below=80, **kwargs):
        """
        Args:
            master: Parent widget
            on_drag: Callback function(delta_y) called during drag with pixel change
            min_above: Minimum height for panel above sash
            min_below: Minimum height for panel below sash
        """
        # Default styling for sash
        kwargs.setdefault("height", 6)
        kwargs.setdefault("fg_color", COLORS.get("nav", "#2a2a3d"))
        kwargs.setdefault("corner_radius", 0)

        super().__init__(master, **kwargs)

        self.on_drag = on_drag
        self.min_above = min_above
        self.min_below = min_below
        self._drag_start_y = None
        self._is_dragging = False

        # Visual handle indicator (centered grip lines)
        self._grip = ctk.CTkFrame(
            self,
            fg_color=COLORS.get("text_mute", "#888"),
            width=40,
            height=2,
            corner_radius=1,
        )
        self._grip.place(relx=0.5, rely=0.5, anchor="center")

        # Bind mouse events
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)

        # Also bind to grip
        self._grip.bind("<Enter>", self._on_enter)
        self._grip.bind("<Leave>", self._on_leave)
        self._grip.bind("<Button-1>", self._on_press)
        self._grip.bind("<B1-Motion>", self._on_motion)
        self._grip.bind("<ButtonRelease-1>", self._on_release)

    def _on_enter(self, event):
        """Change cursor to resize indicator on hover."""
        self.configure(cursor="sb_v_double_arrow")
        self._grip.configure(fg_color=COLORS.get("accent", "#00d4aa"))

    def _on_leave(self, event):
        """Reset cursor when leaving."""
        if not self._is_dragging:
            self.configure(cursor="")
            self._grip.configure(fg_color=COLORS.get("text_mute", "#888"))

    def _on_press(self, event):
        """Start drag operation."""
        self._drag_start_y = event.y_root
        self._is_dragging = True

    def _on_motion(self, event):
        """Handle drag movement."""
        if self._drag_start_y is not None and self.on_drag:
            delta = event.y_root - self._drag_start_y
            self._drag_start_y = event.y_root
            self.on_drag(delta)

    def _on_release(self, event):
        """End drag operation."""
        self._drag_start_y = None
        self._is_dragging = False
        # Check if mouse is still over sash
        try:
            widget_under = self.winfo_containing(event.x_root, event.y_root)
            if widget_under != self and widget_under != self._grip:
                self.configure(cursor="")
                self._grip.configure(fg_color=COLORS.get("text_mute", "#888"))
        except Exception:
            pass


class VirtualGrid(ctk.CTkFrame):
    """Virtualised, sortable proxy grid with an animated country filter.

    ``self.data`` always holds every row (nothing is ever dropped by filtering);
    the country filter only decides what is painted and animates the change in.
    """

    def __init__(self, master, columns, **kwargs):
        kwargs.setdefault("fg_color", COLORS["bg"])
        kwargs.setdefault("corner_radius", 0)
        super().__init__(master, **kwargs)
        self.data = []

        # Scale-aware row height
        self._base_row_h = 30
        self.row_h = int(self._base_row_h * get_scaling_factor())

        self.sort_key = None  # Track current sort column
        self.sort_reverse = False
        self._needs_draw = False  # Deferred drawing flag
        self._needs_sort = False  # Deferred sort flag
        self._last_draw_len = 0  # Track data length at last draw

        # --- country filter + reveal animation --------------------------
        self.filter_code = None   # None = every row on screen
        self.filter_name = ""
        self._view = []           # rows matching the filter (kept in sync on add)
        self._reveal_t0 = 0.0     # start time of the current reveal
        self._reveal_len = 0      # rows the reveal was started for
        self._anim_job = None

        # Column width management - proportional weights (will be converted to pixels)
        self.col_map = columns
        self.num_cols = len(columns)
        # Default weights: Address wider, others smaller
        self.col_weights = [
            2.0,
            0.8,
            1.2,
            0.8,
            0.8,
            0.8,
        ]  # Address, Proto, Country, Status, Ping, Anon

        # Resize tracking
        self._resize_col = None
        self._resize_start_x = 0
        self._resize_start_weights = None
        self._min_col_weight = 0.4  # Minimum column weight

        # Scale-aware header height
        self._header_h = 38
        self._header_font_size = max(9, 11)

        # Create main container frame for header + canvas (excludes scrollbar)
        self._content_frame = ctk.CTkFrame(
            self, fg_color="transparent", corner_radius=0
        )
        self._content_frame.pack(side="left", fill="both", expand=True)

        # Header frame - use place geometry for precise positioning
        self.headers = ctk.CTkFrame(
            self._content_frame,
            height=self._header_h,
            fg_color=COLORS["nav"],
            corner_radius=0,
        )
        self.headers.pack(fill="x")
        self.headers.pack_propagate(False)  # Keep fixed height

        # 1px hairline under the header
        self._header_line = ctk.CTkFrame(
            self._content_frame, height=1, fg_color=COLORS["border"], corner_radius=0
        )
        self._header_line.pack(fill="x")

        # Create header buttons (will be positioned by _update_header_positions)
        self.header_btns = []
        for i, col in enumerate(columns):
            btn = ctk.CTkButton(
                self.headers,
                text=col,
                font=font(self._header_font_size, "bold"),
                fg_color="transparent",
                hover_color=COLORS["surface"],
                text_color=COLORS["accent_glow"],
                corner_radius=0,
                height=self._header_h,
                command=lambda c=col, idx=i: self._on_header_command(c, idx),
            )
            self.header_btns.append(btn)
            # Bind motion for cursor change
            btn.bind("<Motion>", lambda e, idx=i: self._on_header_motion(e, idx))
            btn.bind("<Button-1>", lambda e, idx=i: self._on_btn_press(e, idx))
            btn.bind("<B1-Motion>", self._on_header_drag_global)
            btn.bind("<ButtonRelease-1>", self._on_header_release)

        # Bind drag events to header frame background
        self.headers.bind("<Button-1>", self._on_header_press)
        self.headers.bind("<B1-Motion>", self._on_header_drag_global)
        self.headers.bind("<ButtonRelease-1>", self._on_header_release)

        # Canvas for data rows
        self.canvas = Canvas(self._content_frame, bg=COLORS["bg"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # Scrollbar (outside content frame, aligned with canvas)
        self.scr = ctk.CTkScrollbar(
            self,
            command=self.canvas.yview,
            width=14,
            fg_color=COLORS["bg"],
            button_color=COLORS["surface_2"],
            button_hover_color=COLORS["accent"],
        )
        self.scr.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self.scr.set)

        # Bind events
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind(
            "<MouseWheel>",
            lambda e: (
                self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
                self.draw(),
            ),
        )
        # X11 sends button 4/5 instead of <MouseWheel>
        self.canvas.bind(
            "<Button-4>",
            lambda e: (self.canvas.yview_scroll(-3, "units"), self.draw()),
        )
        self.canvas.bind(
            "<Button-5>",
            lambda e: (self.canvas.yview_scroll(3, "units"), self.draw()),
        )
        self.headers.bind("<Configure>", lambda e: self._update_header_positions())

    # ---------- country filter ----------
    def visible_items(self) -> list:
        """The rows currently on screen (all of them while no country is picked)."""
        return self._view if self.filter_code else self.data

    def visible_count(self) -> int:
        """Number of rows currently on screen."""
        return len(self.visible_items())

    def set_country_filter(self, code: str | None, name: str = "") -> int:
        """Filter the grid to one country code (``None`` clears it).

        Never touches ``self.data``: rows are only hidden. Returns the number of
        rows left on screen.
        """
        self.filter_code = (code or "").strip().upper() or None
        self.filter_name = name or ""
        self._rebuild_view()
        try:
            self.canvas.yview_moveto(0)
        except Exception:
            pass
        self._start_reveal()
        return len(self.visible_items())

    def _rebuild_view(self):
        """Recompute the filtered view from the full data list."""
        if self.filter_code:
            self._view = [
                d for d in self.data if country_code_of(d) == self.filter_code
            ]
        else:
            self._view = list(self.data)

    def country_summary(self) -> list:
        """Per-country aggregate for the picker: count, active count, best ping.

        Sorted by how many proxies the country has, biggest first.
        """
        agg: dict = {}
        for d in self.data:
            code = country_code_of(d)
            row = agg.get(code)
            if row is None:
                row = agg[code] = {
                    "code": code,
                    "name": "",
                    "count": 0,
                    "active": 0,
                    "best": None,
                }
            row["count"] += 1
            if not row["name"]:
                row["name"] = country_name_of(d)
            if d.get("status") == "Active":
                row["active"] += 1
                speed = d.get("speed") or 0
                if row["best"] is None or speed < row["best"]:
                    row["best"] = speed
        rows = list(agg.values())
        # most proxies first, alphabetical inside a tie, "unknown" always last
        rows.sort(key=lambda r: (-r["count"], r["code"] == "??", r["code"]))
        return rows

    # ---------- reveal animation ----------
    def _start_reveal(self):
        """Animate the rows in: they cascade down and fade as they land."""
        self._reveal_t0 = time.perf_counter()
        self._reveal_len = max(1, self.visible_count())
        if self._anim_job is None:
            self._anim_job = self.after(MOTION["frame_ms"], self._anim_tick)

    def _anim_tick(self):
        """One animation frame; stops itself when the reveal is done."""
        self._anim_job = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.draw()
        if self._reveal_progress() < 1.0:
            self._anim_job = self.after(MOTION["frame_ms"], self._anim_tick)

    def _reveal_progress(self) -> float:
        """Eased 0..1 reveal progress of the current animation."""
        if not self._reveal_t0:
            return 1.0
        elapsed = (time.perf_counter() - self._reveal_t0) * 1000.0
        return ease_out(elapsed / MOTION["reveal"])

    def _row_alpha(self, index: int, progress: float) -> float:
        """0..1 opacity of row ``index`` for the given reveal progress."""
        if progress >= 1.0:
            return 1.0
        travel = progress * (self._reveal_len + 4) - index
        return max(0.0, min(1.0, travel / 4.0))

    def stop_animation(self):
        """Cancel a running reveal (used when the grid is torn down)."""
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

    # ---------- header ----------
    def _on_canvas_configure(self, event=None):
        """Handle canvas resize - update headers and redraw."""
        self._update_header_positions()
        self.draw()

    def _update_header_positions(self):
        """Position header buttons to exactly match canvas column positions."""
        w = self.canvas.winfo_width()
        if w < 10:  # Not yet sized
            return

        positions = self._get_col_positions(w)

        for i, btn in enumerate(self.header_btns):
            x = positions[i]
            width = positions[i + 1] - positions[i]
            # CTk widgets require width via configure(), not place()
            # the header sits over a raw canvas row grid: CTk would scale this
            # width again, so undo the factor to keep the columns aligned
            factor = get_scaling_factor() or 1.0
            btn.configure(width=max(1, int(width / factor)))
            btn.place(x=int(x / factor), y=0)

    def _on_header_motion(self, event, col_idx):
        """Change cursor when near column edge for resize."""
        btn = self.header_btns[col_idx]
        btn_width = btn.winfo_width()
        # If within 10 pixels of right edge and not the last column
        if btn_width - event.x < 10 and col_idx < self.num_cols - 1:
            btn.configure(cursor="sb_h_double_arrow")
        else:
            btn.configure(cursor="")

    def _on_header_command(self, col_name, col_idx):
        """Handle header button click - sort unless we're resizing."""
        # Don't sort if we just finished a resize operation
        if self._resize_col is not None:
            return
        self.sort_by(col_name)

    def _on_btn_press(self, event, col_idx):
        """Handle button press - check if starting resize."""
        btn = self.header_btns[col_idx]
        btn_width = btn.winfo_width()
        # If clicking near right edge, start resize
        if btn_width - event.x < 10 and col_idx < self.num_cols - 1:
            self._resize_col = col_idx
            self._resize_start_x = event.x_root
            self._resize_start_weights = self.col_weights.copy()
            # Prevent the button command from firing
            return "break"

    def _on_header_press(self, event):
        """Handle press on header frame (between buttons)."""
        # Find which column edge we're near based on x position
        x = event.x
        w = self.canvas.winfo_width()
        if w < 10:
            return
        col_positions = self._get_col_positions(w)
        for i in range(
            len(col_positions) - 2
        ):  # -2 because last column can't be resized
            edge_x = col_positions[i + 1]
            if abs(x - edge_x) < 10:
                self._resize_col = i
                self._resize_start_x = event.x_root
                self._resize_start_weights = self.col_weights.copy()
                return

    def _on_header_drag_global(self, event):
        """Handle column resize drag - works across entire header."""
        if self._resize_col is not None:
            delta = event.x_root - self._resize_start_x
            # Convert delta pixels to weight change
            total_width = max(self.canvas.winfo_width(), 1)
            total_weight = sum(self._resize_start_weights)
            weight_per_pixel = total_weight / total_width
            weight_delta = delta * weight_per_pixel

            # Adjust the resized column and the next column
            new_weight = self._resize_start_weights[self._resize_col] + weight_delta
            next_weight = (
                self._resize_start_weights[self._resize_col + 1] - weight_delta
            )

            # Enforce minimum weights
            if (
                new_weight >= self._min_col_weight
                and next_weight >= self._min_col_weight
            ):
                self.col_weights[self._resize_col] = new_weight
                self.col_weights[self._resize_col + 1] = next_weight
                self._update_header_weights()
                self.draw()

    def _on_header_release(self, event):
        """End resize operation."""
        # Small delay before clearing resize_col to prevent sort from firing
        if self._resize_col is not None:
            self.after(50, self._clear_resize)

    def _clear_resize(self):
        """Clear resize state after delay."""
        self._resize_col = None
        # Reset all cursors
        for btn in self.header_btns:
            btn.configure(cursor="")

    def _update_header_weights(self):
        """Update header button positions after weight change."""
        self._update_header_positions()

    def _get_col_positions(self, total_width):
        """Calculate column positions based on weights."""
        total_weight = sum(self.col_weights)
        positions = [0]
        cumulative = 0
        for w in self.col_weights:
            cumulative += (w / total_weight) * total_width
            positions.append(int(cumulative))
        return positions

    # ---------- sorting ----------
    def sort_by(self, col_name):
        key_map = {
            "Address": "ip",
            "Proto": "type",
            "Country": "country_code",
            "Status": "status",
            "Ping": "speed",
            "Anon": "anonymity",
        }
        key = key_map.get(col_name, "speed")
        # Toggle direction only if clicking same column, otherwise keep direction
        if self.sort_key == key:
            self.sort_reverse = not self.sort_reverse
        self.sort_key = key
        self._apply_sort()
        self._update_header_marks()

    def _update_header_marks(self):
        """Show an arrow on the sorted column header."""
        marks = {
            "ip": "Address",
            "type": "Proto",
            "country_code": "Country",
            "status": "Status",
            "speed": "Ping",
            "anonymity": "Anon",
        }
        arrow = " ▼" if self.sort_reverse else " ▲"
        for btn, col in zip(self.header_btns, self.col_map):
            label = col + (arrow if marks.get(self.sort_key) == col else "")
            try:
                btn.configure(text=label)
            except Exception:
                pass

    def _apply_sort(self, force_draw=True):
        """Apply current sort to data."""
        if not self.sort_key or not self.data:
            return
        try:
            self.data.sort(key=lambda x: x[self.sort_key], reverse=self.sort_reverse)
        except (KeyError, TypeError):
            self.data.sort(
                key=lambda x: str(x.get(self.sort_key, "")), reverse=self.sort_reverse
            )
        if self.filter_code:
            self._rebuild_view()
        if force_draw:
            self.draw()
        else:
            self._needs_draw = True

    # ---------- data ----------
    def add(self, item):
        """Add item without immediate draw - call flush() to render."""
        self.data.append(item)
        if self.filter_code and country_code_of(item) == self.filter_code:
            self._view.append(item)
        # Mark for deferred sort every 100 items (not 10 - reduces CPU)
        if self.sort_key and len(self.data) % 100 == 0:
            self._needs_sort = True
        self._needs_draw = True

    def flush(self):
        """
        Flush pending changes - apply deferred sort and draw.
        Call this periodically from GUI loop instead of drawing on every add.
        """
        if self._needs_sort and self.sort_key:
            self._apply_sort(force_draw=False)
            self._needs_sort = False

        if self._needs_draw:
            self.draw()
            self._needs_draw = False
            self._last_draw_len = len(self.data)

    def clear(self):
        self.data = []
        self._view = []
        self._needs_draw = False
        self._needs_sort = False
        self._last_draw_len = 0
        self.canvas.delete("all")
        self.draw()

    def get_active_objects(self):
        return [d for d in self.data if d["status"] == "Active"]

    def get_active(self):
        return [
            f"{d['type'].lower()}://{d['ip']}:{d['port']}"
            for d in self.data
            if d["status"] == "Active"
        ]

    def get_counts(self):
        counts = {"HTTP": 0, "HTTPS": 0, "SOCKS4": 0, "SOCKS5": 0}
        for d in self.data:
            t = d.get("type", "HTTP").upper()
            if t == "HTTPS":
                counts["HTTPS"] += 1
            elif "HTTP" in t:
                counts["HTTP"] += 1
            elif "SOCKS4" in t:
                counts["SOCKS4"] += 1
            elif "SOCKS5" in t:
                counts["SOCKS5"] += 1
        return counts

    def get_anonymity_counts(self):
        """Get counts for each anonymity level."""
        counts = {"Elite": 0, "Anonymous": 0, "Transparent": 0, "Unknown": 0}
        for d in self.data:
            if d.get("status") != "Active":
                continue
            anon = d.get("anonymity", "Unknown")
            if anon in counts:
                counts[anon] += 1
            else:
                counts["Unknown"] += 1
        return counts

    def get_all_stats(self):
        """
        Get all stats in a single pass - more efficient than calling
        get_counts() and get_anonymity_counts() separately.
        Returns (proto_counts, anon_counts)
        """
        proto = {"HTTP": 0, "HTTPS": 0, "SOCKS4": 0, "SOCKS5": 0}
        anon = {"Elite": 0, "Anonymous": 0, "Transparent": 0, "Unknown": 0}

        for d in self.data:
            # Protocol counts
            t = d.get("type", "HTTP").upper()
            if t == "HTTPS":
                proto["HTTPS"] += 1
            elif "HTTP" in t:
                proto["HTTP"] += 1
            elif "SOCKS4" in t:
                proto["SOCKS4"] += 1
            elif "SOCKS5" in t:
                proto["SOCKS5"] += 1

            # Anonymity counts (only for active proxies)
            if d.get("status") == "Active":
                a = d.get("anonymity", "Unknown")
                if a in anon:
                    anon[a] += 1
                else:
                    anon["Unknown"] += 1

        return proto, anon

    def get_view_stats(self):
        """Protocol/anonymity stats of the rows currently on screen."""
        proto = {"HTTP": 0, "HTTPS": 0, "SOCKS4": 0, "SOCKS5": 0}
        anon = {"Elite": 0, "Anonymous": 0, "Transparent": 0, "Unknown": 0}
        active = 0
        for d in self.visible_items():
            t = d.get("type", "HTTP").upper()
            if t == "HTTPS":
                proto["HTTPS"] += 1
            elif "HTTP" in t:
                proto["HTTP"] += 1
            elif "SOCKS4" in t:
                proto["SOCKS4"] += 1
            elif "SOCKS5" in t:
                proto["SOCKS5"] += 1
            if d.get("status") == "Active":
                active += 1
                a = d.get("anonymity", "Unknown")
                anon[a if a in anon else "Unknown"] += 1
        return proto, anon, active

    def _status_color(self, item) -> str:
        """Colour for the location/status cell of a row."""
        status = item.get("status", "")
        if status == "Active":
            return COLORS["success"]
        if status in ("Dead", "Timeout", "Error", "Failed"):
            return COLORS["danger"]
        return COLORS["text_dim"]

    def draw(self, _=None):
        """Paint the visible slice of the (filtered) data, animating a reveal."""
        # Update row height for current scaling
        self.row_h = int(self._base_row_h * get_scaling_factor())

        try:
            if not self.canvas.winfo_exists():
                return
        except Exception:
            return

        self.canvas.delete("all")
        items = self.visible_items()
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        total_h = len(items) * self.row_h
        self.canvas.configure(scrollregion=(0, 0, w, total_h))

        if not items:
            self._draw_empty(w, h)
            return

        progress = self._reveal_progress()
        y_off = self.canvas.yview()[0] * total_h
        start = int(y_off // self.row_h)
        end = start + int(h // self.row_h) + 2

        # Get column positions based on weights
        col_positions = self._get_col_positions(w)

        # Scale-aware font size
        font_size = max(8, int(10 * get_scaling_factor()))
        pad = int(10 * get_scaling_factor())

        for i in range(start, min(end, len(items))):
            item = items[i]
            y = i * self.row_h
            alpha = self._row_alpha(i, progress)
            row_bg = COLORS["row_alt"] if i % 2 == 0 else COLORS["bg"]
            # A row that is still cascading in keeps a bit of its surface, so the
            # list looks like it is streaming into place rather than flickering.
            self.canvas.create_rectangle(
                0, y, w, y + self.row_h, fill=row_bg, width=0
            )

            # neon edge on active proxies
            if item.get("status") == "Active":
                self.canvas.create_rectangle(
                    0,
                    y,
                    max(2, int(2 * get_scaling_factor())),
                    y + self.row_h,
                    fill=fade(COLORS["accent"], row_bg, alpha),
                    width=0,
                )

            vals = [
                f"{item.get('ip', '')}:{item.get('port', '')}",
                item.get("type", ""),
                self._country_cell(item),
                item.get("status", ""),
                f"{item.get('speed', 0)} ms",
                item.get("anonymity", ""),
            ]
            for c, val in enumerate(vals):
                color = self._cell_color(c, item, val)
                text_y = y + (self.row_h // 2)
                # rows slide in from the left while they fade
                slide = int((1.0 - alpha) * 8 * get_scaling_factor())
                text_x = col_positions[c] + pad + slide
                self.canvas.create_text(
                    text_x,
                    text_y,
                    text=str(val),
                    fill=fade(color, row_bg, alpha),
                    anchor="w",
                    font=font(font_size),
                )

    def _draw_empty(self, w: int, h: int):
        """Centred placeholder when the list (or the current filter) is empty."""
        message = "No proxies match this country" if self.filter_code else "No data"
        self.canvas.create_text(
            max(w // 2, 1),
            max(h // 2, 1),
            text=message,
            fill=COLORS["text_mute"],
            font=font(max(9, int(11 * get_scaling_factor()))),
        )

    def _country_cell(self, item) -> str:
        """Country column: "[CC] City" when known, else "[CC]" / "[??]"."""
        code = country_code_of(item)
        name = country_name_of(item)
        city = str(item.get("city") or "").strip()
        if city and code != "??":
            return f"[{code}] {city}"
        if name:
            return f"[{code}] {name}"
        return f"[{code}]"

    def _cell_color(self, column_index: int, item, value) -> str:
        """Colour-code a cell: protocol, status, ping and anonymity each have a scale."""
        if column_index == 1:  # Protocol
            proto = str(value).upper()
            if proto == "HTTP":
                return COLORS["info"]
            if proto == "HTTPS":
                return COLORS["accent_glow"]
            if "SOCKS5" in proto:
                return COLORS["accent_2"]
            if "SOCKS4" in proto:
                return COLORS["success"]
            return COLORS["text"]
        if column_index == 2:  # Country
            return COLORS["text_dim"]
        if column_index == 3:  # Status
            return self._status_color(item)
        if column_index == 4:  # Ping
            speed = item.get("speed", 9999) or 9999
            if speed <= 2500:
                return COLORS["success"]
            if speed <= 5000:
                return COLORS["warning"]
            if speed <= 7500:
                return "#F59E0B"
            return COLORS["danger"]
        if column_index == 5:  # Anonymity
            anon = item.get("anonymity", "Unknown")
            if anon == "Elite":
                return COLORS["success"]
            if anon == "Anonymous":
                return COLORS["warning"]
            if anon == "Transparent":
                return COLORS["danger"]
            return COLORS["text_dim"]
        return COLORS["text"]

class SectionCard(ctk.CTkFrame):
    """A titled panel: small caps title, optional right-hand slot, content area.

    Used to group the proxy toolbar into "Actions / Filter / Test" blocks so the
    page reads top-to-bottom instead of as one long strip of controls.
    """

    def __init__(self, master, title: str, **kwargs):
        kwargs.setdefault("fg_color", COLORS["card"])
        kwargs.setdefault("corner_radius", RADIUS["lg"])
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", COLORS["border_soft"])
        super().__init__(master, **kwargs)

        self.head = ctk.CTkFrame(self, fg_color="transparent")
        self.head.pack(fill="x", padx=10, pady=(7, 0))

        self.title_label = ctk.CTkLabel(
            self.head,
            text=title.upper(),
            font=font(max(9, 10), "bold"),
            text_color=COLORS["text_mute"],
        )
        self.title_label.pack(side="left")

        # right-hand slot for badges / inline actions
        # (height=1: an empty CTkFrame would otherwise claim its 200 px default
        # height and stretch the whole card)
        self.slot = ctk.CTkFrame(self.head, fg_color="transparent", height=1)
        self.slot.pack(side="right")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=10, pady=(5, 9))


class StatCard(ctk.CTkFrame):
    """A compact metric tile: caption on top, big value under it."""

    def __init__(self, master, caption: str, value: str = "0", tone: str = "text", **kwargs):
        kwargs.setdefault("fg_color", COLORS["surface"])
        kwargs.setdefault("corner_radius", RADIUS["md"])
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", COLORS["border_soft"])
        super().__init__(master, **kwargs)

        self._tone = tone
        self.caption = ctk.CTkLabel(
            self,
            text=caption,
            font=font(max(8, 9)),
            text_color=COLORS["text_mute"],
            anchor="w",
        )
        self.caption.pack(fill="x", padx=10, pady=(7, 0))

        self.value = ctk.CTkLabel(
            self,
            text=value,
            font=font(max(11, 14), "bold"),
            text_color=COLORS.get(tone, COLORS["text"]),
            anchor="w",
        )
        self.value.pack(fill="x", padx=10, pady=(0, 7))

    def set_value(self, text: str, tone: str | None = None):
        """Update the number and (optionally) its colour tone."""
        try:
            self.value.configure(text=text)
            if tone and tone != self._tone:
                self._tone = tone
                self.value.configure(text_color=COLORS.get(tone, COLORS["text"]))
        except Exception:
            pass


class _CountryRow(ctk.CTkFrame):
    """One clickable country line inside the picker: badge, name, counts."""

    def __init__(self, master, entry: dict, on_click, index: int = 0):
        super().__init__(
            master,
            fg_color="transparent",
            corner_radius=RADIUS["sm"],
            height=40,
        )
        self.pack_propagate(False)
        self.entry = entry
        self.on_click = on_click
        self._hover_job = None
        self._hover_t = 0.0

        code = entry.get("code", "??")
        name = entry.get("name") or {
            "??": "Unknown location",
        }.get(code, code)
        count = entry.get("count", 0)
        active = entry.get("active", 0)
        best = entry.get("best")

        badge = ctk.CTkLabel(
            self,
            text=code,
            width=34,
            height=22,
            corner_radius=RADIUS["sm"],
            fg_color=COLORS["accent_soft"],
            text_color=COLORS["accent_glow"],
            font=font(max(9, 10), "bold"),
        )
        badge.pack(side="left", padx=(8, 8))

        text_wrap = ctk.CTkFrame(self, fg_color="transparent")
        text_wrap.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(
            text_wrap,
            text=name[:28],
            font=font(max(9, 11)),
            text_color=COLORS["text"] if count else COLORS["text_dim"],
            anchor="w",
        ).pack(fill="x", pady=(5, 0))

        if count:
            sub = f"{count} prox" + ("" if count == 1 else "ies")
            if active:
                sub += f" · {active} active"
            if best:
                sub += f" · {int(best)} ms"
        else:
            sub = "no proxies yet"
        ctk.CTkLabel(
            text_wrap,
            text=sub,
            font=font(max(8, 9)),
            text_color=COLORS["text_mute"],
            anchor="w",
        ).pack(fill="x")

        self.count_label = ctk.CTkLabel(
            self,
            text=str(count) if count else "–",
            font=font(max(9, 11), "bold"),
            text_color=COLORS["accent_2"] if count else COLORS["text_mute"],
        )
        self.count_label.pack(side="right", padx=(6, 10))

        # Everything in the row is clickable and hovers as one block
        for widget in (self, badge, text_wrap, self.count_label):
            widget.bind("<Button-1>", self._pick)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
        for child in text_wrap.winfo_children():
            child.bind("<Button-1>", self._pick)
            child.bind("<Enter>", self._on_enter)
            child.bind("<Leave>", self._on_leave)

    def _pick(self, _event=None):
        self.on_click(self.entry)

    def _on_enter(self, _event=None):
        self._animate(1.0)

    def _on_leave(self, _event=None):
        self._animate(0.0)

    def _animate(self, target: float):
        """Ease the hover wash in/out (tkinter has no CSS transitions)."""
        if self._hover_job is not None:
            try:
                self.after_cancel(self._hover_job)
            except Exception:
                pass
            self._hover_job = None

        def step():
            self._hover_job = None
            if not self.winfo_exists():
                return
            delta = target - self._hover_t
            if abs(delta) < 0.06:
                self._hover_t = target
            else:
                self._hover_t += delta * 0.35
            try:
                self.configure(
                    fg_color=mix(COLORS["card"], COLORS["surface_2"], self._hover_t)
                )
            except Exception:
                return
            if self._hover_t != target:
                self._hover_job = self.after(MOTION["frame_ms"], step)

        step()


class CountrySelect(ctk.CTkFrame):
    """Click-to-open country picker with an animated drop-down panel.

    Closed it is one button ("All countries"). Clicking it grows a panel open
    (animated height + cascading rows) holding a search box and one clickable row
    per country, each with its proxy count. Picking a row fires ``on_pick``.
    """

    PANEL_ROWS = 7  # rows visible before the list scrolls
    SLIDE = 14      # px the panel travels while opening (slide, not grow)
    MIN_H = 90      # never place it thinner than this: a ~0 px panel is "unmapped"

    def __init__(self, master, on_pick=None, width: int = 220, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.on_pick = on_pick
        self.width = width
        self.code = None
        self.name = ""
        self._entries: list = []
        self._panel = None
        self._rows_frame = None
        self._search = None
        self._anim_job = None
        self._total = 0
        self._open_height = int((self.PANEL_ROWS + 1.6) * 40)

        self.btn = ctk.CTkButton(
            self,
            text="All countries   ▾",
            width=width,
            height=34,
            anchor="w",
            corner_radius=RADIUS["md"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["surface_2"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            font=font(max(9, 11)),
            command=self.toggle,
        )
        self.btn.pack(fill="x")

    # ---------- open / close ----------
    def toggle(self):
        """Open the panel, or close it when it is already open."""
        if self._panel is not None:
            self.close()
        else:
            self.open()

    def open(self):
        """Open the country list.

        Preferred form is a floating panel pinned under the button. On some
        setups (customtkinter builds where a placed frame never becomes mapped)
        that shows nothing, so the panel checks itself shortly after opening and
        falls back to an inline list - the list must always appear.
        """
        if self._panel is not None:
            return
        self._build_panel(floating=True)
        self.after(240, self._ensure_visible)

    def _build_panel(self, floating: bool, query: str = ""):
        """Create the panel either floating (placed) or inline (packed)."""
        parent = self.winfo_toplevel() if floating else self
        self._floating = floating
        self._panel = ctk.CTkFrame(
            parent,
            fg_color=COLORS["card"],
            corner_radius=RADIUS["lg"],
            border_width=1,
            border_color=COLORS["border"],
        )
        if not floating:
            self._panel.pack(fill="x", pady=(5, 0))

        head = ctk.CTkFrame(self._panel, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(
            head,
            text="FILTER BY COUNTRY",
            font=font(max(8, 9), "bold"),
            text_color=COLORS["text_mute"],
        ).pack(side="left")
        ctk.CTkButton(
            head,
            text="Clear",
            width=52,
            height=22,
            corner_radius=RADIUS["sm"],
            fg_color="transparent",
            hover_color=COLORS["surface_2"],
            text_color=COLORS["accent_glow"],
            font=font(max(8, 9)),
            command=lambda: self._choose(None),
        ).pack(side="right")

        self._search = ctk.CTkEntry(
            self._panel,
            placeholder_text="Search any country or code",
            height=30,
            corner_radius=RADIUS["md"],
            fg_color=COLORS["field"],
            border_color=COLORS["border"],
            font=font(max(9, 10)),
        )
        self._search.pack(fill="x", padx=10, pady=(0, 6))
        self._search.bind("<KeyRelease>", lambda e: self._render_rows())
        self._search.bind("<Escape>", lambda e: self.close())
        if query:
            self._search.insert(0, query)

        rows_kwargs = {"height": self._open_height} if not floating else {}
        self._rows_frame = ctk.CTkScrollableFrame(
            self._panel,
            fg_color="transparent",
            scrollbar_button_color=COLORS["surface_2"],
            scrollbar_button_hover_color=COLORS["accent"],
            **rows_kwargs,
        )
        if floating:
            self._rows_frame.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        else:
            self._rows_frame.pack(fill="x", padx=6, pady=(0, 8))

        if floating:
            x = self.winfo_rootx() - parent.winfo_rootx()
            y = self.winfo_rooty() - parent.winfo_rooty() + self.winfo_height() + 4
            width = max(self.width, self.winfo_width())
            self._place_x, self._place_y, self._place_w = x, y, width
            try:
                self._panel.configure(width=width, height=self.MIN_H)
                self._panel.place(x=x, y=y)
                self._panel.lift()
            except Exception as exc:  # never hide a layout failure again
                print(f"[CountrySelect] floating open failed: {exc!r}", flush=True)

        self._render_rows(cascade=True)
        self._bind_outside()
        try:
            self._search.focus_set()
        except Exception:
            pass

    def _ensure_visible(self):
        """Fall back to the inline list if the floating panel did not show up."""
        panel = self._panel
        if panel is None or not getattr(self, "_floating", False):
            return
        try:
            if panel.winfo_ismapped() and panel.winfo_height() > 8:
                self._start_height_ramp(self.MIN_H, self._open_height)
                return
        except Exception:
            pass
        query = ""
        try:
            query = self._search.get()
        except Exception:
            pass
        try:
            panel.place_forget()
            panel.destroy()
        except Exception:
            pass
        self._rows_frame = None
        self._search = None
        self._panel = None
        self._build_panel(floating=False, query=query)

    def _start_height_ramp(self, from_h: int, to_h: int):
        """Grow the floating panel smoothly (tkinter has no transitions)."""
        panel = self._panel
        if panel is None:
            return
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        steps = max(1, MOTION["base"] // MOTION["frame_ms"])
        state = {"i": 0}

        def step():
            self._anim_job = None
            state["i"] += 1
            t = ease_out(state["i"] / steps)
            try:
                panel.configure(width=self._place_w, height=max(1, int(from_h + (to_h - from_h) * t)))
                panel.place(x=self._place_x, y=self._place_y)
                panel.lift()
            except Exception:
                return
            if state["i"] < steps:
                self._anim_job = self.after(MOTION["frame_ms"], step)

        step()

    def close(self):
        """Collapse the panel and destroy it."""
        if self._panel is None:
            return
        self._unbind_outside()
        panel = self._panel
        self._panel = None
        self._rows_frame = None
        self._search = None
        try:
            panel.place_forget()
            panel.pack_forget()
            panel.destroy()
        except Exception:
            pass

    def _bind_outside(self):
        """Close the panel on any click that is not inside it."""
        try:
            self._top = self.winfo_toplevel()
            self._top_bind = self._top.bind("<Button-1>", self._on_global_click, add="+")
        except Exception:
            self._top = None

    def _unbind_outside(self):
        try:
            if getattr(self, "_top", None) and getattr(self, "_top_bind", None):
                self._top.unbind("<Button-1>", self._top_bind)
        except Exception:
            pass
        self._top = None
        self._top_bind = None

    def _on_global_click(self, event):
        """A click anywhere else closes the drop-down."""
        widget = getattr(event, "widget", None)
        for root in (self, self.btn, self._panel, self._search):
            node = widget
            while node is not None:
                if node is root:
                    return
                node = getattr(node, "master", None)
        self.close()

    # ---------- content ----------
    # ---------- entries ----------
    @staticmethod
    def _zero_entry(code: str) -> dict:
        """A country that has no proxies yet (still selectable)."""
        return {
            "code": code,
            "name": name_of(code),
            "count": 0,
            "active": 0,
            "best": None,
        }

    def _filtered_entries(self, query: str) -> list:
        """Rows to show: what you have, or - when searching - every ISO country.

        With an empty search box the list stays short (the countries that
        actually have proxies). Typing searches the full 249-country table, so a
        filter can be set before anything is loaded ("proxy chara").
        """
        have = list(self._entries)
        if not query:
            if have:
                return have[:60]
            # nothing loaded yet: show a slice of the full table so the picker is
            # never an empty box
            return [self._zero_entry(code) for code in list(COUNTRIES)[:60]]

        hits = []
        seen = set()
        for entry in have:
            text = f"{entry['code']} {entry.get('name') or ''}".lower()
            if query in text:
                hits.append(entry)
                seen.add(entry["code"])
        for code, name in COUNTRIES.items():
            if code in seen:
                continue
            if query in code.lower() or query in name.lower():
                hits.append(self._zero_entry(code))
        return hits[:80]

    def _render_rows(self, cascade: bool = False):
        """(Re)draw the country rows; optionally cascade them in one by one."""
        if self._rows_frame is None:
            return
        query = ""
        try:
            query = (self._search.get() or "").strip().lower()
        except Exception:
            pass

        for child in self._rows_frame.winfo_children():
            child.destroy()

        entries = self._filtered_entries(query)

        if not entries:
            ctk.CTkLabel(
                self._rows_frame,
                text=f'No country matches "{query}"'
                if query
                else "No countries to show",
                text_color=COLORS["text_mute"],
                font=font(max(9, 10)),
            ).pack(pady=12)
            return

        # a hint when the list is the alphabetical fallback (nothing loaded yet)
        if not query and not self._total:
            ctk.CTkLabel(
                self._rows_frame,
                text="No proxies loaded yet - pick a country now, or type to search",
                text_color=COLORS["text_mute"],
                font=font(max(8, 9)),
                wraplength=320,
                justify="left",
            ).pack(fill="x", padx=8, pady=(2, 6))

        # "All countries" always sits on top as the reset action
        if self.code:
            entries = [None] + entries

        rows = []
        for i, entry in enumerate(entries):
            data = entry or {
                "code": "ALL",
                "name": "All countries",
                "count": self._total,
                "active": 0,
                "best": None,
            }
            rows.append(_CountryRow(self._rows_frame, data, self._choose, index=i))

        # Cascade: the first row lands immediately, the rest follow a few ms apart
        for i, row in enumerate(rows):
            if not cascade or i == 0:
                row.pack(fill="x", pady=1)
            else:

                def show(r=row):
                    try:
                        if r.winfo_exists() and r.master is self._rows_frame:
                            r.pack(fill="x", pady=1)
                    except Exception:
                        pass

                self.after(26 * i, show)

    def set_options(self, entries: list, total: int = 0):
        """Feed the picker with country aggregates ({ code, name, count, ... })."""
        self._entries = entries or []
        self._total = total or sum(e.get("count", 0) for e in self._entries)
        if self._panel is not None:
            self._render_rows()

    def set_active(self, code: str | None, name: str = "", count: int | None = None):
        """Update the button label to reflect the current filter."""
        self.code = (code or "").upper() or None
        self.name = name or ""
        if not self.code:
            self.btn.configure(
                text="All countries   ▾",
                border_color=COLORS["border"],
                text_color=COLORS["text"],
            )
            return
        label = f"{self.name or self.code}"
        if count is not None:
            label += f"  ·  {count}"
        label += "   ▾"
        self.btn.configure(
            text=label, border_color=COLORS["accent"], text_color=COLORS["text"]
        )

    def _choose(self, entry):
        """Row click: tell the page, then close the panel."""
        code = None if entry is None else entry.get("code")
        name = "" if entry is None else (entry.get("name") or "")
        if code == "ALL":
            code = None
        self.close()
        if self.on_pick:
            self.on_pick(code, name)


class ActivityLog(ctk.CTkFrame):
    """Scrollback log panel; newest line at the bottom, capped at 1000 lines."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", COLORS["card"])
        kwargs.setdefault("corner_radius", RADIUS["lg"])
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", COLORS["border_soft"])
        super().__init__(master, **kwargs)

        self._log_lock = False  # Simple lock to prevent concurrent updates

        # Scale-aware height
        min_h = 100

        self.txt = ctk.CTkTextbox(
            self,
            fg_color=COLORS["field"],
            wrap="word",
            height=min_h,
            corner_radius=RADIUS["md"],
            border_width=0,
            scrollbar_button_color=COLORS["surface_2"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        self.txt.pack(fill="both", expand=True, padx=5, pady=5)

        # Configure text color
        self.txt._textbox.configure(foreground=COLORS.get("text", "#ffffff"))
        try:
            self.txt._textbox.configure(
                insertbackground=COLORS["accent_2"],
                background=COLORS["field"],
            )
        except Exception:
            pass

    def log(self, message: str):
        """Thread-safe logging with auto-scroll."""
        if self._log_lock:
            return
        try:
            self._log_lock = True
            self.txt.insert("end", f"{message}\n")
            # Keep only last 1000 lines
            lines = int(self.txt.index("end-1c").split(".")[0])
            if lines > 1000:
                self.txt.delete("1.0", f"{lines - 1000}.0")
            # Auto-scroll to bottom
            self.txt.see("end")
        finally:
            self._log_lock = False

    def clear(self):
        self.txt.delete("1.0", "end")
