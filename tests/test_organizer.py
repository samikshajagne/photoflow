"""
Unit tests for core.organizer.

The organizer never reads image *content* — it classifies by path and copies
files — so these tests use plain placeholder files with image extensions
rather than real images, keeping them fast and dependency-free. Duplicate
results are hand-built in the shape DuplicateDetector emits, and blur results
use real BlurResult instances.

This module tests organization only.
"""

from pathlib import Path

import pytest

from core.blur_detector import BlurResult
from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
    CopyOperation,
    OrganizationError,
    OrganizationResult,
    PhotoOrganizer,
)
from utils.config import load_config


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_photo(path: Path, content: bytes = b"jpegdata") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _dup_results(*, representative: str = "", duplicates: list[str] | None = None) -> dict:
    if not representative and not duplicates:
        return {"groups": []}
    return {
        "groups": [
            {"representative": representative, "duplicates": duplicates or []}
        ]
    }


def _blur(path: Path, is_blurry: bool) -> BlurResult:
    return BlurResult(path=str(path), blur_score=10.0 if is_blurry else 5000.0, is_blurry=is_blurry)


def _names_in(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()} if folder.exists() else set()


# --------------------------------------------------------------------------- #
# Folder structure
# --------------------------------------------------------------------------- #
def test_creates_all_four_active_folders(tmp_path: Path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    photo = _make_photo(src / "a.jpg")

    result = PhotoOrganizer().organize(
        original_paths=[photo],
        duplicate_results=_dup_results(),
        blur_results=[],
        destination_root=dest,
    )

    output_root = Path(result.output_root)
    assert output_root.name == "PhotoFlow_Output"
    assert (output_root / FOLDER_BEST_SHOTS).is_dir()
    assert (output_root / FOLDER_DUPLICATES).is_dir()
    assert (output_root / FOLDER_BLURRY).is_dir()
    assert (output_root / FOLDER_REVIEW).is_dir()


def test_empty_input_just_creates_folders(tmp_path: Path):
    result = PhotoOrganizer().organize(
        original_paths=[],
        duplicate_results=_dup_results(),
        blur_results=[],
        destination_root=tmp_path,
    )

    assert result.operations == ()
    assert result.skipped == ()
    assert result.category_counts() == {
        FOLDER_BEST_SHOTS: 0,
        FOLDER_DUPLICATES: 0,
        FOLDER_BLURRY: 0,
        FOLDER_REVIEW: 0,
    }


# --------------------------------------------------------------------------- #
# Routing rules
# --------------------------------------------------------------------------- #
def test_duplicates_go_to_duplicates(tmp_path: Path):
    rep = _make_photo(tmp_path / "src" / "keep.jpg")
    dup = _make_photo(tmp_path / "src" / "copy.jpg")
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[rep, dup],
        duplicate_results=_dup_results(representative=str(rep), duplicates=[str(dup)]),
        blur_results=[],
        destination_root=dest,
    )

    output_root = Path(result.output_root)
    # Only the duplicate goes to Duplicates; the representative goes to Review.
    assert _names_in(output_root / FOLDER_DUPLICATES) == {"copy.jpg"}
    assert _names_in(output_root / FOLDER_REVIEW) == {"keep.jpg"}


def test_blurry_go_to_blurry(tmp_path: Path):
    sharp = _make_photo(tmp_path / "src" / "sharp.jpg")
    blurry = _make_photo(tmp_path / "src" / "blurry.jpg")
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[sharp, blurry],
        duplicate_results=_dup_results(),
        blur_results=[_blur(sharp, False), _blur(blurry, True)],
        destination_root=dest,
    )

    output_root = Path(result.output_root)
    assert _names_in(output_root / FOLDER_BLURRY) == {"blurry.jpg"}
    assert _names_in(output_root / FOLDER_REVIEW) == {"sharp.jpg"}


def test_remaining_go_to_review(tmp_path: Path):
    a = _make_photo(tmp_path / "src" / "a.jpg")
    b = _make_photo(tmp_path / "src" / "b.png")
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[a, b],
        duplicate_results=_dup_results(),
        blur_results=[_blur(a, False), _blur(b, False)],
        destination_root=dest,
    )

    assert _names_in(Path(result.output_root) / FOLDER_REVIEW) == {"a.jpg", "b.png"}


def test_duplicate_takes_precedence_over_blurry(tmp_path: Path):
    rep = _make_photo(tmp_path / "src" / "keep.jpg")
    dup = _make_photo(tmp_path / "src" / "copy.jpg")
    dest = tmp_path / "dest"

    # The duplicate is ALSO flagged blurry; precedence sends it to Duplicates.
    result = PhotoOrganizer().organize(
        original_paths=[rep, dup],
        duplicate_results=_dup_results(representative=str(rep), duplicates=[str(dup)]),
        blur_results=[_blur(dup, True)],
        destination_root=dest,
    )

    output_root = Path(result.output_root)
    assert _names_in(output_root / FOLDER_DUPLICATES) == {"copy.jpg"}
    assert _names_in(output_root / FOLDER_BLURRY) == set()


def test_blurry_representative_goes_to_blurry(tmp_path: Path):
    rep = _make_photo(tmp_path / "src" / "keep.jpg")
    dup = _make_photo(tmp_path / "src" / "copy.jpg")
    dest = tmp_path / "dest"

    # Representative is not a duplicate, so a blurry flag routes it to Blurry.
    result = PhotoOrganizer().organize(
        original_paths=[rep, dup],
        duplicate_results=_dup_results(representative=str(rep), duplicates=[str(dup)]),
        blur_results=[_blur(rep, True)],
        destination_root=dest,
    )

    output_root = Path(result.output_root)
    assert _names_in(output_root / FOLDER_BLURRY) == {"keep.jpg"}
    assert _names_in(output_root / FOLDER_DUPLICATES) == {"copy.jpg"}
    assert _names_in(output_root / FOLDER_REVIEW) == set()


# --------------------------------------------------------------------------- #
# Safety: copy-only, preservation, collisions
# --------------------------------------------------------------------------- #
def test_originals_are_preserved_and_only_copied(tmp_path: Path):
    photo = _make_photo(tmp_path / "src" / "a.jpg", content=b"original-bytes")
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[photo],
        duplicate_results=_dup_results(),
        blur_results=[],
        destination_root=dest,
    )

    # Source untouched...
    assert photo.exists()
    assert photo.read_bytes() == b"original-bytes"
    # ...and a copy exists with identical content.
    copied = Path(result.operations[0].destination)
    assert copied.exists()
    assert copied.read_bytes() == b"original-bytes"
    assert copied != photo


def test_filename_collision_is_resolved(tmp_path: Path):
    # Two distinct files share a basename but live in different folders;
    # both classify to Review and must not overwrite each other.
    a = _make_photo(tmp_path / "src1" / "photo.jpg", content=b"one")
    b = _make_photo(tmp_path / "src2" / "photo.jpg", content=b"two")
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[a, b],
        duplicate_results=_dup_results(),
        blur_results=[],
        destination_root=dest,
    )

    review = Path(result.output_root) / FOLDER_REVIEW
    names = _names_in(review)
    assert names == {"photo.jpg", "photo_1.jpg"}
    # Both contents are preserved (nothing overwritten).
    contents = {(review / n).read_bytes() for n in names}
    assert contents == {b"one", b"two"}


def test_result_records_each_copy(tmp_path: Path):
    a = _make_photo(tmp_path / "src" / "a.jpg")
    b = _make_photo(tmp_path / "src" / "b.jpg")
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[a, b],
        duplicate_results=_dup_results(),
        blur_results=[],
        destination_root=dest,
    )

    assert isinstance(result, OrganizationResult)
    assert len(result.operations) == 2
    assert all(isinstance(op, CopyOperation) for op in result.operations)
    assert result.category_counts()[FOLDER_REVIEW] == 2


# --------------------------------------------------------------------------- #
# Error handling / robustness
# --------------------------------------------------------------------------- #
def test_missing_source_is_skipped_not_fatal(tmp_path: Path):
    real = _make_photo(tmp_path / "src" / "a.jpg")
    missing = tmp_path / "src" / "ghost.jpg"
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[real, missing],
        duplicate_results=_dup_results(),
        blur_results=[],
        destination_root=dest,
    )

    assert len(result.operations) == 1
    assert str(missing) in result.skipped


def test_malformed_duplicate_results_raises(tmp_path: Path):
    photo = _make_photo(tmp_path / "src" / "a.jpg")

    with pytest.raises(OrganizationError):
        PhotoOrganizer().organize(
            original_paths=[photo],
            duplicate_results={"not_groups": []},
            blur_results=[],
            destination_root=tmp_path / "dest",
        )


def test_destination_root_that_is_a_file_raises(tmp_path: Path):
    photo = _make_photo(tmp_path / "src" / "a.jpg")
    file_dest = _make_photo(tmp_path / "iam_a_file")

    with pytest.raises(OrganizationError):
        PhotoOrganizer().organize(
            original_paths=[photo],
            duplicate_results=_dup_results(),
            blur_results=[],
            destination_root=file_dest,
        )


# --------------------------------------------------------------------------- #
# Construction / configuration
# --------------------------------------------------------------------------- #
def test_empty_output_folder_name_raises():
    with pytest.raises(OrganizationError):
        PhotoOrganizer(output_folder_name="   ")


def test_output_folder_name_with_separator_raises():
    with pytest.raises(OrganizationError):
        PhotoOrganizer(output_folder_name="a/b")


def test_from_config_uses_output_folder_name(tmp_path: Path):
    config = load_config()
    organizer = PhotoOrganizer.from_config(config)
    assert organizer.output_folder_name == config.io.output_folder_name

    photo = _make_photo(tmp_path / "src" / "a.jpg")
    result = organizer.organize(
        original_paths=[photo],
        duplicate_results=_dup_results(),
        blur_results=[],
        destination_root=tmp_path / "dest",
    )
    assert Path(result.output_root).name == config.io.output_folder_name


# --------------------------------------------------------------------------- #
# BestShots routing
# --------------------------------------------------------------------------- #
def test_best_shot_goes_to_bestshots(tmp_path: Path):
    rep = _make_photo(tmp_path / "src" / "keep.jpg")
    dup = _make_photo(tmp_path / "src" / "copy.jpg")
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[rep, dup],
        duplicate_results=_dup_results(representative=str(rep), duplicates=[str(dup)]),
        blur_results=[],
        destination_root=dest,
        best_shots=[str(rep)],
    )

    output_root = Path(result.output_root)
    assert _names_in(output_root / FOLDER_BEST_SHOTS) == {"keep.jpg"}
    assert _names_in(output_root / FOLDER_DUPLICATES) == {"copy.jpg"}
    assert _names_in(output_root / FOLDER_REVIEW) == set()


def test_best_shot_takes_precedence_over_blurry(tmp_path: Path):
    rep = _make_photo(tmp_path / "src" / "keep.jpg")
    dup = _make_photo(tmp_path / "src" / "copy.jpg")
    dest = tmp_path / "dest"

    # The best shot is also flagged blurry; precedence keeps it in BestShots.
    result = PhotoOrganizer().organize(
        original_paths=[rep, dup],
        duplicate_results=_dup_results(representative=str(rep), duplicates=[str(dup)]),
        blur_results=[_blur(rep, True)],
        destination_root=dest,
        best_shots=[str(rep)],
    )

    output_root = Path(result.output_root)
    assert _names_in(output_root / FOLDER_BEST_SHOTS) == {"keep.jpg"}
    assert _names_in(output_root / FOLDER_BLURRY) == set()


def test_without_best_shots_representative_stays_in_review(tmp_path: Path):
    # Backward compatibility: when no best_shots are passed, the representative
    # falls through to Review as before.
    rep = _make_photo(tmp_path / "src" / "keep.jpg")
    dup = _make_photo(tmp_path / "src" / "copy.jpg")
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[rep, dup],
        duplicate_results=_dup_results(representative=str(rep), duplicates=[str(dup)]),
        blur_results=[],
        destination_root=dest,
    )

    output_root = Path(result.output_root)
    assert _names_in(output_root / FOLDER_BEST_SHOTS) == set()
    assert _names_in(output_root / FOLDER_REVIEW) == {"keep.jpg"}


def test_bestshots_filename_collision_is_resolved(tmp_path: Path):
    # Two best shots from different source folders share a basename.
    a = _make_photo(tmp_path / "g1" / "best.jpg", content=b"one")
    b = _make_photo(tmp_path / "g2" / "best.jpg", content=b"two")
    dest = tmp_path / "dest"

    result = PhotoOrganizer().organize(
        original_paths=[a, b],
        duplicate_results=_dup_results(),
        blur_results=[],
        destination_root=dest,
        best_shots=[str(a), str(b)],
    )

    best = Path(result.output_root) / FOLDER_BEST_SHOTS
    names = _names_in(best)
    assert names == {"best.jpg", "best_1.jpg"}
    contents = {(best / n).read_bytes() for n in names}
    assert contents == {b"one", b"two"}


def test_plan_routes_best_shots(tmp_path: Path):
    rep = _make_photo(tmp_path / "src" / "keep.jpg")
    dup = _make_photo(tmp_path / "src" / "copy.jpg")

    plan = PhotoOrganizer().plan(
        original_paths=[rep, dup],
        duplicate_results=_dup_results(representative=str(rep), duplicates=[str(dup)]),
        blur_results=[],
        best_shots=[str(rep)],
    )

    by_name = {Path(p).name: cat for p, cat in plan}
    assert by_name["keep.jpg"] == FOLDER_BEST_SHOTS
    assert by_name["copy.jpg"] == FOLDER_DUPLICATES
