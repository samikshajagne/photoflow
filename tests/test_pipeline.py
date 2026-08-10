"""
Integration tests for core.pipeline.

These exercise the real end-to-end flow (scan -> dedupe -> blur -> organize)
against synthesized images:

- a sharp checkerboard (high Laplacian variance; unique content)
- an exact byte copy of it (a duplicate)
- a smooth gradient (near-zero Laplacian variance -> clearly unusable; content
  far from the checkerboard so it is not a duplicate)

Expected routing under the redesigned pipeline: the copy -> Duplicates, the
gradient -> Blurry (the conservative usability gate), and the checkerboard (the
group representative) -> Review. BestShots is empty here: these synthetic
images contain no faces, so none clears the BestShots quality floor (a
face-less frame caps at the floor under the default weights). Face-driven
BestShots selection is covered in test_pipeline_faces.py.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)
from core.pipeline import PhotoFlowPipeline, PipelineError, PipelineResult
from tests.conftest import StubFaceDetector
from utils.config import load_config


# --------------------------------------------------------------------------- #
# Image fixtures
# --------------------------------------------------------------------------- #
def _checkerboard(size: int = 128, square: int = 8) -> np.ndarray:
    rows = []
    for r in range(size // square):
        row = [(0 if (r + c) % 2 else 255) for c in range(size // square)]
        rows.append(np.repeat(row, square))
    return np.repeat(np.array(rows, dtype=np.uint8), square, axis=0)[:size, :size]


def _gradient(size: int = 128) -> np.ndarray:
    return np.tile(np.linspace(0, 255, size, dtype=np.uint8), (size, 1))


def _build_sample_folder(tmp_path: Path) -> Path:
    """Create a source folder with a sharp original, its copy, and a blurry image."""
    src = tmp_path / "photos"
    src.mkdir(parents=True, exist_ok=True)

    sharp = src / "a_sharp.png"
    cv2.imwrite(str(sharp), _checkerboard())

    # Exact byte-for-byte duplicate of the sharp image.
    (src / "b_copy.png").write_bytes(sharp.read_bytes())

    # Distinct, blurry-by-construction image.
    cv2.imwrite(str(src / "z_blurry.png"), _gradient())

    return src


def _pipeline() -> PhotoFlowPipeline:
    """
    Pipeline with a stub face detector that succeeds and finds no faces.

    The fixtures here are synthetic, so a working detector finds nothing in
    them -- but only if it actually runs. With MediaPipe absent, detection
    *fails* for every image, and the pipeline then bypasses face scoring
    entirely, which shifts photos between BestShots and Review and broke the
    category assertions below. See ``tests/conftest.py::StubFaceDetector``.
    """
    pipe = PhotoFlowPipeline.from_config(load_config())
    pipe.face_detector = StubFaceDetector()
    return pipe


# --------------------------------------------------------------------------- #
# End-to-end behavior
# --------------------------------------------------------------------------- #
def test_end_to_end_routes_each_image(tmp_path: Path):
    src = _build_sample_folder(tmp_path)
    dest = tmp_path / "out"

    result = _pipeline().run(input_folder=src, destination_root=dest)

    assert isinstance(result, PipelineResult)
    assert result.scanned_count == 3
    assert result.duplicate_group_count == 1
    assert result.duplicate_count == 1
    assert result.blurry_count == 1
    assert result.category_counts == {
        FOLDER_BEST_SHOTS: 0,
        FOLDER_DUPLICATES: 1,
        FOLDER_BLURRY: 1,
        FOLDER_REVIEW: 1,
    }


def test_end_to_end_writes_expected_files(tmp_path: Path):
    src = _build_sample_folder(tmp_path)
    dest = tmp_path / "out"

    result = _pipeline().run(input_folder=src, destination_root=dest)

    output_root = Path(result.output_root)
    best_names = {p.name for p in (output_root / FOLDER_BEST_SHOTS).iterdir()}
    dup_names = {p.name for p in (output_root / FOLDER_DUPLICATES).iterdir()}
    blurry_names = {p.name for p in (output_root / FOLDER_BLURRY).iterdir()}
    review_names = {p.name for p in (output_root / FOLDER_REVIEW).iterdir()}

    # No faces -> nothing clears the BestShots floor. The duplicate copy goes
    # to Duplicates, the unusable gradient to Blurry, and the group
    # representative falls through to Review.
    assert best_names == set()
    assert dup_names == {"b_copy.png"}
    assert blurry_names == {"z_blurry.png"}
    assert review_names == {"a_sharp.png"}
    assert (output_root / FOLDER_BEST_SHOTS).is_dir()


def test_originals_are_preserved(tmp_path: Path):
    src = _build_sample_folder(tmp_path)
    before = {p.name for p in src.iterdir()}

    _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    after = {p.name for p in src.iterdir()}
    assert after == before  # source folder untouched


def test_default_destination_is_input_folder(tmp_path: Path):
    src = _build_sample_folder(tmp_path)

    result = _pipeline().run(input_folder=src)  # no destination_root

    assert Path(result.output_root) == src / "PhotoFlow_Output"
    assert (src / "PhotoFlow_Output").is_dir()


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #
def test_dry_run_reports_counts_without_copying(tmp_path: Path):
    src = _build_sample_folder(tmp_path)
    dest = tmp_path / "out"

    result = _pipeline().run(input_folder=src, destination_root=dest, dry_run=True)

    assert result.dry_run is True
    assert result.output_root is None
    assert result.organization is None
    assert result.category_counts == {
        FOLDER_BEST_SHOTS: 0,
        FOLDER_DUPLICATES: 1,
        FOLDER_BLURRY: 1,
        FOLDER_REVIEW: 1,
    }
    # Nothing was written anywhere.
    assert not dest.exists()
    assert not (src / "PhotoFlow_Output").exists()


# --------------------------------------------------------------------------- #
# Robustness / errors
# --------------------------------------------------------------------------- #
def test_corrupt_image_is_recorded_as_blur_failure(tmp_path: Path):
    src = tmp_path / "photos"
    src.mkdir()
    cv2.imwrite(str(src / "good.png"), _checkerboard())
    (src / "broken.png").write_bytes(b"not an image")

    result = _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    # The corrupt file can't be blur-analyzed but is still copied to Review.
    assert any("broken.png" in f for f in result.blur_failures)
    review = Path(result.output_root) / FOLDER_REVIEW
    assert "broken.png" in {p.name for p in review.iterdir()}


def test_missing_input_folder_raises_pipeline_error(tmp_path: Path):
    with pytest.raises(PipelineError):
        _pipeline().run(input_folder=tmp_path / "does_not_exist")


def test_empty_folder_produces_zero_counts(tmp_path: Path):
    src = tmp_path / "empty"
    src.mkdir()

    result = _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    assert result.scanned_count == 0
    assert result.category_counts == {
        FOLDER_BEST_SHOTS: 0,
        FOLDER_DUPLICATES: 0,
        FOLDER_BLURRY: 0,
        FOLDER_REVIEW: 0,
    }


def test_component_injection_is_used(tmp_path: Path):
    # A pipeline built from explicit components should behave like from_config.
    from core.blur_detector import BlurDetector
    from core.duplicate_detector import DuplicateDetector
    from core.organizer import PhotoOrganizer
    from core.scanner import ImageScanner

    config = load_config()
    pipeline = PhotoFlowPipeline(
        scanner=ImageScanner.from_config(config),
        duplicate_detector=DuplicateDetector.from_config(config),
        blur_detector=BlurDetector.from_config(config),
        organizer=PhotoOrganizer.from_config(config),
    )
    src = _build_sample_folder(tmp_path)

    result = pipeline.run(input_folder=src, destination_root=tmp_path / "out")

    assert result.scanned_count == 3


# --------------------------------------------------------------------------- #
# Parallelism + empty/unreadable handling
# --------------------------------------------------------------------------- #
def test_parallel_run_matches_sequential(tmp_path: Path):
    src = _build_sample_folder(tmp_path)

    seq = _pipeline()
    seq.max_workers = 1
    par = _pipeline()
    par.max_workers = 4

    r_seq = seq.run(input_folder=src, destination_root=tmp_path / "out_seq")
    r_par = par.run(input_folder=src, destination_root=tmp_path / "out_par")

    # Parallel execution must be deterministic and identical to sequential.
    assert r_par.category_counts == r_seq.category_counts
    assert r_par.best_shot_candidates == r_seq.best_shot_candidates
    assert r_par.duplicate_paths == r_seq.duplicate_paths
    assert r_par.scanned_count == r_seq.scanned_count == 3


def test_all_unreadable_flag_when_no_image_decodes(tmp_path: Path):
    src = tmp_path / "photos"
    src.mkdir()
    # Files with image extensions that are not decodable images.
    (src / "a.png").write_bytes(b"not an image")
    (src / "b.jpg").write_bytes(b"also not an image")

    result = _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    assert result.scanned_count == 2
    assert result.all_unreadable is True


def test_empty_folder_is_not_flagged_unreadable(tmp_path: Path):
    src = tmp_path / "empty"
    src.mkdir()

    result = _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    assert result.scanned_count == 0
    assert result.all_unreadable is False
