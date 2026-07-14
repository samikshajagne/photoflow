"""
Unit tests for ui_qt.models.photo_index.

PhotoIndex is pure Python (no Qt), so these run in the normal suite. They use
lightweight stand-ins shaped like the pipeline's result objects.
"""

import dataclasses

from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)
from ui_qt.models.photo_index import (
    PhotoEntry,
    PhotoIndex,
    normalize_path,
    quality_tier,
)


@dataclasses.dataclass
class _Op:
    source: str
    destination: str
    category: str


@dataclasses.dataclass
class _Org:
    operations: tuple


@dataclasses.dataclass
class _Quality:
    image_path: str
    quality_score: float
    blur_score: float
    brightness: float
    contrast: float
    faces_detected: bool
    face_count: int


@dataclasses.dataclass
class _Result:
    organization: object
    quality_results: tuple


def _result():
    ops = (
        _Op("/p/keep.jpg", "/o/BestShots/keep.jpg", FOLDER_BEST_SHOTS),
        _Op("/p/dup.jpg", "/o/Duplicates/dup.jpg", FOLDER_DUPLICATES),
        _Op("/p/blur.jpg", "/o/Blurry/blur.jpg", FOLDER_BLURRY),
    )
    quality = (
        _Quality("/p/keep.jpg", 91.0, 5000.0, 128.0, 70.0, True, 2),
        _Quality("/p/dup.jpg", 90.0, 4800.0, 127.0, 69.0, True, 2),
        _Quality("/p/blur.jpg", 20.0, 10.0, 120.0, 40.0, False, 0),
    )
    return _Result(_Org(ops), quality)


# --------------------------------------------------------------------------- #
# Browse mode
# --------------------------------------------------------------------------- #
def test_from_paths_has_no_categories_or_metrics():
    idx = PhotoIndex.from_paths(["/p/a.jpg", "/p/b.png"])
    assert len(idx) == 2
    assert idx.categories() == ()
    entry = idx.all_entries()[0]
    assert entry.category is None
    assert entry.quality_score is None
    assert entry.name == "a.jpg"


# --------------------------------------------------------------------------- #
# Analyzed mode
# --------------------------------------------------------------------------- #
def test_from_result_groups_by_category():
    idx = PhotoIndex.from_result(_result())
    assert idx.count(FOLDER_BEST_SHOTS) == 1
    assert idx.count(FOLDER_DUPLICATES) == 1
    assert idx.count(FOLDER_BLURRY) == 1
    assert idx.count(FOLDER_REVIEW) == 0
    # Only non-empty categories are reported, in display order.
    assert idx.categories() == (FOLDER_BEST_SHOTS, FOLDER_DUPLICATES, FOLDER_BLURRY)


def test_from_result_attaches_metrics():
    idx = PhotoIndex.from_result(_result())
    keep = idx.get("/p/keep.jpg")
    assert keep is not None
    assert keep.quality_score == 91.0
    assert keep.blur_score == 5000.0
    assert keep.face_count == 2
    assert keep.faces_detected is True
    assert keep.is_best_shot is True
    # A duplicate is not a best shot.
    assert idx.get("/p/dup.jpg").is_best_shot is False


def test_counts_cover_all_categories():
    idx = PhotoIndex.from_result(_result())
    counts = idx.counts()
    assert set(counts) == {FOLDER_BEST_SHOTS, FOLDER_DUPLICATES, FOLDER_BLURRY, FOLDER_REVIEW}
    assert counts[FOLDER_REVIEW] == 0


def test_from_result_tolerates_missing_organization():
    # A dry-run-like result with no organization still builds (empty groups).
    idx = PhotoIndex.from_result(_Result(None, ()))
    assert idx.categories() == ()
    assert len(idx) == 0


def test_quality_tier_boundaries():
    assert quality_tier(None) is None
    assert quality_tier(95.0) == "Hero"
    assert quality_tier(90.0) == "Hero"      # inclusive
    assert quality_tier(89.9) == "BestShots"
    assert quality_tier(75.0) == "BestShots"  # inclusive (mirrors floor)
    assert quality_tier(74.9) == "Review"
    assert quality_tier(60.0) == "Review"     # inclusive
    assert quality_tier(59.9) == "Low"


def test_from_result_attaches_tier():
    idx = PhotoIndex.from_result(_result())
    # keep=91 -> Hero, dup=90 -> Hero, blur=20 -> Low.
    assert idx.get("/p/keep.jpg").tier == "Hero"
    assert idx.get("/p/dup.jpg").tier == "Hero"
    assert idx.get("/p/blur.jpg").tier == "Low"


def test_normalize_path_is_absolute():
    import os
    # normalize_path resolves to an absolute path; the original relative suffix
    # is preserved (using the OS separator, not necessarily a forward slash).
    normalized = normalize_path("a/b.jpg")
    assert os.path.isabs(normalized)
    # The filename is always preserved.
    assert normalized.endswith("b.jpg")
    # Calling it twice on the same input gives the same result.
    assert normalize_path("a/b.jpg") == normalize_path("a/b.jpg")
