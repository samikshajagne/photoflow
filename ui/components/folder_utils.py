"""
Cross-platform folder helpers for the PhotoFlow UI.

These are UI-layer utilities only -- they validate user-entered folder paths
and reveal folders in the host operating system's file manager. They contain
no image-processing or pipeline logic.

The command/launch logic is split from the side-effecting call so it can be
unit-tested without actually opening a window:

- :func:`file_manager_command` returns the argv list for macOS/Linux (or
  ``None`` on Windows, where ``os.startfile`` is used instead).
- :func:`reveal_folder` performs the actual reveal.
- :func:`validate_input_folder` / :func:`validate_output_folder` return a
  ``(ok, message)`` pair suitable for displaying inline in the UI.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Optional, Union

from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]


class FolderError(Exception):
    """Raised when a folder cannot be revealed in the OS file manager."""


def file_manager_command(system: str, path: str) -> Optional[list[str]]:
    """
    Return the argv list to open ``path`` in the file manager of ``system``.

    Args:
        system: The value of :func:`platform.system` (e.g. ``"Windows"``,
            ``"Darwin"``, ``"Linux"``).
        path: The folder path to open.

    Returns:
        An argv list for macOS (``open``) and Linux/other (``xdg-open``), or
        ``None`` for Windows, where the caller should use ``os.startfile``.
    """
    if system == "Darwin":
        return ["open", path]
    if system == "Windows":
        return None
    # Linux and other Unixes.
    return ["xdg-open", path]


def reveal_folder(path: PathLike) -> None:
    """
    Open ``path`` in the host operating system's file manager.

    Args:
        path: Folder to reveal.

    Raises:
        FolderError: if the folder does not exist or the OS launch fails.
    """
    folder = Path(path)
    if not folder.exists() or not folder.is_dir():
        raise FolderError(f"Folder does not exist: {folder}")

    system = platform.system()
    try:
        if system == "Windows":
            # os.startfile exists only on Windows.
            os.startfile(str(folder))  # type: ignore[attr-defined]
        else:
            command = file_manager_command(system, str(folder))
            if command is None:  # pragma: no cover - defensive
                raise FolderError(f"No file-manager command for system '{system}'")
            subprocess.Popen(command)
    except OSError as exc:
        raise FolderError(f"Failed to open folder '{folder}': {exc}") from exc

    logger.info("Revealed folder in file manager: %s", folder)


def validate_input_folder(path_str: str) -> tuple[bool, str]:
    """
    Validate a user-entered input folder.

    Returns:
        ``(True, "")`` if the path is an existing directory, otherwise
        ``(False, message)`` with a human-readable reason.
    """
    if not path_str or not path_str.strip():
        return False, "Please enter the folder containing your photos."
    folder = Path(path_str).expanduser()
    if not folder.exists():
        return False, f"Folder does not exist: {folder}"
    if not folder.is_dir():
        return False, f"Not a folder: {folder}"
    return True, ""


def validate_output_folder(path_str: str) -> tuple[bool, str]:
    """
    Validate a user-entered output folder.

    The folder need not exist yet (PhotoFlow creates it), but the path must be
    non-empty and must not point at an existing file.

    Returns:
        ``(True, "")`` if usable, otherwise ``(False, message)``.
    """
    if not path_str or not path_str.strip():
        return False, "Please enter a destination folder for the results."
    folder = Path(path_str).expanduser()
    if folder.exists() and not folder.is_dir():
        return False, f"Destination exists but is not a folder: {folder}"
    return True, ""
