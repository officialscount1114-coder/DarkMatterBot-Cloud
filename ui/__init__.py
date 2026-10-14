"""UI package for DarkMatter Traffic Bot.

This package contains the user interface components:
- app.py: Main application window (ModernTrafficBot)
- components.py: Reusable UI components (VirtualGrid, CountrySelect, SectionCard,
  StatCard, ActivityLog, DraggableSash)
- styles.py: Design system (palette, radii, motion tokens, colour helpers)
- utils.py: Utility functions (settings, validation)
- scaling.py: DPI scaling utilities
- pages/: Page-specific UI modules
"""

from .scaling import get_scaling_factor, scaled
from .styles import COLORS, MOTION, RADIUS, ease_out, fade, font, mix
from .utils import Utils

__all__ = [
    "scaled",
    "get_scaling_factor",
    "COLORS",
    "RADIUS",
    "MOTION",
    "font",
    "mix",
    "fade",
    "ease_out",
    "Utils",
]

try:
    from .components import (
        ActivityLog,
        CountrySelect,
        DraggableSash,
        SectionCard,
        StatCard,
        VirtualGrid,
        country_code_of,
    )
    __all__.extend([
        "VirtualGrid",
        "CountrySelect",
        "SectionCard",
        "StatCard",
        "ActivityLog",
        "DraggableSash",
        "country_code_of",
    ])
except (ImportError, ModuleNotFoundError):
    # Headless / Server / CI environments without Tkinter
    pass
