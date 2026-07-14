"""
Tests for the InsightFace backend wiring.

The real model isn't available in CI, so these verify the wiring and the
graceful-failure contract rather than recognition accuracy.
"""

import numpy as np
import pytest

from core.face_embedder import FaceEmbeddingError
from core.insightface_backend import build_insightface_backend


def _have_insightface() -> bool:
    try:
        import insightface  # noqa: F401

        return True
    except ImportError:
        return False


def test_build_returns_callable_without_importing_model():
    # Constructing the backend must not require insightface to be installed.
    backend = build_insightface_backend()
    assert callable(backend)


@pytest.mark.skipif(_have_insightface(), reason="InsightFace is installed")
def test_missing_insightface_raises_clear_error():
    backend = build_insightface_backend()
    crop = np.zeros((112, 112, 3), np.uint8)
    with pytest.raises(FaceEmbeddingError):
        backend([crop])
