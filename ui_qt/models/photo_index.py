"""
In-memory photo index for the PhotoFlow desktop UI.

Turns the pipeline's :class:`~core.pipeline.PipelineResult` into something the
views can browse: a flat list of lightweight :class:`PhotoEntry` records and a
per-category grouping. This module imports no Qt and no heavy libraries -- it
just reshapes data the backend already produced (the organizer's copy
operations give the path->category mapping; the quality results give the
per-photo metrics), so it adds no analysis and changes no backend logic.

Two construction paths:
- :meth:`PhotoIndex.from_paths` -- *browse* mode (Open Folder, pre-analysis):
  entries carry only their path; no category, no metrics.
- :meth:`PhotoIndex.from_result` -- *analyzed* mode: entries are grouped into
  Best Shots / Duplicates / Blurry / Review and carry quality metrics.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)

PathLike = Union[str, Path]

# Category display order (mirrors the pipeline's report order).
CATEGORY_ORDER: tuple[str, ...] = (
    FOLDER_BEST_SHOTS,
    FOLDER_DUPLICATES,
    FOLDER_BLURRY,
    FOLDER_REVIEW,
)

# Internal quality tiers -- a finer ranking than the four output folders. The
# UI still shows BestShots / Duplicates / Blurry / Review, but every analyzed
# entry also carries a tier so later features (album generation, highlight
# reels, client previews) can rank within BestShots. ``TIER_BEST_MIN`` mirrors
# the pipeline's BEST_SHOTS_QUALITY_FLOOR: a photo at/above it is a BestShot.
TIER_HERO = "Hero"          # 90-100: the strongest frames of the shoot
TIER_BEST = "BestShots"     # 75-89:  album-worthy
TIER_REVIEW = "Review"      # 60-74:  usable, worth a look
TIER_LOW = "Low"            # <60:    weak

TIER_HERO_MIN: float = 90.0
TIER_BEST_MIN: float = 75.0
TIER_REVIEW_MIN: float = 60.0


def quality_tier(score: Optional[float]) -> Optional[str]:
    """Map a 0-100 quality score to its internal tier (``None`` if unscored)."""
    if score is None:
        return None
    if score >= TIER_HERO_MIN:
        return TIER_HERO
    if score >= TIER_BEST_MIN:
        return TIER_BEST
    if score >= TIER_REVIEW_MIN:
        return TIER_REVIEW
    return TIER_LOW


def normalize_path(path: PathLike) -> str:
    """Canonicalize a path for reliable cross-source matching."""
    return str(Path(path).resolve(strict=False))


@dataclasses.dataclass(frozen=True)
class PhotoEntry:
    """
    One photo as the UI sees it.

    ``category`` and the metric fields are ``None`` in browse mode (before
    analysis). After analysis they are populated from the pipeline result.
    """

    source_path: str
    category: Optional[str] = None
    quality_score: Optional[float] = None
    blur_score: Optional[float] = None
    face_count: Optional[int] = None
    faces_detected: Optional[bool] = None
    is_best_shot: bool = False
    tier: Optional[str] = None

    @property
    def name(self) -> str:
        return Path(self.source_path).name


class PhotoIndex:
    """A browsable index of photos, optionally grouped by category."""

    def __init__(
        self,
        entries: list[PhotoEntry],
        by_category: Optional[dict[str, list[PhotoEntry]]] = None,
    ) -> None:
        self._entries = entries
        self._by_category = by_category or {}
        self._by_path = {normalize_path(e.source_path): e for e in entries}

    # ----------------------------------------------------------------- #
    # Construction
    # ----------------------------------------------------------------- #
    @classmethod
    def from_paths(cls, paths: Iterable[PathLike]) -> "PhotoIndex":
        """Browse mode: a flat list of entries with no category/metrics."""
        entries = [PhotoEntry(source_path=str(p)) for p in paths]
        return cls(entries)

    @classmethod
    def from_result(cls, result: Any) -> "PhotoIndex":
        """
        Analyzed mode: group photos by category and attach quality metrics.

        Reads ``result.organization.operations`` (source -> category) and
        ``result.quality_results`` (per-image metrics). Tolerant of a missing
        organization (e.g. a dry run): falls back to an empty grouping.
        """
        quality_by_path = {
            normalize_path(q.image_path): q for q in getattr(result, "quality_results", ())
        }

        by_category: dict[str, list[PhotoEntry]] = {key: [] for key in CATEGORY_ORDER}
        entries: list[PhotoEntry] = []

        organization = getattr(result, "organization", None)
        operations = getattr(organization, "operations", ()) if organization else ()
        for op in operations:
            q = quality_by_path.get(normalize_path(op.source))
            quality_score = getattr(q, "quality_score", None)
            entry = PhotoEntry(
                source_path=op.source,
                category=op.category,
                quality_score=quality_score,
                blur_score=getattr(q, "blur_score", None),
                face_count=getattr(q, "face_count", None),
                faces_detected=getattr(q, "faces_detected", None),
                is_best_shot=(op.category == FOLDER_BEST_SHOTS),
                tier=quality_tier(quality_score),
            )
            entries.append(entry)
            by_category.setdefault(op.category, []).append(entry)

        # Stable, name-sorted order within each category.
        for key in by_category:
            by_category[key].sort(key=lambda e: e.name.lower())
        return cls(entries, by_category)

    # ----------------------------------------------------------------- #
    # Access
    # ----------------------------------------------------------------- #
    def all_entries(self) -> list[PhotoEntry]:
        return list(self._entries)

    def categories(self) -> tuple[str, ...]:
        """Categories present, in display order."""
        return tuple(key for key in CATEGORY_ORDER if self._by_category.get(key))

    def entries(self, category: str) -> list[PhotoEntry]:
        return list(self._by_category.get(category, []))

    def count(self, category: str) -> int:
        return len(self._by_category.get(category, []))

    def counts(self) -> dict[str, int]:
        return {key: len(self._by_category.get(key, [])) for key in CATEGORY_ORDER}

    def get(self, source_path: PathLike) -> Optional[PhotoEntry]:
        return self._by_path.get(normalize_path(source_path))

    def __len__(self) -> int:
        return len(self._entries)
