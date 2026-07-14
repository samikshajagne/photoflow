"""
Offline InsightFace embedding backend for PhotoFlow (Phase 2).

Provides a real, free, fully-offline face-recognition backend for
:class:`~core.face_embedder.FaceEmbedder` via its injectable seam. It wraps
InsightFace's ``buffalo_l`` pack (ArcFace ``w600k_r50`` recognition + SCRFD
detection/alignment), which runs locally on CPU through ONNX Runtime — no cloud
APIs, no paid services.

The backend is a callable mapping a list of face crops (RGB arrays, as produced
by ``FaceEmbedder``) to a list of embedding vectors. For each crop it runs
InsightFace's analysis (which re-detects and 5-point aligns the face inside the
crop, the key to good recognition accuracy) and returns the ArcFace embedding;
if no face is found in a crop it returns a zero vector (the clusterer treats it
as its own singleton rather than wrongly merging).

Install (one time):  ``pip install insightface onnxruntime``
First use downloads the ``buffalo_l`` models (~300 MB) to ``~/.insightface``.

Everything is lazily imported so importing this module never requires
InsightFace; a missing install fails clearly only when the backend is actually
invoked.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from core.face_embedder import EmbedBackend, FaceEmbeddingError
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL_PACK = "buffalo_l"
# ArcFace w600k_r50 produces 512-d embeddings.
_EMBED_DIM = 512


def build_insightface_backend(
    model_pack: str = DEFAULT_MODEL_PACK,
    ctx_id: int = -1,  # -1 = CPU; >=0 selects a GPU
    det_size: tuple[int, int] = (320, 320),
) -> EmbedBackend:
    """
    Build an embedding backend backed by InsightFace ``buffalo_l``.

    Args:
        model_pack: InsightFace model pack name (default ``"buffalo_l"``).
        ctx_id: Execution context; ``-1`` for CPU, ``>=0`` for a GPU device.
        det_size: Detector input size for the re-detection within each crop.

    Returns:
        A callable ``(crops: list[np.ndarray]) -> list[np.ndarray]`` suitable
        for ``FaceEmbedder(embed_backend=...)``.

    The heavy model is loaded once, lazily, on the first call. A missing
    ``insightface``/``onnxruntime`` install raises :class:`FaceEmbeddingError`
    with install guidance at that point.
    """
    state: dict[str, object] = {"app": None}

    def _ensure_app():
        if state["app"] is not None:
            return state["app"]
        try:
            from insightface.app import FaceAnalysis  # lazy heavy import
        except ImportError as exc:  # pragma: no cover - depends on local install
            raise FaceEmbeddingError(
                "InsightFace is not installed. Install the offline backend with "
                "'pip install insightface onnxruntime' (first use downloads the "
                f"'{model_pack}' models, ~300 MB, to ~/.insightface)."
            ) from exc
        try:
            app = FaceAnalysis(name=model_pack)
            app.prepare(ctx_id=ctx_id, det_size=det_size)
        except Exception as exc:  # pragma: no cover - model/runtime issues
            raise FaceEmbeddingError(
                f"Failed to initialize InsightFace '{model_pack}': {exc}"
            ) from exc
        state["app"] = app
        logger.info("InsightFace '%s' ready (ctx_id=%d).", model_pack, ctx_id)
        return app

    def backend(crops: list[np.ndarray]) -> list[np.ndarray]:
        app = _ensure_app()
        vectors: list[np.ndarray] = []
        for crop in crops:
            vectors.append(_embed_one(app, crop))
        return vectors

    return backend


def _embed_one(app, rgb_crop: np.ndarray) -> np.ndarray:
    """Embed a single RGB face crop; zero vector if no face is found."""
    import cv2  # local: only needed when the backend actually runs

    bgr = cv2.cvtColor(np.ascontiguousarray(rgb_crop), cv2.COLOR_RGB2BGR)
    try:
        faces = app.get(bgr)
    except Exception as exc:  # pragma: no cover - per-crop robustness
        logger.warning("InsightFace failed on a crop: %s", exc)
        return np.zeros(_EMBED_DIM, dtype=np.float32)
    if not faces:
        return np.zeros(_EMBED_DIM, dtype=np.float32)
    # Largest detected face in the crop is the subject.
    face = max(faces, key=lambda f: _area(getattr(f, "bbox", None)))
    embedding = getattr(face, "normed_embedding", None)
    if embedding is None:
        embedding = getattr(face, "embedding", None)
    if embedding is None:
        return np.zeros(_EMBED_DIM, dtype=np.float32)
    return np.asarray(embedding, dtype=np.float32)


def _area(bbox: Optional[np.ndarray]) -> float:
    if bbox is None:
        return 0.0
    x1, y1, x2, y2 = bbox[:4]
    return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))
