"""
Unit tests for core.blur_detector.

These synthesize images on disk with OpenCV rather than shipping fixtures,
so the suite is self-contained and deterministic. A high-frequency
checkerboard yields a very high Variance-of-Laplacian score (sharp); a
heavily Gaussian-blurred copy and a flat field yield low scores (blurry),
giving wide, stable separation around the default threshold of 100.

This module tests blur detection only — no face/quality/organization/UI.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.blur_detector import (
    DEFAULT_BLUR_SCORE_MIN,
    BlurDetectionError,
    BlurDetector,
    BlurResult,
)
from utils.config import load_config


# --------------------------------------------------------------------------- #
# Image fixture helpers
# --------------------------------------------------------------------------- #
def _checkerboard(size: int = 128, square: int = 8) -> np.ndarray:
    """High-frequency pattern -> high Laplacian variance (sharp)."""
    rows = []
    for r in range(size // square):
        row = [(0 if (r + c) % 2 else 255) for c in range(size // square)]
        rows.append(np.repeat(row, square))
    grid = np.repeat(np.array(rows, dtype=np.uint8), square, axis=0)
    return grid[:size, :size]


def _save_sharp(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), _checkerboard())
    return path


def _save_blurry(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    blurred = cv2.GaussianBlur(_checkerboard(), (15, 15), 5)
    cv2.imwrite(str(path), blurred)
    return path


def _save_uniform(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((128, 128), 127, dtype=np.uint8))
    return path


# --------------------------------------------------------------------------- #
# Core detection behavior
# --------------------------------------------------------------------------- #
def test_sharp_image_is_not_blurry(tmp_path: Path):
    path = _save_sharp(tmp_path / "sharp.png")

    result = BlurDetector().detect(path)

    assert isinstance(result, BlurResult)
    assert result.is_blurry is False
    assert result.blur_score >= DEFAULT_BLUR_SCORE_MIN


def test_blurry_image_is_blurry(tmp_path: Path):
    path = _save_blurry(tmp_path / "blurry.png")

    result = BlurDetector().detect(path)

    assert result.is_blurry is True
    assert result.blur_score < DEFAULT_BLUR_SCORE_MIN


def test_uniform_image_scores_near_zero_and_is_blurry(tmp_path: Path):
    path = _save_uniform(tmp_path / "flat.png")

    result = BlurDetector().detect(path)

    assert result.blur_score == pytest.approx(0.0, abs=1e-6)
    assert result.is_blurry is True


def test_result_reports_analyzed_path(tmp_path: Path):
    path = _save_sharp(tmp_path / "sharp.png")

    result = BlurDetector().detect(path)

    assert result.path == str(path)


def test_score_is_a_plain_float(tmp_path: Path):
    path = _save_sharp(tmp_path / "sharp.png")

    result = BlurDetector().detect(path)

    assert type(result.blur_score) is float


# --------------------------------------------------------------------------- #
# Threshold configurability
# --------------------------------------------------------------------------- #
def test_low_threshold_treats_blurry_image_as_sharp(tmp_path: Path):
    path = _save_blurry(tmp_path / "blurry.png")

    # A blurred checkerboard still scores ~36; a threshold below that flips
    # the verdict to "sharp", proving the threshold actually drives the call.
    result = BlurDetector(blur_score_min=5.0).detect(path)

    assert result.is_blurry is False


def test_high_threshold_treats_sharp_image_as_blurry(tmp_path: Path):
    path = _save_sharp(tmp_path / "sharp.png")

    result = BlurDetector(blur_score_min=1_000_000.0).detect(path)

    assert result.is_blurry is True


def test_score_is_independent_of_threshold(tmp_path: Path):
    path = _save_sharp(tmp_path / "sharp.png")

    score_low = BlurDetector(blur_score_min=1.0).detect(path).blur_score
    score_high = BlurDetector(blur_score_min=99999.0).detect(path).blur_score

    assert score_low == pytest.approx(score_high)


# --------------------------------------------------------------------------- #
# Supported formats
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".png", ".bmp", ".tiff"])
def test_supported_formats_are_analyzed(tmp_path: Path, ext: str):
    path = tmp_path / f"sharp{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), _checkerboard())

    result = BlurDetector().detect(path)

    # Lossy formats (jpg) shift the score slightly but it stays clearly sharp.
    assert result.is_blurry is False


def test_extension_match_is_case_insensitive(tmp_path: Path):
    path = tmp_path / "SHARP.PNG"
    cv2.imwrite(str(path), _checkerboard())

    result = BlurDetector().detect(path)

    assert result.is_blurry is False


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(BlurDetectionError):
        BlurDetector().detect(tmp_path / "nope.png")


def test_directory_path_raises(tmp_path: Path):
    with pytest.raises(BlurDetectionError):
        BlurDetector().detect(tmp_path)


def test_unsupported_extension_raises(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")

    with pytest.raises(BlurDetectionError):
        BlurDetector().detect(path)


def test_corrupt_image_raises(tmp_path: Path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"this is not a valid image")

    with pytest.raises(BlurDetectionError):
        BlurDetector().detect(path)


def test_empty_file_raises(tmp_path: Path):
    path = tmp_path / "empty.png"
    path.write_bytes(b"")

    with pytest.raises(BlurDetectionError):
        BlurDetector().detect(path)


# --------------------------------------------------------------------------- #
# Construction / configuration
# --------------------------------------------------------------------------- #
def test_negative_threshold_raises():
    with pytest.raises(BlurDetectionError):
        BlurDetector(blur_score_min=-1.0)


def test_empty_extensions_raises():
    with pytest.raises(BlurDetectionError):
        BlurDetector(supported_extensions=())


def test_extension_without_dot_raises():
    with pytest.raises(BlurDetectionError):
        BlurDetector(supported_extensions=("jpg",))


def test_from_config_uses_configured_threshold():
    config = load_config()
    detector = BlurDetector.from_config(config)

    assert detector.blur_score_min == config.thresholds.blur_score_min


def test_from_config_detector_works_end_to_end(tmp_path: Path):
    path = _save_sharp(tmp_path / "sharp.png")

    detector = BlurDetector.from_config(load_config())
    result = detector.detect(path)

    assert result.is_blurry is False
