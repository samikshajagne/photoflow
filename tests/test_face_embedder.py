"""
Unit tests for core.face_embedder.

The heavy recognition model is injected as a fake backend, so the wrapper's own
responsibilities -- validation, cropping, normalization, ordering, error
handling -- are exercised without any model.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.face_embedder import FaceEmbedder, FaceEmbeddingError
from utils.config import load_config


def _write_image(path: Path, size: int = 200, value: int = 127) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((size, size, 3), value, np.uint8))
    return path


def _counting_backend(record: list):
    """A backend that records crop shapes and returns a vector per crop."""

    def backend(crops):
        record.extend(crop.shape for crop in crops)
        # Return a distinct, non-unit vector per crop to test normalization.
        return [np.full(8, float(i + 1), dtype=np.float32) for i in range(len(crops))]

    return backend


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_embeds_each_region_and_normalizes(tmp_path: Path):
    path = _write_image(tmp_path / "people.png")
    shapes: list = []
    embedder = FaceEmbedder(embed_backend=_counting_backend(shapes))

    regions = [(0.0, 0.0, 0.5, 0.5), (0.5, 0.5, 0.4, 0.4)]
    results = embedder.embed(path, regions)

    assert len(results) == 2
    assert [r.face_index for r in results] == [0, 1]
    assert all(r.image_path == str(path) for r in results)
    # Vectors are L2-normalized (unit length).
    for r in results:
        assert np.linalg.norm(r.vector) == pytest.approx(1.0, abs=1e-5)
    # Two crops were handed to the backend.
    assert len(shapes) == 2


def test_no_regions_returns_empty_without_backend(tmp_path: Path):
    path = _write_image(tmp_path / "landscape.png")
    # No backend configured, but with no regions embed() must not need one.
    results = FaceEmbedder().embed(path, [])
    assert results == []


def test_tiny_faces_are_skipped(tmp_path: Path):
    path = _write_image(tmp_path / "img.png", size=200)
    shapes: list = []
    embedder = FaceEmbedder(embed_backend=_counting_backend(shapes), min_face_px=24)

    # First box ~100px (kept), second box ~4px (skipped).
    regions = [(0.0, 0.0, 0.5, 0.5), (0.1, 0.1, 0.02, 0.02)]
    results = embedder.embed(path, regions)

    assert len(results) == 1
    assert results[0].face_index == 0  # index preserved despite the skip


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
def test_missing_backend_raises(tmp_path: Path):
    path = _write_image(tmp_path / "img.png")
    with pytest.raises(FaceEmbeddingError):
        FaceEmbedder().embed(path, [(0.0, 0.0, 0.5, 0.5)])


def test_backend_count_mismatch_raises(tmp_path: Path):
    path = _write_image(tmp_path / "img.png")
    embedder = FaceEmbedder(embed_backend=lambda crops: [])  # returns too few
    with pytest.raises(FaceEmbeddingError):
        embedder.embed(path, [(0.0, 0.0, 0.5, 0.5)])


def test_missing_file_raises():
    embedder = FaceEmbedder(embed_backend=lambda crops: [np.ones(4)])
    with pytest.raises(FaceEmbeddingError):
        embedder.embed("/no/such/file.png", [(0.0, 0.0, 0.5, 0.5)])


def test_unsupported_extension_raises(tmp_path: Path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hi", encoding="utf-8")
    embedder = FaceEmbedder(embed_backend=lambda crops: [np.ones(4)])
    with pytest.raises(FaceEmbeddingError):
        embedder.embed(bad, [(0.0, 0.0, 0.5, 0.5)])


def test_corrupt_image_raises(tmp_path: Path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    embedder = FaceEmbedder(embed_backend=lambda crops: [np.ones(4)])
    with pytest.raises(FaceEmbeddingError):
        embedder.embed(bad, [(0.0, 0.0, 0.5, 0.5)])


# --------------------------------------------------------------------------- #
# Construction / config
# --------------------------------------------------------------------------- #
def test_empty_extensions_raises():
    with pytest.raises(FaceEmbeddingError):
        FaceEmbedder(supported_extensions=())


def test_extension_without_dot_raises():
    with pytest.raises(FaceEmbeddingError):
        FaceEmbedder(supported_extensions=("jpg",))


def test_bad_min_face_px_raises():
    with pytest.raises(FaceEmbeddingError):
        FaceEmbedder(min_face_px=0)


def test_from_config_reads_extensions():
    config = load_config()
    embedder = FaceEmbedder.from_config(config)
    # Supported extensions come from config; a config ext is accepted.
    assert ".jpg" in embedder._extensions
