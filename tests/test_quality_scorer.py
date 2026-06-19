"""
Unit tests for core.quality_scorer.

The pure scoring math (``combine``) is tested with plain numbers; the
image-based path (``score``) is tested against synthesized OpenCV images so
brightness/contrast derivation is exercised end to end. Face presence is a
fourth active signal (``face_weight``); tests covering the other signals pin
``face_weight`` to 0 where they need a face-independent comparison.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.quality_scorer import (
    QualityResult,
    QualityScorer,
    QualityScoringError,
)
from utils.config import load_config


# --------------------------------------------------------------------------- #
# Pure scoring math
# --------------------------------------------------------------------------- #
def test_score_is_within_bounds_across_extremes():
    scorer = QualityScorer()
    for blur, bright, contrast in [
        (0, 0, 0),
        (1e9, 127.5, 200),
        (5000, 128, 70),
        (10, 255, 0),
    ]:
        for faces in (False, True):
            score = scorer.combine(blur, bright, contrast, faces_detected=faces)
            assert 0.0 <= score <= 100.0


def test_worst_case_is_zero():
    assert QualityScorer().combine(blur_score=0, brightness=0, contrast=0) == 0.0


def test_best_case_is_one_hundred():
    # Maximum score now requires a face as well (face_weight is active).
    score = QualityScorer().combine(
        blur_score=1e9, brightness=127.5, contrast=500, faces_detected=True
    )
    assert score == pytest.approx(100.0)


def test_best_case_without_face_is_below_one_hundred():
    score = QualityScorer().combine(
        blur_score=1e9, brightness=127.5, contrast=500, faces_detected=False
    )
    assert score < 100.0


def test_sharper_image_scores_higher():
    scorer = QualityScorer()
    low = scorer.combine(blur_score=10, brightness=128, contrast=70)
    high = scorer.combine(blur_score=10_000, brightness=128, contrast=70)
    assert high > low


def test_better_exposure_scores_higher():
    scorer = QualityScorer()
    dark = scorer.combine(blur_score=5000, brightness=10, contrast=70)
    well = scorer.combine(blur_score=5000, brightness=128, contrast=70)
    assert well > dark


def test_higher_contrast_scores_higher():
    scorer = QualityScorer()
    flat = scorer.combine(blur_score=5000, brightness=128, contrast=2)
    punchy = scorer.combine(blur_score=5000, brightness=128, contrast=80)
    assert punchy > flat


def test_face_presence_scores_higher():
    scorer = QualityScorer()
    without = scorer.combine(blur_score=5000, brightness=128, contrast=70, faces_detected=False)
    with_face = scorer.combine(blur_score=5000, brightness=128, contrast=70, faces_detected=True)
    assert with_face > without


def test_zero_face_weight_disables_face_effect():
    scorer = QualityScorer(face_weight=0.0)
    without = scorer.combine(blur_score=5000, brightness=128, contrast=70, faces_detected=False)
    with_face = scorer.combine(blur_score=5000, brightness=128, contrast=70, faces_detected=True)
    assert without == with_face


def test_blur_subscore_is_half_at_reference():
    # Pure-blur weighting (exposure and face pinned to 0): an image at exactly
    # blur_score_min earns half the sharpness sub-score.
    scorer = QualityScorer(
        blur_weight=1.0, exposure_weight=0.0, face_weight=0.0, blur_score_min=100.0
    )
    score = scorer.combine(blur_score=100.0, brightness=0, contrast=0)
    assert score == pytest.approx(50.0)


def test_weights_change_the_outcome():
    # A sharp but badly exposed image scores higher when blur is weighted more
    # heavily. Face pinned to 0 to isolate the blur-vs-exposure trade-off.
    blur_heavy = QualityScorer(blur_weight=0.9, exposure_weight=0.1, face_weight=0.0)
    exposure_heavy = QualityScorer(blur_weight=0.1, exposure_weight=0.9, face_weight=0.0)
    args = dict(blur_score=10_000, brightness=5, contrast=2)
    assert blur_heavy.combine(**args) > exposure_heavy.combine(**args)


# --------------------------------------------------------------------------- #
# Construction / config
# --------------------------------------------------------------------------- #
def test_negative_weight_raises():
    with pytest.raises(QualityScoringError):
        QualityScorer(blur_weight=-0.1)


def test_negative_face_weight_raises():
    with pytest.raises(QualityScoringError):
        QualityScorer(face_weight=-0.1)


def test_zero_active_weight_raises():
    with pytest.raises(QualityScoringError):
        QualityScorer(blur_weight=0.0, exposure_weight=0.0, face_weight=0.0)


def test_nonpositive_blur_reference_raises():
    with pytest.raises(QualityScoringError):
        QualityScorer(blur_score_min=0.0)


def test_from_config_reads_scoring_weights():
    config = load_config()
    scorer = QualityScorer.from_config(config)
    assert scorer.blur_weight == config.scoring_weights.blur_weight
    assert scorer.exposure_weight == config.scoring_weights.exposure_weight
    assert scorer.face_weight == config.scoring_weights.face_weight
    assert scorer.blur_score_min == config.thresholds.blur_score_min


# --------------------------------------------------------------------------- #
# Image-based scoring
# --------------------------------------------------------------------------- #
def _save_gray(path: Path, value: int) -> Path:
    cv2.imwrite(str(path), np.full((64, 64), value, dtype=np.uint8))
    return path


def test_score_returns_quality_result(tmp_path: Path):
    path = _save_gray(tmp_path / "mid.png", 128)

    result = QualityScorer().score(path, blur_score=5000.0)

    assert isinstance(result, QualityResult)
    assert result.image_path == str(path)
    assert 0.0 <= result.quality_score <= 100.0
    assert result.blur_score == 5000.0
    assert result.faces_detected is False
    assert result.face_count == 0


def test_score_includes_face_information(tmp_path: Path):
    path = _save_gray(tmp_path / "mid.png", 128)

    no_face = QualityScorer().score(path, blur_score=5000.0)
    with_face = QualityScorer().score(path, blur_score=5000.0, faces_detected=True, face_count=2)

    assert with_face.faces_detected is True
    assert with_face.face_count == 2
    # Same pixels, but the face raises the quality score.
    assert with_face.quality_score > no_face.quality_score


def test_score_measures_brightness_and_contrast(tmp_path: Path):
    # A flat 64x64 field: brightness == fill value, contrast (std) == 0.
    path = _save_gray(tmp_path / "flat.png", 200)

    result = QualityScorer().score(path, blur_score=5000.0)

    assert result.brightness == pytest.approx(200.0, abs=0.5)
    assert result.contrast == pytest.approx(0.0, abs=1e-6)


def test_score_missing_file_raises(tmp_path: Path):
    with pytest.raises(QualityScoringError):
        QualityScorer().score(tmp_path / "nope.png", blur_score=1.0)


def test_score_corrupt_image_raises(tmp_path: Path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")

    with pytest.raises(QualityScoringError):
        QualityScorer().score(bad, blur_score=1.0)


# --------------------------------------------------------------------------- #
# Subject-aware sharpness
# --------------------------------------------------------------------------- #
def _checkerboard(size: int = 128, square: int = 8) -> np.ndarray:
    rows = []
    for r in range(size // square):
        row = [(0 if (r + c) % 2 else 255) for c in range(size // square)]
        rows.append(np.repeat(row, square))
    return np.repeat(np.array(rows, dtype=np.uint8), square, axis=0)[:size, :size]


def test_subject_aware_sharpness_measures_the_face_region(tmp_path: Path):
    # A mostly-flat (soft) frame with a sharp checkerboard patch in the top-left
    # corner -- like a tack-sharp face against a bokeh background.
    frame = np.full((100, 100), 128, dtype=np.uint8)
    frame[0:20, 0:20] = _checkerboard(size=20, square=2)
    path = tmp_path / "bokeh.png"
    cv2.imwrite(str(path), frame)

    scorer = QualityScorer()
    # Whole-frame blur score is low (soft frame); the face box covers the patch.
    no_region = scorer.score(path, blur_score=5.0)
    with_region = scorer.score(
        path,
        blur_score=5.0,
        faces_detected=True,
        face_count=1,
        face_regions=[(0.0, 0.0, 0.2, 0.2)],
    )

    # Sharpness (and thus quality) is taken from the sharp face region, not the
    # soft whole frame.
    assert with_region.sharpness > no_region.sharpness
    assert with_region.quality_score > no_region.quality_score


def test_degenerate_face_region_falls_back_to_global(tmp_path: Path):
    path = _save_gray(tmp_path / "mid.png", 128)
    scorer = QualityScorer()
    # A zero-size box yields no usable crop -> fall back to the global blur score.
    result = scorer.score(
        path, blur_score=5000.0, faces_detected=True, face_regions=[(0.5, 0.5, 0.0, 0.0)]
    )
    assert result.sharpness == 5000.0


# --------------------------------------------------------------------------- #
# Usability gate (independent of the quality ranking)
# --------------------------------------------------------------------------- #
def test_normal_image_is_usable(tmp_path: Path):
    path = _save_gray(tmp_path / "ok.png", 128)
    result = QualityScorer().score(path, blur_score=5000.0)
    assert result.usable is True


def test_severely_blurred_image_is_unusable(tmp_path: Path):
    # Flat field -> Laplacian variance ~0, far below the sharpness floor.
    path = _save_gray(tmp_path / "soft.png", 128)
    result = QualityScorer().score(path, blur_score=1.0)
    assert result.usable is False


def test_near_black_image_is_unusable(tmp_path: Path):
    # Low-amplitude checkerboard: textured (passes the sharpness floor) but
    # almost black (mean ~10), so it fails the brightness floor.
    frame = np.where(_checkerboard() > 0, 20, 0).astype(np.uint8)
    path = tmp_path / "dark.png"
    cv2.imwrite(str(path), frame)
    result = QualityScorer().score(path, blur_score=5000.0)
    assert result.brightness < 12.0
    assert result.usable is False


def test_blown_out_image_is_unusable(tmp_path: Path):
    frame = np.where(_checkerboard() > 0, 255, 235).astype(np.uint8)
    path = tmp_path / "blown.png"
    cv2.imwrite(str(path), frame)
    result = QualityScorer().score(path, blur_score=5000.0)
    assert result.brightness > 243.0
    assert result.usable is False


def test_usability_bounds_validation():
    with pytest.raises(QualityScoringError):
        QualityScorer(usable_sharpness_min=-1.0)
    with pytest.raises(QualityScoringError):
        QualityScorer(usable_brightness_min=200.0, usable_brightness_max=100.0)
