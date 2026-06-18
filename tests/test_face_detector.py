"""
Unit tests for core.face_detector.

The real MediaPipe model is heavyweight and may be unavailable in some
environments, so the wrapper's own responsibilities -- path/extension
validation, image loading, result shaping, error handling -- are tested by
monkeypatching the isolated ``_count_faces`` method. A guarded test exercises
the real backend only when MediaPipe's solutions API is present.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.face_detector import (
    DEFAULT_SUPPORTED_EXTENSIONS,
    FaceDetectionError,
    FaceDetector,
    FaceResult,
)
from utils.config import load_config


def _solutions_available() -> bool:
    try:
        import mediapipe as mp  # noqa: F401

        _ = mp.solutions.face_detection
        return True
    except Exception:
        return False


def _write_image(path: Path, ext: str = ".png") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = np.full((32, 32, 3), 127, dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return path


# --------------------------------------------------------------------------- #
# Result shaping (via monkeypatched detection)
# --------------------------------------------------------------------------- #
def test_faces_detected_true_when_count_positive(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(FaceDetector, "_count_faces", lambda self, img: 2)
    path = _write_image(tmp_path / "people.png")

    result = FaceDetector().detect(path)

    assert isinstance(result, FaceResult)
    assert result.face_count == 2
    assert result.faces_detected is True
    assert result.image_path == str(path)


def test_no_faces_when_count_zero(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(FaceDetector, "_count_faces", lambda self, img: 0)
    path = _write_image(tmp_path / "landscape.png")

    result = FaceDetector().detect(path)

    assert result.face_count == 0
    assert result.faces_detected is False


def test_as_dict_matches_spec_shape():
    result = FaceResult(image_path="/x/a.png", face_count=2, faces_detected=True)
    assert result.as_dict() == {"face_count": 2, "faces_detected": True}


@pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".png", ".bmp", ".tiff"])
def test_supported_formats_are_accepted(tmp_path: Path, monkeypatch, ext: str):
    monkeypatch.setattr(FaceDetector, "_count_faces", lambda self, img: 1)
    path = _write_image(tmp_path / f"img{ext}", ext)

    result = FaceDetector().detect(path)

    assert result.faces_detected is True


def test_extension_match_is_case_insensitive(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(FaceDetector, "_count_faces", lambda self, img: 0)
    path = _write_image(tmp_path / "IMG.PNG")

    result = FaceDetector().detect(path)

    assert result.face_count == 0


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
def test_unsupported_extension_raises(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("hi", encoding="utf-8")
    with pytest.raises(FaceDetectionError):
        FaceDetector().detect(path)


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FaceDetectionError):
        FaceDetector().detect(tmp_path / "nope.png")


def test_directory_path_raises(tmp_path: Path):
    with pytest.raises(FaceDetectionError):
        FaceDetector().detect(tmp_path)


def test_corrupt_image_raises(tmp_path: Path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")
    with pytest.raises(FaceDetectionError):
        FaceDetector().detect(path)


def test_empty_file_raises(tmp_path: Path):
    path = tmp_path / "empty.png"
    path.write_bytes(b"")
    with pytest.raises(FaceDetectionError):
        FaceDetector().detect(path)


# --------------------------------------------------------------------------- #
# Construction / config
# --------------------------------------------------------------------------- #
def test_confidence_out_of_range_raises():
    with pytest.raises(FaceDetectionError):
        FaceDetector(min_detection_confidence=-0.1)
    with pytest.raises(FaceDetectionError):
        FaceDetector(min_detection_confidence=1.5)


def test_empty_extensions_raises():
    with pytest.raises(FaceDetectionError):
        FaceDetector(supported_extensions=())


def test_extension_without_dot_raises():
    with pytest.raises(FaceDetectionError):
        FaceDetector(supported_extensions=("jpg",))


def test_invalid_model_selection_raises():
    with pytest.raises(FaceDetectionError):
        FaceDetector(model_selection=2)


def test_from_config_reads_confidence_and_extensions():
    config = load_config()
    detector = FaceDetector.from_config(config)
    assert detector.min_detection_confidence == config.thresholds.face_detection_confidence_min


def test_default_supported_extensions_cover_required_formats():
    for ext in (".jpg", ".jpeg", ".png", ".bmp", ".tiff"):
        assert ext in DEFAULT_SUPPORTED_EXTENSIONS


# --------------------------------------------------------------------------- #
# Backend availability
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(_solutions_available(), reason="MediaPipe backend is available")
def test_missing_backend_raises_clear_error(tmp_path: Path):
    # No monkeypatch: exercise the real lazy import path. Where MediaPipe's
    # solutions API is absent, detect() must raise FaceDetectionError.
    path = _write_image(tmp_path / "img.png")
    with pytest.raises(FaceDetectionError):
        FaceDetector().detect(path)


@pytest.mark.skipif(not _solutions_available(), reason="MediaPipe backend unavailable")
def test_real_backend_finds_no_faces_in_synthetic_image(tmp_path: Path):
    path = _write_image(tmp_path / "img.png")
    result = FaceDetector().detect(path)
    assert result.faces_detected is False
