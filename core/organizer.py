"""
Folder organization for PhotoFlow (Milestone 2).

This module takes the outputs of the analysis stages — duplicate detection
groups and per-image blur verdicts — together with the list of original
photos, and lays out copies under a single output directory::

    <destination>/PhotoFlow_Output/
        ├── Duplicates/   redundant copies (members of a duplicate group)
        ├── Blurry/       images flagged blurry that aren't duplicates
        └── Review/       everything else (incl. group representatives)

``BestShots/`` is intentionally NOT created here: best-shot selection
depends on quality scoring, which is a later milestone. The folder name is
reserved as a constant so it can be added without churn.

Safety guarantees:
- Originals are **copied**, never moved or deleted (``shutil.copy2``, which
  also preserves timestamps/metadata).
- Filename collisions in a destination folder are resolved by suffixing
  ``_1``, ``_2``, … so no copy ever overwrites another.
- Output folders are created automatically; all path handling goes through
  :mod:`pathlib` for cross-platform behavior.

Classification precedence when a photo qualifies for more than one bucket:
``Duplicates`` > ``Blurry`` > ``Review``. A photo listed as a duplicate is
redundant regardless of sharpness, so it lands in ``Duplicates`` even if it
is also blurry. Duplicate-group *representatives* are not duplicates and so
fall through to ``Blurry`` (if flagged) or ``Review``.

Scope: organization only — no face detection, quality scoring, UI, or
persistence.
"""

from __future__ import annotations

import dataclasses
import shutil
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Union

from core.blur_detector import BlurResult
from utils.logger import get_logger

if TYPE_CHECKING:
    from utils.config import AppConfig

logger = get_logger(__name__)

# Destination subfolder names.
FOLDER_DUPLICATES = "Duplicates"
FOLDER_BLURRY = "Blurry"
FOLDER_REVIEW = "Review"
# Reserved for a later milestone (best-shot selection after quality scoring).
# Deliberately NOT created by this module yet.
FOLDER_BEST_SHOTS = "BestShots"

# Subfolders this module creates on every run.
_ACTIVE_FOLDERS: tuple[str, ...] = (FOLDER_DUPLICATES, FOLDER_BLURRY, FOLDER_REVIEW)

DEFAULT_OUTPUT_FOLDER_NAME = "PhotoFlow_Output"

PathLike = Union[str, Path]


class OrganizationError(Exception):
    """Raised when photos cannot be organized (bad input or I/O failure)."""


@dataclasses.dataclass(frozen=True)
class CopyOperation:
    """A single copy performed during organization."""

    source: str
    destination: str
    category: str


@dataclasses.dataclass(frozen=True)
class OrganizationResult:
    """
    Summary of an organization run.

    Attributes:
        output_root: The ``PhotoFlow_Output`` directory that was populated.
        operations: One :class:`CopyOperation` per file successfully copied.
        skipped: Source paths that could not be copied (e.g. missing file),
            recorded rather than raised so one bad file never aborts the run.
    """

    output_root: str
    operations: tuple[CopyOperation, ...]
    skipped: tuple[str, ...]

    def category_counts(self) -> dict[str, int]:
        """Number of files copied into each category folder."""
        counts = Counter(op.category for op in self.operations)
        # Always report all active folders, even those that received nothing.
        return {folder: counts.get(folder, 0) for folder in _ACTIVE_FOLDERS}


class PhotoOrganizer:
    """
    Copies analyzed photos into category folders under an output directory.

    Args:
        output_folder_name: Name of the top-level output folder created inside
            the destination root. Must be non-empty and a single path
            component (no separators).

    Raises:
        OrganizationError: if ``output_folder_name`` is invalid.
    """

    def __init__(self, output_folder_name: str = DEFAULT_OUTPUT_FOLDER_NAME) -> None:
        if not output_folder_name or not output_folder_name.strip():
            raise OrganizationError("output_folder_name must not be empty")
        # Guard against a name that would escape the destination root.
        if Path(output_folder_name).name != output_folder_name:
            raise OrganizationError(
                f"output_folder_name must be a single path component, "
                f"got '{output_folder_name}'"
            )
        self.output_folder_name = output_folder_name

    @classmethod
    def from_config(cls, config: "AppConfig") -> "PhotoOrganizer":
        """
        Build an organizer from a validated :class:`~utils.config.AppConfig`.

        Reads ``io.output_folder_name``. Note that PhotoFlow always copies
        (never moves) for safety, so ``io.copy_not_move`` is not consulted
        here — moving originals is intentionally unsupported.
        """
        return cls(output_folder_name=config.io.output_folder_name)

    def organize(
        self,
        *,
        original_paths: Iterable[PathLike],
        duplicate_results: Mapping[str, Any],
        blur_results: Iterable[BlurResult],
        destination_root: PathLike,
    ) -> OrganizationResult:
        """
        Classify and copy each original photo into the output structure.

        Args:
            original_paths: Every photo to organize. Each is copied exactly
                once into the bucket chosen by the precedence rules.
            duplicate_results: The mapping returned by
                :meth:`~core.duplicate_detector.DuplicateDetector.detect`,
                i.e. ``{"groups": [{"representative", "duplicates"}, ...]}``.
            blur_results: :class:`~core.blur_detector.BlurResult` objects;
                those with ``is_blurry`` true mark their path as blurry.
            destination_root: Directory in which the output folder is created.

        Returns:
            An :class:`OrganizationResult` describing the copies made and any
            sources skipped.

        Raises:
            OrganizationError: if ``destination_root`` is invalid, the output
                folders cannot be created, or ``duplicate_results`` is
                malformed.
        """
        duplicate_paths = self._extract_duplicate_paths(duplicate_results)
        blurry_paths = self._extract_blurry_paths(blur_results)

        output_root = self._prepare_output_dirs(destination_root)

        operations: list[CopyOperation] = []
        skipped: list[str] = []

        for raw in original_paths:
            source = Path(raw)
            key = self._normalize(source)

            if not source.exists() or not source.is_file():
                logger.warning("Skipping missing/non-file source: %s", source)
                skipped.append(str(source))
                continue

            category = self._classify(key, duplicate_paths, blurry_paths)
            try:
                destination = self._copy_into(source, output_root / category)
            except OSError as exc:
                logger.warning("Failed to copy '%s': %s", source, exc)
                skipped.append(str(source))
                continue

            operations.append(
                CopyOperation(
                    source=str(source),
                    destination=str(destination),
                    category=category,
                )
            )

        result = OrganizationResult(
            output_root=str(output_root),
            operations=tuple(operations),
            skipped=tuple(skipped),
        )
        logger.info(
            "Organized %d file(s) into '%s' (%s); %d skipped.",
            len(operations),
            output_root,
            ", ".join(f"{k}={v}" for k, v in result.category_counts().items()),
            len(skipped),
        )
        return result

    def plan(
        self,
        *,
        original_paths: Iterable[PathLike],
        duplicate_results: Mapping[str, Any],
        blur_results: Iterable[BlurResult],
    ) -> list[tuple[Path, str]]:
        """
        Classify originals into categories **without copying anything**.

        Applies exactly the same precedence rules as :meth:`organize`
        (Duplicates > Blurry > Review) but performs no filesystem writes,
        making it suitable for a dry-run/preview. Missing or non-file paths
        are silently omitted (they would be skipped by :meth:`organize`).

        Args:
            original_paths: Photos to classify.
            duplicate_results: ``DuplicateDetector.detect`` output.
            blur_results: :class:`~core.blur_detector.BlurResult` objects.

        Returns:
            A list of ``(source_path, category)`` tuples for existing files,
            where ``category`` is one of the ``FOLDER_*`` names.

        Raises:
            OrganizationError: if ``duplicate_results`` is malformed.
        """
        duplicate_paths = self._extract_duplicate_paths(duplicate_results)
        blurry_paths = self._extract_blurry_paths(blur_results)

        classified: list[tuple[Path, str]] = []
        for raw in original_paths:
            source = Path(raw)
            if not source.exists() or not source.is_file():
                continue
            category = self._classify(self._normalize(source), duplicate_paths, blurry_paths)
            classified.append((source, category))
        return classified

    # ----------------------------------------------------------------- #
    # Input parsing
    # ----------------------------------------------------------------- #
    def _extract_duplicate_paths(self, duplicate_results: Mapping[str, Any]) -> set[str]:
        """
        Collect normalized paths of every photo that is a *duplicate*.

        Group representatives are deliberately excluded — they are the copy
        to keep, not a redundant duplicate.
        """
        if "groups" not in duplicate_results:
            raise OrganizationError(
                "duplicate_results must contain a 'groups' key "
                "(see DuplicateDetector.detect)"
            )
        groups = duplicate_results["groups"]
        if not isinstance(groups, list):
            raise OrganizationError("duplicate_results['groups'] must be a list")

        paths: set[str] = set()
        for group in groups:
            try:
                duplicates = group["duplicates"]
            except (TypeError, KeyError) as exc:
                raise OrganizationError(
                    f"Malformed duplicate group, missing 'duplicates': {group!r}"
                ) from exc
            for dup in duplicates:
                paths.add(self._normalize(Path(dup)))
        return paths

    def _extract_blurry_paths(self, blur_results: Iterable[BlurResult]) -> set[str]:
        """Collect normalized paths of every image flagged blurry."""
        return {
            self._normalize(Path(r.path)) for r in blur_results if r.is_blurry
        }

    # ----------------------------------------------------------------- #
    # Filesystem helpers
    # ----------------------------------------------------------------- #
    def _prepare_output_dirs(self, destination_root: PathLike) -> Path:
        """Create the output root and active category folders; return the root."""
        root = Path(destination_root)
        if root.exists() and not root.is_dir():
            raise OrganizationError(
                f"destination_root exists but is not a directory: {root}"
            )

        output_root = root / self.output_folder_name
        try:
            for folder in _ACTIVE_FOLDERS:
                (output_root / folder).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OrganizationError(
                f"Failed to create output folders under '{output_root}': {exc}"
            ) from exc
        return output_root

    def _copy_into(self, source: Path, target_folder: Path) -> Path:
        """Copy ``source`` into ``target_folder`` under a collision-free name."""
        destination = self._unique_destination(target_folder, source.name)
        shutil.copy2(source, destination)
        return destination

    @staticmethod
    def _unique_destination(folder: Path, filename: str) -> Path:
        """
        Return a path in ``folder`` for ``filename`` that doesn't yet exist.

        If ``filename`` is free it's used as-is; otherwise ``_1``, ``_2``, …
        is inserted before the suffix until a free name is found.
        """
        candidate = folder / filename
        if not candidate.exists():
            return candidate
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        counter = 1
        while True:
            candidate = folder / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    # ----------------------------------------------------------------- #
    # Classification
    # ----------------------------------------------------------------- #
    @staticmethod
    def _classify(
        normalized_path: str,
        duplicate_paths: set[str],
        blurry_paths: set[str],
    ) -> str:
        """Apply precedence Duplicates > Blurry > Review for one photo."""
        if normalized_path in duplicate_paths:
            return FOLDER_DUPLICATES
        if normalized_path in blurry_paths:
            return FOLDER_BLURRY
        return FOLDER_REVIEW

    @staticmethod
    def _normalize(path: Path) -> str:
        """
        Canonicalize a path for reliable cross-source matching.

        ``resolve(strict=False)`` yields an absolute, normalized path without
        requiring the file to exist, so duplicate/blur paths and original
        paths compare equal regardless of how they were originally spelled.
        """
        return str(path.resolve(strict=False))
