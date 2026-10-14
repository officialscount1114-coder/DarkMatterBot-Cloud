"""Bulk proxy import window: paste or load any size of list in one go.

The window only collects the input and the options; the actual parsing runs in
``ModernTrafficBot.start_bulk_import`` on a worker thread and reports back here,
so a million-line import never freezes the UI.
"""

from __future__ import annotations

import os
from tkinter import filedialog
from typing import TYPE_CHECKING

import customtkinter as ctk

from .scaling import scaled
from .styles import COLORS, RADIUS, font

if TYPE_CHECKING:
    from .app import ModernTrafficBot

HINT = (
    "One proxy per line. ip:port · user:pass@ip:port · http://ip:port · "
    "socks5://user:pass@ip:port · ip:port:user:pass — commas/spaces also work. "
    "No limit on the number of lines."
)


class BulkAddWindow(ctk.CTkToplevel):
    """Modal-ish bulk import window (paste box + file picker + options)."""

    def __init__(self, app: "ModernTrafficBot"):
        super().__init__(app)
        self.app = app

        self.title("Add proxies - bulk import")
        self.geometry(f"{scaled(760)}x{scaled(600)}")
        self.minsize(scaled(620), scaled(460))
        self.configure(fg_color=COLORS["bg"])
        self.transient(app)

        self._file_path = ""

        # ---- header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(
            head, text="Add proxies in bulk", font=font(scaled(16), "bold"), text_color=COLORS["text"]
        ).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text=HINT,
            font=font(scaled(10)),
            text_color=COLORS["text_mute"],
            justify="left",
            wraplength=scaled(700),
        ).pack(anchor="w", pady=(2, 0))

        # ---- paste area
        self.txt = ctk.CTkTextbox(
            self,
            fg_color=COLORS["field"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=RADIUS["md"],
            font=font(scaled(11)),
            wrap="none",
            scrollbar_button_color=COLORS["surface_2"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        self.txt.pack(fill="both", expand=True, padx=16, pady=(6, 6))

        # ---- source buttons
        src = ctk.CTkFrame(self, fg_color="transparent")
        src.pack(fill="x", padx=16)

        self._btn(src, "Paste from clipboard", self._from_clipboard, tone="accent").pack(side="left")
        self._btn(src, "Load file...", self._from_file, tone="accent").pack(side="left", padx=6)
        self._btn(src, "Clear text", self._clear, tone="ghost").pack(side="left", padx=6)

        self.lbl_source = ctk.CTkLabel(
            src, text="", font=font(scaled(10)), text_color=COLORS["text_mute"]
        )
        self.lbl_source.pack(side="right")

        # ---- options
        opts = ctk.CTkFrame(self, fg_color=COLORS["card"], corner_radius=RADIUS["md"])
        opts.pack(fill="x", padx=16, pady=(10, 6))

        row1 = ctk.CTkFrame(opts, fg_color="transparent")
        row1.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(row1, text="Default protocol", font=font(scaled(10)), text_color=COLORS["text_mute"]).pack(
            side="left", padx=(0, 6)
        )
        self.combo_proto = ctk.CTkComboBox(
            row1,
            values=["http", "https", "socks4", "socks5"],
            width=scaled(100),
            height=scaled(28),
            fg_color=COLORS["field"],
            border_color=COLORS["border"],
            button_color=COLORS["surface_2"],
            button_hover_color=COLORS["accent"],
            font=font(scaled(11)),
            dropdown_font=font(scaled(11)),
        )
        self.combo_proto.set("http")
        self.combo_proto.pack(side="left")

        ctk.CTkLabel(row1, text="Max (0 = unlimited)", font=font(scaled(10)), text_color=COLORS["text_mute"]).pack(
            side="left", padx=(18, 6)
        )
        self.entry_max = ctk.CTkEntry(
            row1,
            width=scaled(90),
            height=scaled(28),
            fg_color=COLORS["field"],
            border_color=COLORS["border"],
            font=font(scaled(11)),
        )
        self.entry_max.insert(0, "0")
        self.entry_max.pack(side="left")

        row2 = ctk.CTkFrame(opts, fg_color="transparent")
        row2.pack(fill="x", padx=12, pady=(2, 10))
        self.chk_dupes = self._check(row2, "Skip duplicates", True)
        self.chk_dupes.pack(side="left", padx=(0, 14))
        self.chk_replace = self._check(row2, "Replace existing list", False)
        self.chk_replace.pack(side="left", padx=(0, 14))
        self.chk_test = self._check(row2, "Test after adding", False, tone="success")
        self.chk_test.pack(side="left")

        # ---- progress + actions
        prog = ctk.CTkFrame(self, fg_color="transparent")
        prog.pack(fill="x", padx=16, pady=(4, 2))
        self.bar = ctk.CTkProgressBar(
            prog, height=scaled(8), progress_color=COLORS["accent"], fg_color=COLORS["surface"]
        )
        self.bar.set(0)
        self.bar.pack(fill="x")

        self.lbl_status = ctk.CTkLabel(
            self, text="Ready - paste a list or load a file.", font=font(scaled(10)),
            text_color=COLORS["text_dim"], anchor="w",
        )
        self.lbl_status.pack(fill="x", padx=16)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=(6, 12))

        self.btn_add = ctk.CTkButton(
            actions,
            text="ADD PROXIES",
            width=scaled(150),
            height=scaled(36),
            corner_radius=RADIUS["md"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=font(scaled(12), "bold"),
            command=self._start,
        )
        self.btn_add.pack(side="left")

        self.btn_cancel = ctk.CTkButton(
            actions,
            text="Cancel",
            width=scaled(100),
            height=scaled(36),
            corner_radius=RADIUS["md"],
            fg_color="transparent",
            border_width=1,
            border_color=COLORS["border"],
            hover_color=COLORS["surface_2"],
            text_color=COLORS["text_dim"],
            font=font(scaled(11)),
            state="disabled",
            command=self.app.cancel_bulk_import,
        )
        self.btn_cancel.pack(side="left", padx=8)

        self.btn_close = ctk.CTkButton(
            actions,
            text="Close",
            width=scaled(90),
            height=scaled(36),
            corner_radius=RADIUS["md"],
            fg_color="transparent",
            hover_color=COLORS["surface_2"],
            text_color=COLORS["text_dim"],
            font=font(scaled(11)),
            command=self.destroy,
        )
        self.btn_close.pack(side="right")

    # ---------- small builders ----------
    def _btn(self, parent, text: str, command, tone: str = "accent") -> ctk.CTkButton:
        if tone == "ghost":
            return ctk.CTkButton(
                parent, text=text, width=scaled(96), height=scaled(30),
                corner_radius=RADIUS["md"], fg_color="transparent", border_width=1,
                border_color=COLORS["border"], hover_color=COLORS["surface_2"],
                text_color=COLORS["text_dim"], font=font(scaled(11)), command=command,
            )
        return ctk.CTkButton(
            parent, text=text, width=scaled(140), height=scaled(30),
            corner_radius=RADIUS["md"], fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"], font=font(scaled(11)), command=command,
        )

    def _check(self, parent, text: str, checked: bool, tone: str = "accent") -> ctk.CTkCheckBox:
        box = ctk.CTkCheckBox(
            parent, text=text, font=font(scaled(11)), checkbox_width=scaled(16),
            checkbox_height=scaled(16), corner_radius=RADIUS["sm"],
            border_color=COLORS["border"], fg_color=COLORS[tone], hover_color=COLORS["accent_hover"],
        )
        if checked:
            box.select()
        return box

    # ---------- sources ----------
    def _from_clipboard(self):
        try:
            text = self.clipboard_get()
        except Exception:
            self.lbl_status.configure(text="Clipboard is empty or unavailable.")
            return
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", text)
        self.lbl_source.configure(text=f"clipboard: {text.count(chr(10)) + 1:,} lines")

    def _from_file(self):
        path = filedialog.askopenfilename(
            title="Select a proxy list",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        self._file_path = path
        size_mb = os.path.getsize(path) / (1024 * 1024)
        self.lbl_source.configure(text=f"file: {os.path.basename(path)} ({size_mb:.1f} MB)")
        self.lbl_status.configure(
            text="File selected - ADD PROXIES will stream it (the text box is ignored)."
        )

    def _clear(self):
        self.txt.delete("1.0", "end")
        self._file_path = ""
        self.lbl_source.configure(text="")

    # ---------- run ----------
    def _start(self):
        text = ""
        if not self._file_path:
            text = self.txt.get("1.0", "end")
            if not text.strip():
                self.lbl_status.configure(text="Nothing to add - paste a list or load a file.")
                return

        def _int(value: str, default: int = 0) -> int:
            try:
                return max(0, int(str(value).strip()))
            except (TypeError, ValueError):
                return default

        options = {
            "text": text,
            "path": self._file_path,
            "default_protocol": self.combo_proto.get(),
            "max_items": _int(self.entry_max.get(), 0),
            "skip_duplicates": bool(self.chk_dupes.get()),
            "replace": bool(self.chk_replace.get()),
            "test_after": bool(self.chk_test.get()),
        }
        self.btn_add.configure(state="disabled", text="ADDING...")
        self.btn_cancel.configure(state="normal")
        self.btn_close.configure(state="disabled")
        self.bar.set(0)
        self.lbl_status.configure(text="Starting import ...")
        self.app.start_bulk_import(options, self)

    # ---------- callbacks from the worker ----------
    def set_progress(self, fraction: float, text: str):
        """Called (on the UI thread) while the import runs."""
        try:
            self.bar.set(max(0.0, min(1.0, fraction)))
            self.lbl_status.configure(text=text)
        except Exception:
            pass

    def finish(self, summary: str, failed: bool = False):
        """Called when the import ends, was cancelled, or failed."""
        try:
            self.btn_add.configure(state="normal", text="ADD PROXIES")
            self.btn_cancel.configure(state="disabled")
            self.btn_close.configure(state="normal")
            self.bar.set(0 if failed else 1)
            self.lbl_status.configure(
                text=summary, text_color=COLORS["danger"] if failed else COLORS["success"]
            )
        except Exception:
            pass
