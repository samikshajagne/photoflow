"""
A diagnostics report the customer can paste into a support email.

The problem this solves: a studio writes "it crashed" and that's all you get.
Reproducing a Windows 11 machine with 40,000 photos on a network drive is not
possible from your desk, so the report has to come from them — but only if
producing it takes one click.

What goes in: app version, Python and Qt versions, OS, CPU count, memory,
whether each optional dependency is present, the resolved data/log paths,
licence *state* (never the key), and the tail of the log file.

What deliberately stays out:
  - the licence key itself (state and last 6 characters at most)
  - photo contents, obviously
  - the user's account name, which is scrubbed from every path

That last one matters: log lines and paths routinely contain
``C:\\Users\\<real name>``, and a support report is often forwarded around. It
costs nothing to replace it with ``<user>``.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

from utils.logger import get_logger
from utils.version import APP_NAME, __version__

logger = get_logger(__name__)

# How much of the log to include: enough for a traceback and the events leading
# to it, small enough to paste into an email.
LOG_TAIL_LINES = 60

_OPTIONAL_PACKAGES = (
    "mediapipe",
    "scipy",
    "psd_tools",
    "insightface",
    "onnxruntime",
    "rembg",
    "openai",
)


# Image extensions the app logs paths for. Those paths are the customer's client
# work -- folder names like "Priya_Sangeet" and filenames are exactly the sort of
# thing that must not travel in a support email.
_IMAGE_EXT = r"jpe?g|png|tiff?|bmp|webp|psd|heic|cr2|nef|arw|dng"


def scrub(text: str) -> str:
    """
    Strip anything identifying from a report before it leaves the machine.

    Three passes, in order:

    1. **Photo paths.** The log records the file being processed, so the tail is
       full of the customer's client work. Each becomes ``<photo.jpg>`` --
       keeping the extension, which is all that has diagnostic value.
    2. **Other absolute paths** that aren't ours become ``<path>``. Client folder
       names are as sensitive as filenames.
    3. **The account name**, in ``C:\\Users\\name``, ``/Users/name``,
       ``/home/name`` and anywhere else it appears.

    Our *own* directories (the app's data/log paths) are intentionally left
    readable, because knowing where the app installed itself is useful and those
    paths contain nothing private once the username is replaced.
    """
    # 1. Photo file paths -> <photo.ext>. Spaces are allowed inside the path
    #    because client folders are routinely named "Priya & Arjun"; the match is
    #    non-greedy and anchored at a drive letter or leading slash, and it stops
    #    at the extension, so it can't run away across a log line.
    text = re.sub(
        rf"(?:[A-Za-z]:[\\/]|/)[^\"'<>|\r\n]*?\.({_IMAGE_EXT})\b",
        lambda m: f"<photo.{m.group(1).lower()}>",
        text,
        flags=re.IGNORECASE,
    )
    # 2. Any other absolute path with a file extension -> <path>, again allowing
    #    spaces. Excludes paths under Users\ because those are ours and stay
    #    readable once the account name is replaced in pass 3.
    text = re.sub(
        r"(?<![\w<])[A-Za-z]:\\(?!Users\\)[^\"'<>|\r\n]*?\.[A-Za-z0-9]{2,4}\b",
        "<path>",
        text,
    )
    # 3. Remaining bare Windows directories (no extension) -> <path>.
    text = re.sub(
        r"(?<![\w<])[A-Za-z]:\\(?!Users\\)[^\s\"'<>|\r\n]{2,}",
        "<path>",
        text,
    )
    # 3. Account name
    text = re.sub(r"([A-Za-z]:\\Users\\)[^\\\s\"']+", r"\1<user>", text)
    text = re.sub(r"(/Users/)[^/\s\"']+", r"\1<user>", text)
    text = re.sub(r"(/home/)[^/\s\"']+", r"\1<user>", text)
    username = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(username) > 2:  # a 1-2 character name would match far too much
        text = re.sub(re.escape(username), "<user>", text, flags=re.IGNORECASE)
    return text


def _package_status() -> list[str]:
    import importlib.util

    lines = []
    for name in _OPTIONAL_PACKAGES:
        try:
            present = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):  # broken/partial install
            present = False
        lines.append(f"  {name:<14} {'yes' if present else 'no'}")
    return lines


def _memory_gb() -> str:
    """Total RAM, best effort — informative, never worth an exception."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = Status()
            status.dwLength = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return f"{status.ullTotalPhys / 1024**3:.1f} GB"
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return f"{pages * size / 1024**3:.1f} GB"
    except Exception:  # noqa: BLE001
        return "unknown"


def _qt_version() -> str:
    try:
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR

        return f"Qt {QT_VERSION_STR} / PyQt {PYQT_VERSION_STR}"
    except Exception:  # noqa: BLE001
        return "not available"


def _licence_summary() -> list[str]:
    """Licence *state* only — never the key itself."""
    try:
        from core.licensing import LicenseManager

        status = LicenseManager().status()
        lines = [f"  state         {status.state}"]
        if status.days_left:
            lines.append(f"  days left     {status.days_left}")
        if status.key:
            lines.append(f"  key ending    ...{status.key[-6:]}")
        if status.customer:
            lines.append(f"  licensed to   {status.customer}")
        return lines
    except Exception as exc:  # noqa: BLE001
        return [f"  (unavailable: {exc})"]


def _log_tail(lines: int = LOG_TAIL_LINES) -> list[str]:
    from utils.paths import bundle_root, is_frozen, user_log_dir

    candidates = [user_log_dir() / "photoflow.log"]
    if not is_frozen():
        candidates.append(bundle_root() / "logs" / "photoflow.log")

    for path in candidates:
        try:
            if path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace")
                tail = content.splitlines()[-lines:]
                return [f"  (from {path.name}, last {len(tail)} lines)"] + [
                    "  " + line for line in tail
                ]
        except OSError as exc:
            return [f"  (could not read {path}: {exc})"]
    return ["  (no log file found yet)"]


def collect(include_log: bool = True) -> str:
    """
    Build the full diagnostics report as plain text.

    Never raises: a diagnostics tool that crashes while reporting a crash is
    worse than useless, so every section degrades to a note.
    """
    from utils.paths import is_frozen, user_data_dir, user_log_dir

    sections: list[str] = [
        f"{APP_NAME} diagnostics",
        "=" * 52,
        "",
        "Application",
        f"  version       {__version__}",
        f"  frozen build  {'yes' if is_frozen() else 'no (running from source)'}",
        f"  python        {platform.python_version()} ({platform.architecture()[0]})",
        f"  qt            {_qt_version()}",
        "",
        "System",
        f"  os            {platform.system()} {platform.release()} ({platform.version()})",
        f"  machine       {platform.machine()}",
        f"  cpu cores     {os.cpu_count()}",
        f"  memory        {_memory_gb()}",
        "",
        "Optional components",
        *_package_status(),
        "",
        "Licence",
        *_licence_summary(),
        "",
        "Paths",
        f"  data          {user_data_dir()}",
        f"  logs          {user_log_dir()}",
    ]

    if include_log:
        sections += ["", "Recent log", *_log_tail()]

    sections += [
        "",
        "-" * 52,
        "No photos, file names or client details are included in this report.",
    ]
    return scrub("\n".join(sections))
