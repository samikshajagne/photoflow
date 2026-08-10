"""
Offline, commercially-licensed SFace embedding backend for PhotoFlow.

A drop-in alternative to :mod:`core.insightface_backend` for
:class:`~core.face_embedder.FaceEmbedder`'s injectable seam, built to remove
the **licensing blocker** on shipping PhotoFlow commercially.

Why this exists
---------------
InsightFace's *code* is MIT, but its README states that the training data
"and the models trained with these data are available for non-commercial
research purposes only" -- which covers the ``buffalo_l`` pack this project
otherwise uses (ArcFace ``w600k_r50``). That makes it unusable in a paid
product without a separate licence (InsightFace do now offer one:
``recognition-oss-pack@insightface.ai``).

This backend instead uses two models from OpenCV's ``opencv_zoo`` whose
weights are released under permissive licences that allow commercial use:

- **SFace** recognition (``face_recognition_sface_2021dec.onnx``) -- Apache-2.0
- **YuNet** detection (``face_detection_yunet_2023mar.onnx``) -- MIT

Both run locally on CPU through OpenCV's own ONNX support, so there is no new
Python dependency (OpenCV is already required) and nothing leaves the machine.

Accuracy caveat
---------------
SFace is smaller and generally less accurate than ArcFace ``w600k_r50``
(published third-party comparisons put SFace around 93% where ArcFace reaches
~99% on LFW-style benchmarks). **It also produces 128-d embeddings rather than
512-d, with a different distance distribution -- so the clustering threshold
must be re-tuned per backend.** See
:data:`core.person_cluster.SFACE_DISTANCE_MAX` and
``scripts/benchmark_embedders.py``, which measures the separation between
same-person and different-person distances on real photos.

Why detection runs *inside* each crop
-------------------------------------
Recognition accuracy depends heavily on alignment. ``FaceEmbedder`` hands this
backend loose face crops, so -- exactly as the InsightFace backend does -- YuNet
re-detects within each crop to recover the 5 landmarks that
``FaceRecognizerSF.alignCrop`` needs. Skipping alignment and feeding a plain
resized crop measurably degrades matching.

As with the InsightFace backend, a crop where no face can be found yields a
zero vector, which the clusterer treats as its own singleton rather than
wrongly merging it with someone.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np

from core.face_embedder import EmbedBackend, FaceEmbeddingError
from utils.logger import get_logger
from utils.paths import writable_model_dir

logger = get_logger(__name__)

# SFace outputs 128-d embeddings (ArcFace w600k_r50 outputs 512-d).
EMBED_DIM = 128

# Where model files live. Resolved at call time rather than import time because
# the right answer differs between a source checkout (alongside the bundled
# MediaPipe .tflite models, which is convenient) and an installed copy (whose
# Program Files directory isn't writable, so downloads must go to the per-user
# cache instead). See utils.paths.writable_model_dir.
DEFAULT_MODEL_DIR: Optional[Path] = None

SFACE_FILENAME = "face_recognition_sface_2021dec.onnx"
YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"

_ZOO_BASE = "https://github.com/opencv/opencv_zoo/raw/main/models"
SFACE_URL = f"{_ZOO_BASE}/face_recognition_sface/{SFACE_FILENAME}"
YUNET_URL = f"{_ZOO_BASE}/face_detection_yunet/{YUNET_FILENAME}"

# Guards against a truncated/HTML-error download being cached as a "model".
_MIN_PLAUSIBLE_MODEL_BYTES = 50_000


def ensure_model(
    filename: str,
    url: str,
    model_dir: Optional[Path] = None,
    allow_download: bool = True,
) -> Path:
    """
    Return the local path to a model file, downloading it if necessary.

    Mirrors the InsightFace backend's behaviour of fetching weights on first
    use (that pack is ~300 MB; these two total well under 50 MB).

    Raises:
        FaceEmbeddingError: if the file is absent and cannot be downloaded, or
            if what arrived is too small to be a real model.
    """
    model_dir = Path(model_dir) if model_dir is not None else writable_model_dir()
    path = model_dir / filename
    if path.exists() and path.stat().st_size >= _MIN_PLAUSIBLE_MODEL_BYTES:
        return path

    if not allow_download:
        raise FaceEmbeddingError(
            f"Face model '{filename}' not found at {path}. Download it from "
            f"{url} and place it in {model_dir}."
        )

    model_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    logger.info("Downloading face model '%s' from %s", filename, url)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, tmp.open("wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        tmp.unlink(missing_ok=True)
        raise FaceEmbeddingError(
            f"Could not download face model '{filename}' from {url}: {exc}. "
            f"Download it manually and place it in {model_dir}."
        ) from exc

    if tmp.stat().st_size < _MIN_PLAUSIBLE_MODEL_BYTES:
        size = tmp.stat().st_size
        tmp.unlink(missing_ok=True)
        raise FaceEmbeddingError(
            f"Downloaded '{filename}' is only {size} bytes -- that is not a "
            f"valid model (an error page?). Check {url}."
        )
    tmp.replace(path)
    logger.info("Face model ready: %s (%d bytes)", path, path.stat().st_size)
    return path


def build_sface_backend(
    model_dir: Optional[Path] = None,
    allow_download: bool = True,
    detector=None,
    recognizer=None,
) -> EmbedBackend:
    """
    Build an embedding backend backed by SFace (Apache-2.0) + YuNet (MIT).

    Args:
        model_dir: Directory holding (or to receive) the two ONNX models.
        allow_download: Fetch missing models on first use.
        detector: Pre-built detector with OpenCV's ``FaceDetectorYN`` interface
            (``setInputSize``/``detect``). Injectable for testing.
        recognizer: Pre-built recognizer with OpenCV's ``FaceRecognizerSF``
            interface (``alignCrop``/``feature``). Injectable for testing.

    Returns:
        A callable ``(crops: list[np.ndarray]) -> list[np.ndarray]`` suitable
        for ``FaceEmbedder(embed_backend=...)``. Crops are RGB; returned
        vectors are raw (``FaceEmbedder`` L2-normalizes them).

    Models load lazily on the first call, so building the backend is cheap and
    import-safe.
    """
    state: dict[str, object] = {"detector": detector, "recognizer": recognizer}

    def _ensure_models():
        if state["detector"] is not None and state["recognizer"] is not None:
            return state["detector"], state["recognizer"]
        import cv2  # local: only needed when the backend actually runs

        if not hasattr(cv2, "FaceRecognizerSF") or not hasattr(cv2, "FaceDetectorYN"):
            raise FaceEmbeddingError(
                "This OpenCV build lacks FaceRecognizerSF/FaceDetectorYN "
                f"(cv2 {getattr(cv2, '__version__', '?')}). Install "
                "opencv-python-headless>=4.5.4."
            )
        sface_path = ensure_model(SFACE_FILENAME, SFACE_URL, model_dir, allow_download)
        yunet_path = ensure_model(YUNET_FILENAME, YUNET_URL, model_dir, allow_download)
        try:
            if state["recognizer"] is None:
                state["recognizer"] = cv2.FaceRecognizerSF.create(str(sface_path), "")
            if state["detector"] is None:
                # Input size is set per-crop before each detect() call.
                state["detector"] = cv2.FaceDetectorYN.create(
                    str(yunet_path), "", (320, 320)
                )
        except Exception as exc:  # pragma: no cover - model/runtime issues
            raise FaceEmbeddingError(
                f"Failed to initialize SFace/YuNet from {model_dir}: {exc}"
            ) from exc
        logger.info("SFace + YuNet ready (Apache-2.0 / MIT weights, CPU).")
        return state["detector"], state["recognizer"]

    def backend(crops: list[np.ndarray]) -> list[np.ndarray]:
        det, rec = _ensure_models()
        return [_embed_one(det, rec, crop) for crop in crops]

    return backend


def _embed_one(detector, recognizer, rgb_crop: np.ndarray) -> np.ndarray:
    """Embed one RGB face crop; zero vector if no face can be aligned."""
    import cv2  # local: only needed when the backend actually runs

    zero = np.zeros(EMBED_DIM, dtype=np.float32)
    if rgb_crop is None or getattr(rgb_crop, "size", 0) == 0:
        return zero

    bgr = cv2.cvtColor(np.ascontiguousarray(rgb_crop), cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    if height < 2 or width < 2:
        return zero

    try:
        detector.setInputSize((width, height))
        _retval, faces = detector.detect(bgr)
    except Exception as exc:  # pragma: no cover - per-crop robustness
        logger.warning("YuNet failed on a crop: %s", exc)
        return zero

    if faces is None or len(faces) == 0:
        return zero

    # YuNet rows are [x, y, w, h, 5x landmark xy..., score]; largest face wins,
    # matching the InsightFace backend's "largest detected face is the subject".
    faces = np.asarray(faces, dtype=np.float32)
    largest = max(range(len(faces)), key=lambda i: float(faces[i][2] * faces[i][3]))
    try:
        aligned = recognizer.alignCrop(bgr, faces[largest])
        feature = recognizer.feature(aligned)
    except Exception as exc:  # pragma: no cover - per-crop robustness
        logger.warning("SFace failed on a crop: %s", exc)
        return zero

    vector = np.asarray(feature, dtype=np.float32).reshape(-1)
    if vector.size == 0:
        return zero
    return vector
