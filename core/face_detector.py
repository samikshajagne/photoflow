"""
Face detection for PhotoFlow (Milestone 2, patched).

Detects whether an image contains human faces (and how many) using
MediaPipe's Face Detection solution. This is *detection only* -- PhotoFlow
deliberately does not do face recognition, identification, or matching; it
just answers "are there faces, and how many?" so the quality scorer can
favor photos of people.

The public entry point is :class:`FaceDetector`, built directly or from a
validated :class:`~utils.config.AppConfig` via
:meth:`FaceDetector.from_config` (reading
``thresholds.face_detection_confidence_min`` and optionally
``thresholds.face_model_path``). Its :meth:`~FaceDetector.detect` method
returns a :class:`FaceResult` carrying ``face_count`` and
``faces_detected``.

Backend selection
-----------------
MediaPipe has had two successive APIs:

* **Legacy Solutions API** (``mediapipe.solutions.face_detection``): present
  through 0.9.x and still importable on many 0.10.x builds, but deprecated
  and silently returns zero detections on some platforms.
* **Tasks Vision API** (``mediapipe.tasks.python.vision.FaceDetector``):
  the official API from 0.10 onward; requires a ``.tflite`` model file.

This module prefers the **Tasks API on mediapipe ≥ 0.10** and falls back to
the Solutions API on older versions, so it works correctly on both old and
new installs. If neither works, a clear :class:`FaceDetectionError` is
raised with installation guidance.

Performance
-----------
Large images (e.g. 24 MP wedding RAWs) are downsampled to
``max_analysis_edge_px`` before MediaPipe sees them. Detected bounding boxes
are scaled back to the original image coordinate space so all downstream
consumers (e.g. the quality scorer's subject-aware sharpness) receive
accurate relative coordinates.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

import cv2
import numpy as np

from utils.logger import get_logger
from utils.paths import resource_path

if TYPE_CHECKING:
    from utils.config import AppConfig

logger = get_logger(__name__)

PathLike = Union[str, Path]

# File extensions this engine accepts. Compared case-insensitively against a
# file's suffix. Module default so the detector is usable without a config.
DEFAULT_SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
)

# Mirrors thresholds.face_detection_confidence_min in default_config.yaml.
DEFAULT_MIN_DETECTION_CONFIDENCE: float = 0.5

# MediaPipe model_selection: 0 = short-range (~2m, selfies), 1 = full-range
# (~5m). Full-range is the more general default for organizing arbitrary
# photos.
DEFAULT_MODEL_SELECTION: int = 1

# Downsample images to this many pixels on their longest edge before running
# MediaPipe. Higher = better detection of small/distant faces at the cost of
# slightly more RAM. 1920 gives a good balance for typical wedding shoots
# (full-room group shots + closeup portraits) without hitting memory limits.
DEFAULT_MAX_ANALYSIS_EDGE_PX: int = 1920

# Tasks Vision API model, used when the Tasks backend is selected.
# Using the FULL-RANGE model: handles faces up to ~5m away (group shots,
# ceremony wide shots) vs the short-range model which is optimised for ~2m
# selfies. Full-range catches far-away faces at the cost of slightly worse
# very-closeup detection (negligible for wedding photos).
_MODEL_FILENAME = "blaze_face_full_range.tflite"
_MODEL_ENV_VAR = "PHOTOFLOW_FACE_MODEL"
# Read-only: resolved via utils.paths so it points inside the PyInstaller bundle
# when frozen and at the project's data/ directory when running from source.
_BUNDLED_MODEL = resource_path("data", "models", _MODEL_FILENAME)
_MODEL_DOWNLOAD_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_full_range/float16/latest/blaze_face_full_range.tflite"
)


class FaceDetectionError(Exception):
    """Raised when face detection cannot proceed (bad input, missing backend)."""


# A face bounding box as relative coordinates (xmin, ymin, width, height),
# each in [0, 1] of the image's width/height. Relative coords keep regions
# resolution-independent so downstream consumers (e.g. subject-aware
# sharpness in the quality scorer) can map them onto any decode size.
FaceBox = tuple[float, float, float, float]


@dataclasses.dataclass(frozen=True)
class FaceResult:
    """
    Outcome of analyzing one image for faces.

    Attributes:
        image_path: The analyzed image's path, as a string.
        face_count: Number of faces detected (>= 0).
        faces_detected: ``True`` when ``face_count`` is greater than zero.
        regions: Per-face bounding boxes as relative ``(xmin, ymin, width,
            height)`` tuples in ``[0, 1]``. Empty when no faces were found (or
            when a backend cannot supply boxes). Used by the quality scorer to
            measure sharpness on the subject rather than the whole frame.
    """

    image_path: str
    face_count: int
    faces_detected: bool
    regions: tuple[FaceBox, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return the compact ``{"face_count", "faces_detected"}`` form."""
        return {"face_count": self.face_count, "faces_detected": self.faces_detected}


# ---------------------------------------------------------------------------
# MediaPipe version helper
# ---------------------------------------------------------------------------

def _mp_version() -> tuple[int, int]:
    """Return (major, minor) of the installed mediapipe, or (0, 0) if absent."""
    try:
        import mediapipe as mp  # noqa: PLC0415
        parts = str(getattr(mp, "__version__", "0.0")).split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except Exception:  # noqa: BLE001
        return (0, 0)


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def _resolve_model_path(explicit: Optional[str]) -> Optional[Path]:
    """Find the Tasks face-detector model in (in order): explicit arg, the
    ``PHOTOFLOW_FACE_MODEL`` env var, the bundled ``data/models`` path, or the
    user cache. Returns ``None`` if none exist."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get(_MODEL_ENV_VAR)
    if env:
        candidates.append(Path(env))
    candidates.append(_BUNDLED_MODEL)
    candidates.append(Path.home() / ".cache" / "photoflow" / _MODEL_FILENAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Backend adapters
# ---------------------------------------------------------------------------

class _SolutionsBackend:
    """Adapter over the legacy ``mediapipe.solutions.face_detection`` API."""

    def __init__(self, face_detection_mod: Any, model_selection: int, min_conf: float) -> None:
        self._fd = face_detection_mod.FaceDetection(
            model_selection=model_selection, min_detection_confidence=min_conf
        )

    def detect(self, rgb_image: np.ndarray) -> list[FaceBox]:
        results = self._fd.process(rgb_image)
        detections = getattr(results, "detections", None) or []
        boxes: list[FaceBox] = []
        for det in detections:
            rbb = det.location_data.relative_bounding_box
            boxes.append(
                (float(rbb.xmin), float(rbb.ymin), float(rbb.width), float(rbb.height))
            )
        return boxes

    def close(self) -> None:
        try:
            self._fd.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


class _TasksBackend:
    """Adapter over the Tasks Vision ``FaceDetector`` API (needs a model file)."""

    def __init__(self, mp_module: Any, vision: Any, base_options_cls: Any,
                 model_path: Path, min_conf: float) -> None:
        options = vision.FaceDetectorOptions(
            base_options=base_options_cls(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.IMAGE,
            min_detection_confidence=min_conf,
        )
        self._mp = mp_module
        self._detector = vision.FaceDetector.create_from_options(options)

    def detect(self, rgb_image: np.ndarray) -> list[FaceBox]:
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(rgb_image),
        )
        result = self._detector.detect(image)
        detections = getattr(result, "detections", None) or []
        height, width = rgb_image.shape[:2]
        boxes: list[FaceBox] = []
        for det in detections:
            # Tasks API reports pixel coordinates; normalize to [0, 1].
            bb = det.bounding_box
            boxes.append(
                (
                    float(bb.origin_x) / width,
                    float(bb.origin_y) / height,
                    float(bb.width) / width,
                    float(bb.height) / height,
                )
            )
        return boxes

    def close(self) -> None:
        try:
            self._detector.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


# ---------------------------------------------------------------------------
# Public detector
# ---------------------------------------------------------------------------

class FaceDetector:
    """
    Detects faces in an image using MediaPipe Face Detection.

    Args:
        min_detection_confidence: Minimum MediaPipe confidence in ``[0, 1]``
            for a detection to count.
        supported_extensions: Accepted file extensions, each starting with a
            dot (e.g. ``".jpg"``). Matched case-insensitively. Must be
            non-empty.
        model_selection: MediaPipe legacy model (0 short-range, 1 full-range).
            Only used when the Solutions API backend is selected (mediapipe
            < 0.10). Ignored by the Tasks backend.
        model_path: Explicit path to the ``.tflite`` model file for the Tasks
            backend. When ``None``, the bundled ``data/models`` file (or the
            ``PHOTOFLOW_FACE_MODEL`` env var) is used.
        max_analysis_edge_px: Longest edge (pixels) to which large images are
            downsampled before analysis. ``0`` disables downsampling.

    Raises:
        FaceDetectionError: if any argument is out of range.
    """

    def __init__(
        self,
        min_detection_confidence: float = DEFAULT_MIN_DETECTION_CONFIDENCE,
        supported_extensions: tuple[str, ...] = DEFAULT_SUPPORTED_EXTENSIONS,
        model_selection: int = DEFAULT_MODEL_SELECTION,
        model_path: Optional[str] = None,
        max_analysis_edge_px: int = DEFAULT_MAX_ANALYSIS_EDGE_PX,
    ) -> None:
        if not 0.0 <= min_detection_confidence <= 1.0:
            raise FaceDetectionError(
                f"min_detection_confidence must be in [0, 1], got {min_detection_confidence}"
            )
        if not supported_extensions:
            raise FaceDetectionError("supported_extensions must not be empty")
        for ext in supported_extensions:
            if not ext.startswith("."):
                raise FaceDetectionError(
                    f"supported_extensions entries must start with '.', got '{ext}'"
                )
        if model_selection not in (0, 1):
            raise FaceDetectionError(
                f"model_selection must be 0 or 1, got {model_selection}"
            )
        if max_analysis_edge_px < 0:
            raise FaceDetectionError(
                f"max_analysis_edge_px must be >= 0, got {max_analysis_edge_px}"
            )

        self.min_detection_confidence = float(min_detection_confidence)
        self._extensions = frozenset(ext.lower() for ext in supported_extensions)
        self.model_selection = model_selection
        self._model_path = model_path
        self.max_analysis_edge_px = max_analysis_edge_px
        # Lazily created MediaPipe detector and a cached init error so a
        # missing/broken backend fails fast without retrying every image.
        self._detector: Optional[Any] = None
        self._init_error: Optional[FaceDetectionError] = None

    @classmethod
    def from_config(cls, config: "AppConfig") -> "FaceDetector":
        """
        Build a detector from a validated :class:`~utils.config.AppConfig`.

        Reads ``thresholds.face_detection_confidence_min``,
        ``thresholds.face_model_path`` (optional), ``io.supported_extensions``,
        and ``performance.analysis_max_edge_px``.
        """
        model_path: Optional[str] = getattr(config.thresholds, "face_model_path", None)
        return cls(
            min_detection_confidence=config.thresholds.face_detection_confidence_min,
            supported_extensions=config.io.supported_extensions,
            model_path=model_path,
            max_analysis_edge_px=config.performance.analysis_max_edge_px,
        )

    def detect(self, image_path: PathLike) -> FaceResult:
        """
        Detect faces in a single image.

        Args:
            image_path: Path to the image to analyze.

        Returns:
            A :class:`FaceResult` with ``face_count`` and ``faces_detected``.

        Raises:
            FaceDetectionError: if the path is missing, is not a file, has an
                unsupported extension, cannot be decoded, or the MediaPipe
                backend is unavailable.
        """
        path = Path(image_path)
        if not path.exists():
            raise FaceDetectionError(f"Image does not exist: {path}")
        if not path.is_file():
            raise FaceDetectionError(f"Path is not a file: {path}")
        if path.suffix.lower() not in self._extensions:
            raise FaceDetectionError(
                f"Unsupported file extension '{path.suffix}' for '{path}'. "
                f"Supported: {sorted(self._extensions)}"
            )

        rgb, scale = self._load_rgb(path)
        raw_regions = tuple(self._detect_regions(rgb))
        # Rescale bounding boxes back to original image coordinates when the
        # image was downsampled for analysis.
        regions = self._rescale_regions(raw_regions, scale)
        face_count = len(regions)
        faces_detected = face_count > 0

        logger.debug(
            "Face analysis '%s': faces=%d detected=%s (scale=%.3f)",
            path,
            face_count,
            faces_detected,
            scale,
        )
        return FaceResult(
            image_path=str(path),
            face_count=face_count,
            faces_detected=faces_detected,
            regions=regions,
        )

    def _load_rgb(self, path: Path) -> tuple[np.ndarray, float]:
        """
        Load an image as an RGB array, downsampling if it exceeds
        ``max_analysis_edge_px``.

        Returns:
            ``(rgb_array, scale)`` where ``scale`` is the ratio
            ``analysis_size / original_size`` (1.0 = no downsampling). Boxes
            returned by the backend are in analysis-image coordinates and must
            be divided by ``scale`` to get original-image relative coords.
            Since we use *relative* (0-1) coords, the boxes are already in
            [0,1] after dividing by the analysis image size — scale only
            matters for absolute px math, which we do not do here.

        Reads raw bytes and decodes via ``cv2.imdecode`` so non-ASCII paths
        work across platforms; decode/read failures raise
        :class:`FaceDetectionError`.
        """
        try:
            raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        except OSError as exc:
            raise FaceDetectionError(f"Failed to read image '{path}': {exc}") from exc
        if raw.size == 0:
            raise FaceDetectionError(f"Image file is empty: {path}")

        bgr = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if bgr is None:
            raise FaceDetectionError(
                f"Could not decode image (corrupt or unsupported): {path}"
            )

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        # Downsample to analysis size if needed.
        scale = 1.0
        if self.max_analysis_edge_px > 0:
            h, w = rgb.shape[:2]
            longest = max(h, w)
            if longest > self.max_analysis_edge_px:
                scale = self.max_analysis_edge_px / longest
                new_w = max(1, round(w * scale))
                new_h = max(1, round(h * scale))
                rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
                logger.debug(
                    "Face analysis: downsampled '%s' from %dx%d to %dx%d (scale=%.3f)",
                    path.name, w, h, new_w, new_h, scale,
                )
        return rgb, scale

    @staticmethod
    def _rescale_regions(
        regions: tuple[FaceBox, ...], scale: float
    ) -> tuple[FaceBox, ...]:
        """
        Adjust relative bounding boxes when the analysis image was downsampled.

        Relative coords (0-1) are resolution-independent by definition, so
        boxes detected on a downsampled image have the *same* relative coords
        as they would on the original. The boxes therefore need no rescaling —
        this method exists as a hook for future absolute-coord backends and to
        document the reasoning explicitly.
        """
        # Relative coords are scale-invariant; return unchanged.
        return regions

    def _detect_regions(self, rgb_image: np.ndarray) -> list[FaceBox]:
        """
        Run MediaPipe on an RGB image and return per-face bounding boxes.

        Isolated so it can be monkeypatched in tests and so the MediaPipe
        import stays lazy. Boxes are relative ``(xmin, ymin, width, height)``.
        """
        backend = self._get_detector()
        return backend.detect(rgb_image)

    def _count_faces(self, rgb_image: np.ndarray) -> int:
        """Convenience wrapper: number of faces (length of detected regions)."""
        return len(self._detect_regions(rgb_image))

    def _get_detector(self) -> Any:
        """Return a cached MediaPipe FaceDetection, creating it on first use."""
        if self._detector is not None:
            return self._detector
        if self._init_error is not None:
            raise self._init_error
        try:
            self._detector = self._create_detector()
        except FaceDetectionError as exc:
            self._init_error = exc
            raise
        return self._detector

    def _create_detector(self) -> Any:
        """
        Build a face-detection backend compatible with the installed MediaPipe.

        **Backend selection logic (fixes #1 from audit):**

        * mediapipe ≥ 0.10 → prefer Tasks API (Solutions deprecated/broken).
        * mediapipe < 0.10  → prefer Solutions API (Tasks not available).
        * Falls back to the other API if the preferred one fails.
        * Raises :class:`FaceDetectionError` with install guidance if neither works.

        The backend name is logged at INFO level so it appears in
        ``logs/photoflow.log`` and the diagnostic output for easy debugging.
        """
        try:
            import mediapipe as mp  # noqa: PLC0415
        except ImportError as exc:
            raise FaceDetectionError(
                "MediaPipe is not installed (pip install mediapipe)."
            ) from exc

        major, minor = _mp_version()
        use_tasks_first = (major, minor) >= (0, 10)

        if use_tasks_first:
            # Tasks API is the supported path on mediapipe 0.10+.
            tasks_backend = self._try_tasks_backend(mp)
            if tasks_backend is not None:
                logger.info(
                    "FaceDetector: using Tasks API backend (mediapipe %d.%d).",
                    major, minor,
                )
                return tasks_backend
            # Fallback to legacy Solutions (may still work on some 0.10 builds).
            solutions_backend = self._try_solutions_backend(mp)
            if solutions_backend is not None:
                logger.info(
                    "FaceDetector: Tasks API unavailable; using Solutions backend "
                    "(mediapipe %d.%d). Detection may be unreliable on this version.",
                    major, minor,
                )
                return solutions_backend
        else:
            # Legacy path: Solutions first, then Tasks.
            solutions_backend = self._try_solutions_backend(mp)
            if solutions_backend is not None:
                logger.info(
                    "FaceDetector: using Solutions API backend (mediapipe %d.%d).",
                    major, minor,
                )
                return solutions_backend
            tasks_backend = self._try_tasks_backend(mp)
            if tasks_backend is not None:
                logger.info(
                    "FaceDetector: Solutions API unavailable; using Tasks backend "
                    "(mediapipe %d.%d).",
                    major, minor,
                )
                return tasks_backend

        # Neither backend worked.
        model_path = _resolve_model_path(self._model_path)
        if model_path is None:
            raise FaceDetectionError(
                f"MediaPipe is installed (version {major}.{minor}) but face detection "
                f"failed to initialize. The Tasks backend also requires the model file "
                f"'{_MODEL_FILENAME}'. Download it from:\n  {_MODEL_DOWNLOAD_URL}\n"
                f"and place it at '{_BUNDLED_MODEL}', or set the "
                f"'{_MODEL_ENV_VAR}' environment variable to its path."
            )
        raise FaceDetectionError(
            f"MediaPipe face detection is unavailable (version {major}.{minor}): "
            "neither the Solutions nor the Tasks Vision API could be initialized."
        )

    def _try_solutions_backend(self, mp: Any) -> Optional[_SolutionsBackend]:
        """Attempt to build the legacy Solutions backend. Returns None on failure."""
        try:
            solutions = getattr(mp, "solutions", None)
            face_detection_mod = getattr(solutions, "face_detection", None) if solutions else None
            if face_detection_mod is None:
                return None
            backend = _SolutionsBackend(
                face_detection_mod, self.model_selection, self.min_detection_confidence
            )
            # Smoke-test: run on a tiny blank image to detect silent no-op builds.
            test_img = np.zeros((16, 16, 3), dtype=np.uint8)
            backend.detect(test_img)  # result doesn't matter; just must not error
            return backend
        except Exception as exc:  # noqa: BLE001
            logger.debug("FaceDetector: Solutions backend init failed: %s", exc)
            return None

    def _try_tasks_backend(self, mp: Any) -> Optional[_TasksBackend]:
        """Attempt to build the Tasks Vision backend. Returns None on failure."""
        try:
            from mediapipe.tasks.python import BaseOptions, vision  # noqa: PLC0415
        except ImportError:
            return None
        model_path = _resolve_model_path(self._model_path)
        if model_path is None:
            logger.debug(
                "FaceDetector: Tasks backend skipped — model file '%s' not found. "
                "Checked: bundled path '%s', env var '%s', ~/.cache/photoflow/.",
                _MODEL_FILENAME, _BUNDLED_MODEL, _MODEL_ENV_VAR,
            )
            return None
        try:
            backend = _TasksBackend(
                mp, vision, BaseOptions, model_path, self.min_detection_confidence
            )
            logger.debug(
                "FaceDetector: Tasks backend initialized with model '%s'.", model_path
            )
            return backend
        except Exception as exc:  # noqa: BLE001
            logger.debug("FaceDetector: Tasks backend init failed: %s", exc)
            return None
