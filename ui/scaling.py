"""DPI scaling utilities for customtkinter widgets.

Important: customtkinter already scales every dimension and font it is given
(``CTkBaseClass._apply_widget_scaling``), so a value that goes into a CTk widget
must NOT be pre-multiplied by the scaling factor - doing that scales everything
twice and the layout overflows (sidebars too wide, cards clipped, widgets pushed
off-window) on any display that is not at 100%.

``scaled()`` therefore returns the base value unchanged: it exists so call sites
stay explicit and so a future non-CTk backend has one place to hook into.

``get_scaling_factor()`` is still the real factor and is used where it is
genuinely needed: raw tkinter canvases (the proxy grid draws with absolute
pixel coordinates, and those are not scaled by CTk).
"""

try:
    import customtkinter as ctk
except (ImportError, ModuleNotFoundError):
    ctk = None


def get_scaling_factor() -> float:
    """Current widget scaling factor (1.0 = 100% DPI).

    ``ScalingTracker.get_widget_scaling()`` needs a real widget and raises when
    it is handed ``None``; the old code swallowed that error and silently
    reported 1.0 on every display. Everything drawn on a raw tkinter canvas (the
    proxy grid rows) was therefore left at base size while customtkinter scaled
    the surrounding widget chrome, so the grid, its header and its rows visibly
    disagreed on any display that is not at 100%.

    The factor is read straight from the tracker instead - the tracked per-window
    DPI scaling multiplied by the user scaling, which is exactly the product
    customtkinter applies to every widget dimension.
    """
    if ctk is None:
        return 1.0
    try:
        tracker = ctk.ScalingTracker
        dpi_factors = [
            float(value)
            for value in (getattr(tracker, "window_dpi_scaling_dict", None) or {}).values()
            if value
        ]
        dpi = max(dpi_factors) if dpi_factors else 1.0
        user = float(getattr(tracker, "widget_scaling", 1) or 1)
        factor = dpi * user
        return factor if factor > 0 else 1.0
    except Exception:
        return 1.0


def scaled(base_value: int) -> int:
    """Return a widget dimension unchanged (customtkinter scales it itself)."""
    return int(base_value)


def canvas_scaled(base_value: int) -> int:
    """Scale a value used for raw canvas drawing (CTk does not scale those)."""
    return int(base_value * get_scaling_factor())
