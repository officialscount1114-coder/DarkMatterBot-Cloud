"""MR ADS design system - palette, radii, type and motion tokens.

This module is the single source of truth for the look of the whole UI. Every
page draws its colours from ``COLORS``, so editing the palette here restyles the
entire application at once.

The theme is "carbon azure": neutral near-black surfaces with a royal-blue
accent and a cyan companion, softer (larger) corners and roomier spacing. The
original keys (bg / nav / card / accent / accent_hover / success / danger /
warning / text / text_dim / border) are kept, so existing widgets keep working;
everything else is additive.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Colour tokens
# --------------------------------------------------------------------------
COLORS: dict[str, str] = {
    # --- surfaces, darkest to lightest -----------------------------------
    "bg": "#0A0C12",          # window / page background
    "nav": "#0F121A",         # sidebar + grid header
    "card": "#161A24",        # panels, cards
    "surface": "#1D2230",     # nested surfaces (rows, insets)
    "surface_2": "#28303F",   # hover / raised surface
    "field": "#10131B",       # inputs, entries, canvases
    # --- lines ------------------------------------------------------------
    "border": "#2A3145",      # main border
    "border_soft": "#1E2431", # dividers, hairlines
    # --- rows -------------------------------------------------------------
    "row_alt": "#12161F",     # zebra rows
    "row_hover": "#232B3D",   # hovered row
    # --- brand / accents --------------------------------------------------
    "accent": "#3B82F6",      # primary royal blue
    "accent_hover": "#2563EB",
    "accent_2": "#22D3EE",    # cyan companion
    "accent_soft": "#152238", # accent-washed surface
    "accent_glow": "#60A5FA",
    "brand_1": "#3B82F6",     # brand lockup gradient (start)
    "brand_2": "#22D3EE",     # brand lockup gradient (end)
    "strip": "#22D3EE",       # thin accent strip on cards
    # --- status -----------------------------------------------------------
    "success": "#2DD4A7",
    "danger": "#F87171",
    "warning": "#F5B93E",
    "info": "#38BDF8",
    # --- text -------------------------------------------------------------
    "text": "#EDF1F8",
    "text_dim": "#9BA6BE",
    "text_mute": "#616C86",
}

# --------------------------------------------------------------------------
# Geometry / type / motion tokens
# --------------------------------------------------------------------------
RADIUS = {"sm": 8, "md": 12, "lg": 16, "xl": 20, "pill": 999}

MOTION = {
    "frame_ms": 16,      # ~60 fps animation tick
    "fast": 140,         # hover / press
    "base": 220,         # panel transitions
    "slow": 360,         # page switch
    "reveal": 520,       # staggered list reveal
}

FONT = "Roboto"

#: Uniform spacing scale, so every panel breathes the same way.
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 18, "xl": 26}


def font(size: int, weight: str = "normal") -> tuple:
    """Return a tkinter font tuple in the app font family.

    Args:
        size: Point size (already scaled by the caller).
        weight: "normal" or "bold".
    """
    return (FONT, size, weight) if weight != "normal" else (FONT, size)


# --------------------------------------------------------------------------
# Colour helpers (used by the animation code)
# --------------------------------------------------------------------------
def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _to_rgb(color: str) -> tuple[int, int, int]:
    """Parse ``#rrggbb`` into an (r, g, b) tuple; unknown values become black."""
    try:
        c = color.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, AttributeError):
        return (0, 0, 0)


def mix(first: str, second: str, t: float) -> str:
    """Blend two hex colours: ``t=0`` returns ``first``, ``t=1`` returns ``second``."""
    t = _clamp(t)
    r1, g1, b1 = _to_rgb(first)
    r2, g2, b2 = _to_rgb(second)
    return "#{:02x}{:02x}{:02x}".format(
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    )


def fade(color: str, background: str, t: float) -> str:
    """Fade ``color`` toward ``background``: ``t=0`` invisible, ``t=1`` full colour.

    tkinter has no per-item alpha, so an animation "fades" by walking the item
    colour toward the surface it sits on.
    """
    return mix(background, color, t)


def ease_out(t: float) -> float:
    """Cubic ease-out, clamped to 0..1 - the standard curve for the animations."""
    t = _clamp(t)
    return 1.0 - (1.0 - t) ** 3


def ease_in_out(t: float) -> float:
    """Smooth acceleration then deceleration, clamped to 0..1."""
    t = _clamp(t)
    return 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2
