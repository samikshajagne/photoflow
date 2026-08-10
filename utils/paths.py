"""
Where PhotoFlow reads bundled files from, and where it writes user data to.

This exists because a frozen, installed application lives somewhere very
different from a development checkout, and getting it wrong produces bugs that
never appear while developing:

* **Read-only install directory.** An installed app usually sits in
  ``C:\\Program Files\\...``, which a normal user account cannot write to. Any
  code that saves settings, presets or downloaded models *next to itself* works
  perfectly from a source checkout and then fails for every real customer.
  :func:`user_data_dir` is the writable place for anything the app produces.
* **Bundled files move.** PyInstaller unpacks bundled data into a temporary
  directory exposed as ``sys._MEIPASS``, so paths built from ``__file__``
  resolve differently once frozen. :func:`resource_path` handles both cases.

Rule of thumb: **read** with :func:`resource_path`, **write** with
:func:`user_data_dir`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from utils.version import APP_NAME, COMPANY_NAME

# Project root when running from source (this file is utils/paths.py).
_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than source."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def bundle_root() -> Path:
    """
    Directory that bundled read-only files live under.

    The PyInstaller unpack directory when frozen, otherwise the project root.
    """
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return _SOURCE_ROOT


def resource_path(*parts: str) -> Path:
    """
    Absolute path to a bundled resource, e.g. ``resource_path("data", "models")``.

    Works identically from source and from a frozen build, so callers never
    need to know which they're in.
    """
    return bundle_root().joinpath(*parts)


def user_data_dir() -> Path:
    """
    Per-user writable directory for settings, presets and downloads.

    Windows gets ``%LOCALAPPDATA%\\Samiksha Technologies\\PhotoFlow``, macOS
    ``~/Library/Application Support/...``, and everything else follows the
    XDG spec (``$XDG_DATA_HOME`` or ``~/.local/share``). The directory is
    created on first use.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        target = base / COMPANY_NAME / APP_NAME
    elif sys.platform == "darwin":
        target = Path.home() / "Library" / "Application Support" / COMPANY_NAME / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        target = base / "samiksha-technologies" / "photoflow"

    target.mkdir(parents=True, exist_ok=True)
    return target


def user_cache_dir() -> Path:
    """
    Writable directory for things that can be re-created if deleted.

    Downloaded model files belong here rather than in the install directory:
    they're large, they're re-downloadable, and the install directory usually
    isn't writable.
    """
    target = user_data_dir() / "cache"
    target.mkdir(parents=True, exist_ok=True)
    return target


def user_log_dir() -> Path:
    """Writable directory for log files (handy to ask customers for)."""
    target = user_data_dir() / "logs"
    target.mkdir(parents=True, exist_ok=True)
    return target


def writable_model_dir() -> Path:
    """
    Where auto-downloaded models should be stored.

    Prefers a bundled ``data/models`` directory *if it is actually writable*
    (true in a source checkout, which keeps development convenient), and falls
    back to the per-user cache directory otherwise (true once installed).
    """
    bundled = resource_path("data", "models")
    try:
        if bundled.is_dir() and os.access(bundled, os.W_OK):
            return bundled
    except OSError:  # pragma: no cover - odd filesystems
        pass
    target = user_cache_dir() / "models"
    target.mkdir(parents=True, exist_ok=True)
    return target
