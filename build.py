"""Build DarkMatterBot into a distributable executable (PyInstaller).

From a terminal:      python build.py            (single .exe, zipped release)
                      python build.py --onedir   (folder build, starts faster)
In the app:           Settings -> Build EXE      (same code path, streams here)

The build is self-contained: it installs PyInstaller if needed, cleans old
artefacts, bundles ``resources/`` (GeoLite2 database, user-agents, referers) and
``ui/``, then writes the result to ``dist/`` and a zip to ``releases/``.

Note: PyInstaller does **not** cross-compile. Run this on Windows to get
``DarkMatterBot.exe``; on Linux/macOS the identical command produces that
platform's own binary (handy for smoke-testing the bundle).
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile

APP_NAME = "DarkMatterBot"
DEFAULT_VERSION = "3.7.0"

# Everything the app imports that PyInstaller cannot always see on its own.
HIDDEN_IMPORTS = [
    "customtkinter",
    "customtkinter.windows",
    "darkdetect",
    "PIL",
    "PIL._tkinter_finder",
    "PIL.ImageTk",
    "psutil",
    "curl_cffi",
    "curl_cffi.requests",
    "geoip2.database",
    "maxminddb",
    "fake_useragent",
    "websockets",
    "requests",
    "aiohttp",
    "asyncssh",
    "certifi",
]

# Packages whose data files (assets, DLLs) must be copied in as well.
COLLECT_ALL = ["customtkinter", "curl_cffi"]

# Everything main.py/`core`/`ui` import at runtime. PyInstaller happily builds a
# broken exe when one of these is missing from the interpreter (the bundle then
# dies with "No module named ..."), so they are checked before every build.
RUNTIME_MODULES = [
    "customtkinter",
    "PIL",
    "darkdetect",
    "curl_cffi",
    "geoip2",
    "maxminddb",
    "requests",
    "psutil",
    "fake_useragent",
    "websockets",
    "aiohttp",
]
# nice to have: the app degrades gracefully without these
OPTIONAL_MODULES = ["playwright", "asyncssh"]


def project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def read_version() -> str:
    """Version from pyproject.toml, falling back to the known current one."""
    pyproject = os.path.join(project_root(), "pyproject.toml")
    try:
        with open(pyproject, encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped.startswith("version") and "=" in stripped:
                    value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
    except OSError:
        pass
    return DEFAULT_VERSION


def _on_rm_error(func, path, _exc_info):
    """Clear the read-only flag Windows sets on build outputs, then retry."""
    try:
        if not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def clean_outputs(log) -> None:
    """Remove dist/ and build/ so the result is never a stale mix."""
    for folder in ("dist", "build"):
        target = os.path.join(project_root(), folder)
        if os.path.exists(target):
            log(f"Cleaning {folder}/ ...")
            shutil.rmtree(target, ignore_errors=False, onerror=_on_rm_error)


def archive_old_releases(log) -> None:
    """Move previous release zips aside so releases/ only holds the newest."""
    releases = os.path.join(project_root(), "releases")
    archive = os.path.join(releases, "archive")
    candidates = glob.glob(os.path.join(releases, f"{APP_NAME}_v*.zip")) + glob.glob(
        os.path.join(project_root(), f"{APP_NAME}_v*.zip")
    )
    if not candidates:
        return
    os.makedirs(archive, exist_ok=True)
    for path in candidates:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name, ext = os.path.splitext(os.path.basename(path))
        try:
            shutil.move(path, os.path.join(archive, f"{name}_{stamp}{ext}"))
            log(f"Archived previous release: {os.path.basename(path)}")
        except Exception as exc:  # never fail a build over housekeeping
            log(f"Could not archive {os.path.basename(path)}: {exc}")


def missing_modules(modules) -> list:
    """Names from ``modules`` that this interpreter cannot import."""
    import importlib.util

    missing = []
    for name in modules:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except Exception:
            missing.append(name)
    return missing


def ensure_runtime(log) -> bool:
    """Make sure the app's own dependencies are installed before building.

    The exe is only as complete as the interpreter building it. A build from an
    environment without customtkinter succeeds but produces an exe that dies on
    start, so this installs what is missing and refuses to continue if it still
    is - one clear error instead of a broken executable.
    """
    missing = missing_modules(RUNTIME_MODULES)
    if not missing:
        log("Runtime dependencies: all present.")
        return True

    log(f"Missing runtime packages: {', '.join(missing)}")
    log("Installing requirements.txt ...")
    requirements = os.path.join(project_root(), "requirements.txt")
    if os.path.exists(requirements):
        subprocess.call([sys.executable, "-m", "pip", "install", "-r", requirements])
    # belt and braces: these are imported directly and are easy to forget
    subprocess.call(
        [sys.executable, "-m", "pip", "install", "pillow", "darkdetect", "requests", "psutil"]
    )

    missing = missing_modules(RUNTIME_MODULES)
    if missing:
        log("=" * 62)
        log(f"STILL MISSING: {', '.join(missing)}")
        log("Refusing to build - the exe would fail with 'No module named ...'.")
        log("Run this, then build again:")
        log("    python -m pip install -r requirements.txt")
        log("=" * 62)
        return False
    log("Runtime dependencies installed.")
    return True


def ensure_pyinstaller(log) -> None:
    """Install PyInstaller if it is missing."""
    try:
        import PyInstaller  # noqa: F401

        log("PyInstaller is available.")
    except ImportError:
        log("Installing PyInstaller ...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"]
        )


def exe_path(onefile: bool = True) -> str:
    """Where PyInstaller will put the result on this platform."""
    name = f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME
    dist = os.path.join(project_root(), "dist")
    return os.path.join(dist, name) if onefile else os.path.join(dist, APP_NAME, name)


def build_executable(log=print, onefile: bool = True, windowed: bool = True) -> str | None:
    """Run PyInstaller, streaming its output to ``log``.

    Args:
        log: Callable taking one line of text (print, or the app's logger).
        onefile: Single self-contained binary (slower start) vs a folder build.
        windowed: No console window (this is a GUI app).

    Returns:
        Path to the built executable, or None when the build failed.
    """
    root = project_root()
    sep = ";" if os.name == "nt" else ":"
    if not ensure_runtime(log):
        return None
    ensure_pyinstaller(log)
    archive_old_releases(log)
    clean_outputs(log)

    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean"]
    command += ["--onefile"] if onefile else ["--onedir"]
    if windowed:
        command.append("--windowed")
    icon = os.path.join(root, "resources", "favicon.ico")
    if os.path.exists(icon):
        command.append(f"--icon={icon}")
    # resources/ holds the GeoIP database and the text assets; ui/ is imported at
    # runtime for the theme file
    command.append(f"--add-data={os.path.join(root, 'resources')}{sep}resources")
    command.append(f"--add-data={os.path.join(root, 'ui')}{sep}ui")
    for module in HIDDEN_IMPORTS:
        command.append(f"--hidden-import={module}")
    for package in COLLECT_ALL:
        command.append(f"--collect-all={package}")
    command += [f"--name={APP_NAME}", "main.py"]

    log(f"Building {'one-file' if onefile else 'one-folder'} executable ...")
    log(" ".join(f'"{c}"' if " " in c else c for c in command))

    try:
        # A --windowed app has no console; without this flag Windows would flash
        # a cmd window for the whole build.
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creation_flags,
        )
        log_file = open(os.path.join(root, "build.log"), "w", encoding="utf-8", errors="replace")
        try:
            for line in process.stdout or []:
                line = line.rstrip()
                log_file.write(line + "\n")
                log_file.flush()
                # PyInstaller is chatty; keep the log readable
                if line and not line.startswith(("   ", "checking", "analyzing")):
                    log(line)
            code = process.wait()
        finally:
            log_file.close()
    except FileNotFoundError:
        log("PyInstaller could not be started. Is it installed for this Python?")
        return None

    if code != 0:
        log(f"Build failed (exit code {code}).")
        return None

    target = exe_path(onefile)
    if not os.path.exists(target):
        log(f"Build reported success but {target} is missing.")
        return None
    size_mb = os.path.getsize(target) / (1024 * 1024)
    log(f"Build successful: {target} ({size_mb:.1f} MB)")
    log(f"Full PyInstaller output: {os.path.join(root, 'build.log')}")
    return target


def make_release_zip(target: str, log=print) -> str | None:
    """Zip the build for distribution: releases/DarkMatterBot_v<version>.zip."""
    releases = os.path.join(project_root(), "releases")
    os.makedirs(releases, exist_ok=True)
    version = read_version()
    zip_path = os.path.join(releases, f"{APP_NAME}_v{version}.zip")
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(target, os.path.basename(target))
        log(f"Release archive: {zip_path}")
        return zip_path
    except Exception as exc:
        log(f"Could not create the release archive: {exc}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DarkMatterBot.")
    parser.add_argument("--onedir", action="store_true", help="folder build instead of a single file")
    parser.add_argument("--console", action="store_true", help="keep the console window (debugging)")
    parser.add_argument("--no-zip", action="store_true", help="skip the release archive")
    args = parser.parse_args()

    target = build_executable(log=print, onefile=not args.onedir, windowed=not args.console)
    if not target:
        return 1
    if not args.no_zip:
        make_release_zip(target, log=print)
    return 0


if __name__ == "__main__":
    sys.exit(main())
