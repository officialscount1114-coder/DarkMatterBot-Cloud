"""Headless subset of the ui package: settings/utility helpers only.

The full ui package imports customtkinter and the page modules, which need a
display. Only ui.utils is shipped to servers, so this __init__ must stay free
of GUI imports.
"""

