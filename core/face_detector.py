"""
Face detection for PhotoFlow (Milestone 2).

Detects whether an image contains human faces (and how many) using
MediaPipe's Face Detection solution. This is *detection only* -- PhotoFlow
deliberately does not do face recognition, identification, or matching; it
just answers "are there faces, and how many?" so the quality scorer can
favor photos of people.

The public entry point is :class:`FaceDetector`, built directly or from a
validated :class:`~utils.config.AppConfig` via
:meth:`FaceDetector.from_config` (reading
``thresholds.face_detection_confidence_min``). Its
:meth:`~FaceDetector.detect` method returns a :class:`FaceResult` carrying
``face_count`` and ``faces_detected``.

The MediaPipe dependency is imported lazily and kept behind
:meth:`~FaceDetector._count_faces` so the rest of the module imports cleanly
even where MediaPipe isn't installed, and so the wrapper logic is testable
without the heavyweight model.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

import cv2
import numpy as np

from utils.logger import get_logger

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
    ".tiff",
)

# Mirrors thresholds.face_detection_confidence_min in default_config.yaml.
DEFAULT_MIN_DETECTION_CONFIDENCE: float = 0.5

# MediaPipe model_selection: 0 = short-range (~2m, selfies), 1 = full-range
# (~5m). Full-range is the more general default for organizing arbitrary
# photos.
DEFAULT_MODEL_SELECTION: int = 1


class FaceDetectionError(Exception):
    """Raised when face detection cannot proceed (bad input, missing backend)."""


@dataclasses.dataclass(frozen=True)
class FaceResult:
    """
    Outcome of analyzing one image for faces.

    Attributes:
        image_path: The analyzed image's path, as a string.
        face_count: Number of faces detected (>= 0).
        faces_detected: ``True`` when ``face_count`` is greater than zero.
    """

    image_path: str
    face_count: int
    faces_detected: bool

    def as_dict(self) -> dict[str, Any]:
        """Return the compact ``{"face_count", "faces_detected"}`` form."""
        return {"face_count": self.face_count, "faces_detected": self.faces_detected}


class FaceDetector:
    """
    Detects faces in an image using MediaPipe Face Detection.

    Args:
        min_detection_confidence: Minimum MediaPipe confidence in ``[0, 1]``
            for a detection to count.
        supported_extensions: Accepted file extensions, each starting with a
            dot (e.g. ``".jpg"``). Matched case-insensitively. Must be
            non-empty.
        model_selection: MediaPipe model (0 short-range, 1 full-range).

    Raises:
        FaceDetectionError: if any argument is out of range.
    """

    def __init__(
        self,
        min_detection_confidence: float = DEFAULT_MIN_DETECTION_CONFIDENCE,
        supported_extensions: tuple[str, ...] = DEFAULT_SUPPORTED_EXTENSIONS,
        model_selection: int = DEFAULT_MODEL_SELECTION,
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

        self.min_detection_confidence = float(min_detection_confidence)
        self._extensions = frozenset(ext.lower() for ext in supported_extensions)
        self.model_selection = model_selection
        # Lazily created MediaPipe detector and a cached init error so a
        # missing/broken backend fails fast without retrying every image.
        self._detector: Optional[Any] = None
        self._init_error: Optional[FaceDetectionError] = None

    @classmethod
    def from_config(cls, config: "AppConfig") -> "FaceDetector":
        """
        Build a detector from a validated :class:`~utils.config.AppConfig`.

        Reads ``thresholds.face_detection_confidence_min`` and
        ``io.supported_extensions``.
        """
        return cls(
            min_detection_confidence=config.thresholds.face_detection_confidence_min,
            supported_extensions=config.io.supported_extensions,
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

        rgb = self._load_rgb(path)
        face_count = self._count_faces(rgb)
        faces_detected = face_count > 0

        logger.info(
            "Face analysis '%s': faces=%d detected=%s",
            path,
            face_count,
            faces_detected,
        )
        return FaceResult(
            image_path=str(path),
            face_count=face_count,
            faces_detected=faces_detected,
        )

    def _load_rgb(self, path: Path) -> np.ndarray:
        """
        Load an image as an RGB array (MediaPipe expects RGB).

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
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def _count_faces(self, rgb_image: np.ndarray) -> int:
        """
        Run MediaPipe on an RGB image and return the number of faces.

        Isolated so it can be monkeypatched in tests and so the MediaPipe
        import stays lazy.
        """
        detector = self._get_detector()
        results = detector.process(rgb_image)
        detections = getattr(results, "detections", None)
        return 0 if not detections else len(detections)

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
        """Instantiate the MediaPipe FaceDetection solution."""
        try:
            import mediapipe as mp

            face_detection = mp.solutions.face_detection
        except (ImportError, AttributeError) as exc:
            raise FaceDetectionError(
                "MediaPipe Face Detection backend is unavailable. Install a full "
                "'mediapipe' build (pip install mediapipe) that provides "
                "mediapipe.solutions.face_detection."
            ) from exc
        return face_detection.FaceDetection(
            model_selection=self.model_selection,
            min_detection_confidence=self.min_detection_confidence,
        )
