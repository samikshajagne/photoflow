"""
Recursive image scanner for PhotoFlow (Milestone 2).

A small, reusable helper that walks a folder tree and returns the image files
worth processing, filtered by a configurable set of extensions. The blur
stage and the organizer both need the same authoritative list of originals,
so enumeration lives here rather than being duplicated at each call site.

The public entry point is :class:`ImageScanner`, which can be built directly
or from a validated :class:`~utils.config.AppConfig` via
:meth:`ImageScanner.from_config`.

Scope: enumeration only — no hashing, decoding, or analysis.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Union

from utils.logger import get_logger

if TYPE_CHECKING:
    from utils.config import AppConfig

logger = get_logger(__name__)

# File extensions considered images by default. Compared case-insensitively
# against each file's suffix. Kept as a module default so the scanner is
# usable without a full AppConfig.
DEFAULT_SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
)

PathLike = Union[str, Path]


class ScanError(Exception):
    """Raised when a folder cannot be scanned (missing, not a directory, I/O)."""


class ImageScanner:
    """
    Recursively enumerates supported image files within a folder.

    Args:
        supported_extensions: Extensions to include, each starting with a dot
            (e.g. ``".jpg"``). Matched case-insensitively. Must be non-empty.

    Raises:
        ScanError: if ``supported_extensions`` is empty or malformed.
    """

    def __init__(
        self,
        supported_extensions: tuple[str, ...] = DEFAULT_SUPPORTED_EXTENSIONS,
    ) -> None:
        if not supported_extensions:
            raise ScanError("supported_extensions must not be empty")
        for ext in supported_extensions:
            if not ext.startswith("."):
                raise ScanError(
                    f"supported_extensions entries must start with '.', got '{ext}'"
                )
        self._extensions = frozenset(ext.lower() for ext in supported_extensions)

    @classmethod
    def from_config(cls, config: "AppConfig") -> "ImageScanner":
        """Build a scanner from ``io.supported_extensions`` in the config."""
        return cls(supported_extensions=config.io.supported_extensions)

    def scan(self, folder: PathLike) -> list[Path]:
        """
        Return supported image files under ``folder``, recursively.

        Results are sorted for deterministic, reproducible ordering.
        Individual unreadable entries (e.g. broken symlinks) are logged and
        skipped rather than aborting the whole scan.

        Args:
            folder: Root directory to scan.

        Returns:
            A sorted list of image file paths.

        Raises:
            ScanError: if ``folder`` does not exist or is not a directory.
        """
        root = Path(folder)
        if not root.exists():
            raise ScanError(f"Folder does not exist: {root}")
        if not root.is_dir():
            raise ScanError(f"Path is not a directory: {root}")

        try:
            candidates = sorted(root.rglob("*"))
        except OSError as exc:
            raise ScanError(f"Failed to scan folder '{root}': {exc}") from exc

        matches: list[Path] = []
        for path in candidates:
            try:
                if path.is_file() and path.suffix.lower() in self._extensions:
                    matches.append(path)
            except OSError as exc:
                logger.warning("Skipping unreadable path '%s': %s", path, exc)

        logger.info("Scanner found %d image file(s) under '%s'.", len(matches), root)
        return matches
