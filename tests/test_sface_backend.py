"""
Tests for the Apache-2.0 SFace embedding backend (core/sface_backend.py).

The real ONNX weights aren't needed here: ``build_sface_backend`` accepts
injected detector/recognizer objects with OpenCV's ``FaceDetectorYN`` /
``FaceRecognizerSF`` interfaces, so the backend's contract (crop -> vector,
largest-face selection, zero-vector-on-failure, alignment being used at all)
is tested without a ~40 MB download or network access in CI.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.face_embedder import FaceEmbeddingError
from core.person_cluster import (
    ARCFACE_DISTANCE_MAX,
    SFACE_DISTANCE_MAX,
    distance_max_for_backend,
)
from core.sface_backend import (
    EMBED_DIM,
    SFACE_FILENAME,
    build_sface_backend,
    ensure_model,
)


# --------------------------------------------------------------------------- #
# Fakes matching OpenCV's interfaces
# --------------------------------------------------------------------------- #
class FakeDetector:
    """Stands in for cv2.FaceDetectorYN."""

    def __init__(self, faces=None, raise_on_detect=False):
        # YuNet rows: [x, y, w, h, 10 landmark coords, score] = 15 columns.
        self._faces = faces
        self._raise = raise_on_detect
        self.input_sizes: list[tuple[int, int]] = []

    def setInputSize(self, size):  # noqa: N802 - mirrors the cv2 API
        self.input_sizes.append(size)

    def detect(self, _bgr):
        if self._raise:
            raise RuntimeError("detector exploded")
        return 1, self._faces


class FakeRecognizer:
    """Stands in for cv2.FaceRecognizerSF."""

    def __init__(self, raise_on_feature=False):
        self._raise = raise_on_feature
        self.aligned_with: list[np.ndarray] = []

    def alignCrop(self, _bgr, face_row):  # noqa: N802 - mirrors the cv2 API
        self.aligned_with.append(np.asarray(face_row, dtype=np.float32))
        return np.zeros((112, 112, 3), dtype=np.uint8)

    def feature(self, _aligned):
        if self._raise:
            raise RuntimeError("recognizer exploded")
        # Real SFace returns shape (1, 128).
        return np.arange(EMBED_DIM, dtype=np.float32).reshape(1, EMBED_DIM)


def _face_row(x=0.0, y=0.0, w=50.0, h=50.0) -> list[float]:
    return [x, y, w, h] + [1.0] * 10 + [0.99]


def _crop(size: int = 64) -> np.ndarray:
    return np.full((size, size, 3), 128, dtype=np.uint8)


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
def test_backend_returns_one_vector_per_crop_of_expected_dim():
    backend = build_sface_backend(
        detector=FakeDetector(faces=np.array([_face_row()], dtype=np.float32)),
        recognizer=FakeRecognizer(),
    )
    out = backend([_crop(), _crop()])
    assert len(out) == 2
    for vec in out:
        assert vec.shape == (EMBED_DIM,)
        assert vec.dtype == np.float32


def test_backend_returns_zero_vector_when_no_face_found():
    """Zero vector is the agreed 'no face' sentinel -- the clusterer keeps it as
    its own singleton rather than merging it into someone else."""
    backend = build_sface_backend(
        detector=FakeDetector(faces=None), recognizer=FakeRecognizer()
    )
    (vec,) = backend([_crop()])
    assert vec.shape == (EMBED_DIM,)
    assert not np.any(vec)


def test_backend_returns_zero_vector_for_empty_face_list():
    backend = build_sface_backend(
        detector=FakeDetector(faces=np.empty((0, 15), dtype=np.float32)),
        recognizer=FakeRecognizer(),
    )
    (vec,) = backend([_crop()])
    assert not np.any(vec)


def test_backend_picks_the_largest_face_in_the_crop():
    small = _face_row(x=0, y=0, w=10, h=10)
    big = _face_row(x=20, y=20, w=80, h=80)
    rec = FakeRecognizer()
    backend = build_sface_backend(
        detector=FakeDetector(faces=np.array([small, big], dtype=np.float32)),
        recognizer=rec,
    )
    backend([_crop()])
    # The row handed to alignCrop must be the larger face.
    assert rec.aligned_with[0][2] == pytest.approx(80.0)
    assert rec.aligned_with[0][3] == pytest.approx(80.0)


def test_backend_aligns_before_extracting_features():
    """Alignment is what makes recognition accurate; skipping it silently
    degrades matching, so assert it actually happens."""
    rec = FakeRecognizer()
    backend = build_sface_backend(
        detector=FakeDetector(faces=np.array([_face_row()], dtype=np.float32)),
        recognizer=rec,
    )
    backend([_crop()])
    assert len(rec.aligned_with) == 1


def test_backend_sets_input_size_per_crop():
    """YuNet needs its input size set to each crop's dimensions before detect."""
    det = FakeDetector(faces=np.array([_face_row()], dtype=np.float32))
    backend = build_sface_backend(detector=det, recognizer=FakeRecognizer())
    backend([_crop(64), _crop(96)])
    assert det.input_sizes == [(64, 64), (96, 96)]


def test_backend_survives_detector_and_recognizer_errors():
    failing_det = build_sface_backend(
        detector=FakeDetector(raise_on_detect=True), recognizer=FakeRecognizer()
    )
    (vec,) = failing_det([_crop()])
    assert not np.any(vec)

    failing_rec = build_sface_backend(
        detector=FakeDetector(faces=np.array([_face_row()], dtype=np.float32)),
        recognizer=FakeRecognizer(raise_on_feature=True),
    )
    (vec2,) = failing_rec([_crop()])
    assert not np.any(vec2)


def test_backend_handles_degenerate_crops():
    backend = build_sface_backend(
        detector=FakeDetector(faces=np.array([_face_row()], dtype=np.float32)),
        recognizer=FakeRecognizer(),
    )
    out = backend([np.empty((0, 0, 3), dtype=np.uint8), np.zeros((1, 1, 3), np.uint8)])
    assert all(not np.any(v) for v in out)


def test_backend_is_lazy_and_needs_no_models_until_called():
    """Building must not touch disk/network, so import and construction stay
    cheap even on a machine with no models downloaded."""
    backend = build_sface_backend(
        model_dir=Path("/nonexistent"), allow_download=False
    )
    assert callable(backend)


# --------------------------------------------------------------------------- #
# Model resolution
# --------------------------------------------------------------------------- #
def test_ensure_model_errors_clearly_when_missing_and_download_disabled(tmp_path):
    with pytest.raises(FaceEmbeddingError) as excinfo:
        ensure_model(SFACE_FILENAME, "https://example.invalid/m.onnx", tmp_path, False)
    message = str(excinfo.value)
    assert SFACE_FILENAME in message
    assert str(tmp_path) in message  # tells the user exactly where to put it


def test_ensure_model_accepts_an_existing_plausible_file(tmp_path):
    path = tmp_path / SFACE_FILENAME
    path.write_bytes(b"x" * 60_000)
    assert ensure_model(SFACE_FILENAME, "https://example.invalid/m.onnx", tmp_path) == path


def test_ensure_model_rejects_a_truncated_file_and_redownloads(tmp_path, monkeypatch):
    """A tiny file is usually a cached error page, not a model."""
    path = tmp_path / SFACE_FILENAME
    path.write_bytes(b"<html>404</html>")

    monkeypatch.setattr(
        "core.sface_backend.urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no network")),
    )
    # Too small to trust -> tries to re-download -> fails loudly rather than
    # silently loading a bogus model.
    with pytest.raises(FaceEmbeddingError):
        ensure_model(SFACE_FILENAME, "https://example.invalid/m.onnx", tmp_path)


# --------------------------------------------------------------------------- #
# Per-backend clustering thresholds
# --------------------------------------------------------------------------- #
def test_sface_threshold_is_tighter_than_arcface():
    """SFace embeddings separate less than ArcFace's, so reusing ArcFace's
    threshold would merge different guests into one person."""
    assert SFACE_DISTANCE_MAX < ARCFACE_DISTANCE_MAX


def test_distance_max_lookup_by_backend_name():
    assert distance_max_for_backend("sface") == SFACE_DISTANCE_MAX
    assert distance_max_for_backend("arcface") == ARCFACE_DISTANCE_MAX
    assert distance_max_for_backend("insightface") == ARCFACE_DISTANCE_MAX
    assert distance_max_for_backend("BUFFALO_L") == ARCFACE_DISTANCE_MAX  # case-insensitive


def test_unknown_backend_falls_back_to_arcface_with_a_warning(caplog):
    with caplog.at_level("WARNING"):
        assert distance_max_for_backend("some-new-model") == ARCFACE_DISTANCE_MAX
    assert "no tuned clustering threshold" in caplog.text.lower()
