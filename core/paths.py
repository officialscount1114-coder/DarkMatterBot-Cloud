"""Path resolution that works both from source and from a PyInstaller build.

Two different kinds of file live under ``resources/`` and they must not be mixed
up inside a ``--onefile`` exe:

* read-only assets shipped with the app (GeoLite2 database, user-agents,
  referers, sources). PyInstaller unpacks them to a temp dir at every start and
  exposes that dir as ``sys._MEIPASS``.
* writable state produced while running (settings.json, proxies.json,
  sessions.json, source_health.json). Those must live *next to the exe*, because
  ``sys._MEIPASS`` is thrown away when the process exits.

From source both helpers simply return the same relative path the code used
before (``resources/...``), so the development workflow is unchanged.
"""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    """True when running from a PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """The folder the app lives in: the exe's folder, or the source root."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    # core/paths.py -> project root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_path(relative: str) -> str:
    """Absolute path of a read-only bundled asset.

    In a build the file is inside the unpacked bundle; when running from source
    it is resolved against the project root (falling back to the given relative
    path so behaviour stays exactly as it was).
    """
    relative = relative.replace("\\", "/").lstrip("/")
    candidates = []
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, relative))
        candidates.append(os.path.join(app_dir(), relative))
    else:
        candidates.append(os.path.join(app_dir(), relative))
        candidates.append(relative)

    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0] if candidates else relative


def data_path(relative: str) -> str:
    """Absolute path of a writable file, kept next to the exe / project root."""
    relative = relative.replace("\\", "/").lstrip("/")
    if is_frozen():
        return os.path.join(app_dir(), relative)
    return relative


def resources_dir() -> str:
    """Directory holding the read-only resources."""
    return os.path.dirname(bundle_path("resources/_probe_"))
