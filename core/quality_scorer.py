"""
Quality scoring for PhotoFlow (Milestone 2).

Assigns each image a single quality score in the range 0-100 by combining
four signals:

- **Sharpness**, from the blur stage's Variance-of-Laplacian ``blur_score``.
- **Brightness**, the mean grayscale intensity (well-exposed mid-tones score
  highest; pure black or blown-out white score lowest).
- **Contrast**, the standard deviation of grayscale intensity (flat, washed-
  out images score low; punchy images score high, up to a reference point).
- **Faces**, from the face stage: photos containing people score higher than
  those without, all else equal.

Each signal is mapped to a sub-score in ``[0, 1]`` and combined using the
weights from ``scoring_weights`` in the config. Brightness and contrast are
the two halves of the **exposure** signal, sharing ``exposure_weight``;
sharpness uses ``blur_weight``; face presence uses ``face_weight``. With the
shipped default weights (0.5 + 0.2 + 0.3) the active weights sum to 1.0; the
implementation renormalizes by their sum so any weighting keeps the score on
a full 0-100 scale.

The public entry point is :class:`QualityScorer`:

- :meth:`~QualityScorer.combine` is the pure function over the numeric inputs
  (blur score, brightness, contrast, faces-detected flag).
- :meth:`~QualityScorer.score` loads an image, derives brightness/contrast,
  and returns a :class:`QualityResult`.

Scope: quality scoring only -- face *detection* itself lives in
``core.face_detector``, and duplicate detection logic is untouched.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence, Union

import cv2
import numpy as np

from utils.logger import get_logger

if TYPE_CHECKING:
    from utils.config import AppConfig

logger = get_logger(__name__)

PathLike = Union[str, Path]

# Reference values used to normalize raw signals into [0, 1] sub-scores.
# Mirrors thresholds.blur_score_min in data/default_config.yaml.
DEFAULT_BLUR_SCORE_MIN: float = 100.0
# Grayscale intensity (0-255); the mid-point is the ideal exposure.
_BRIGHTNESS_IDEAL: float = 127.5
# Standard deviation (0-~128) at/above which contrast is considered full.
DEFAULT_CONTRAST_REFERENCE: float = 64.0

# Default scoring weights, mirroring scoring_weights in the config.
DEFAULT_BLUR_WEIGHT: float = 0.5
DEFAULT_FACE_WEIGHT: float = 0.3
DEFAULT_EXPOSURE_WEIGHT: float = 0.2

_MAX_SCORE: float = 100.0

# --- Usability gate ---------------------------------------------------------
# The usability gate is intentionally separate from the quality score. The
# quality score ranks photos against each other; the usability gate is a
# conservative, absolute verdict on whether a photo is *clearly unusable*
# (severe blur on the subject, or a near-black / blown-out frame). Only
# unusable photos are routed to the Blurry bin, so it stays small and
# trustworthy. These thresholds are deliberately permissive.
DEFAULT_USABLE_SHARPNESS_MIN: float = 15.0   # subject Laplacian-variance floor
DEFAULT_USABLE_BRIGHTNESS_MIN: float = 12.0  # below = effectively black
DEFAULT_USABLE_BRIGHTNESS_MAX: float = 243.0  # above = blown out


class QualityScoringError(Exception):
    """Raised when an image cannot be scored (unreadable/corrupt, bad config)."""


@dataclasses.dataclass(frozen=True)
class QualityResult:
    """
    Quality score for a single image.

    Attributes:
        image_path: The analyzed image's path, as a string.
        quality_score: Overall quality in ``[0, 100]`` (higher is better),
            rounded to two decimals.
        blur_score: The raw Variance-of-Laplacian input used.
        brightness: Mean grayscale intensity (0-255).
        contrast: Standard deviation of grayscale intensity.
        faces_detected: Whether the image was found to contain a face.
        face_count: Number of faces detected.
        sharpness: The effective sharpness used for scoring -- subject-aware
            (measured on the face region) when a face was located, otherwise
            the whole-frame ``blur_score``.
        usable: ``False`` only when the photo is *clearly* unusable (severe
            blur on the subject, or a near-black / blown-out frame). This is a
            conservative absolute verdict, independent of ``quality_score``,
            and is what routes a photo to the Blurry bin.
    """

    image_path: str
    quality_score: float
    blur_score: float
    brightness: float
    contrast: float
    faces_detected: bool
    face_count: int
    sharpness: float = 0.0
    usable: bool = True


class QualityScorer:
    """
    Combines sharpness, exposure, and face presence into a 0-100 quality score.

    Args:
        blur_weight: Weight applied to the sharpness sub-score.
        exposure_weight: Weight applied to the exposure sub-score (the mean of
            the brightness and contrast sub-scores).
        face_weight: Weight applied to the face-presence sub-score (1.0 when a
            face is present, else 0.0). Must be >= 0.
        blur_score_min: Reference blur score; an image scoring this much earns
            half of the sharpness sub-score (smooth saturating curve). Must be
            > 0.
        contrast_reference: Std-dev at/above which contrast is full marks.
            Must be > 0.

    Raises:
        QualityScoringError: if any weight is negative, the active weights
            (blur + exposure + face) sum to zero, or a reference value is
            non-positive.
    """

    def __init__(
        self,
        blur_weight: float = DEFAULT_BLUR_WEIGHT,
        exposure_weight: float = DEFAULT_EXPOSURE_WEIGHT,
        face_weight: float = DEFAULT_FACE_WEIGHT,
        blur_score_min: float = DEFAULT_BLUR_SCORE_MIN,
        contrast_reference: float = DEFAULT_CONTRAST_REFERENCE,
        usable_sharpness_min: float = DEFAULT_USABLE_SHARPNESS_MIN,
        usable_brightness_min: float = DEFAULT_USABLE_BRIGHTNESS_MIN,
        usable_brightness_max: float = DEFAULT_USABLE_BRIGHTNESS_MAX,
    ) -> None:
        for name, weight in (
            ("blur_weight", blur_weight),
            ("exposure_weight", exposure_weight),
            ("face_weight", face_weight),
        ):
            if weight < 0:
                raise QualityScoringError(f"{name} must be >= 0, got {weight}")

        active_weight = blur_weight + exposure_weight + face_weight
        if active_weight <= 0:
            raise QualityScoringError(
                "blur_weight + exposure_weight + face_weight must be > 0"
            )
        if blur_score_min <= 0:
            raise QualityScoringError(f"blur_score_min must be > 0, got {blur_score_min}")
        if contrast_reference <= 0:
            raise QualityScoringError(
                f"contrast_reference must be > 0, got {contrast_reference}"
            )

        if usable_sharpness_min < 0:
            raise QualityScoringError(
                f"usable_sharpness_min must be >= 0, got {usable_sharpness_min}"
            )
        if not 0.0 <= usable_brightness_min < usable_brightness_max <= 255.0:
            raise QualityScoringError(
                "usable_brightness bounds must satisfy "
                f"0 <= min < max <= 255, got min={usable_brightness_min}, "
                f"max={usable_brightness_max}"
            )

        self.blur_weight = float(blur_weight)
        self.exposure_weight = float(exposure_weight)
        self.face_weight = float(face_weight)
        self.blur_score_min = float(blur_score_min)
        self.contrast_reference = float(contrast_reference)
        self.usable_sharpness_min = float(usable_sharpness_min)
        self.usable_brightness_min = float(usable_brightness_min)
        self.usable_brightness_max = float(usable_brightness_max)
        self._active_weight = float(active_weight)

    @classmethod
    def from_config(cls, config: "AppConfig") -> "QualityScorer":
        """
        Build a scorer from a validated :class:`~utils.config.AppConfig`.

        Reads ``scoring_weights`` (blur/face/exposure) and
        ``thresholds.blur_score_min``.
        """
        return cls(
            blur_weight=config.scoring_weights.blur_weight,
            exposure_weight=config.scoring_weights.exposure_weight,
            face_weight=config.scoring_weights.face_weight,
            blur_score_min=config.thresholds.blur_score_min,
        )

    def score(
        self,
        image_path: PathLike,
        blur_score: float,
        faces_detected: bool = False,
        face_count: int = 0,
        face_regions: Sequence[tuple[float, float, float, float]] = (),
    ) -> QualityResult:
        """
        Score one image given its blur score and face information.

        Brightness and contrast are derived from the image itself; the blur
        score and face info are supplied by the caller (the blur and face
        stages already computed them), avoiding redundant work.

        Sharpness is **subject-aware**: when ``face_regions`` are supplied, the
        Variance-of-Laplacian is measured on the face region(s) rather than the
        whole frame, so a tack-sharp subject against a soft (bokeh) background
        is not penalized. With no face regions, the whole-frame ``blur_score``
        is used.

        Args:
            image_path: Path to the image to score.
            blur_score: Whole-frame Variance-of-Laplacian from the blur stage.
            faces_detected: Whether a face was detected in the image.
            face_count: Number of faces detected (informational).
            face_regions: Relative ``(xmin, ymin, width, height)`` face boxes
                in ``[0, 1]`` from the face stage. When present, sharpness is
                measured on these regions.

        Returns:
            A :class:`QualityResult`, including the effective ``sharpness`` used
            and the conservative ``usable`` verdict.

        Raises:
            QualityScoringError: if the image is missing or cannot be decoded.
        """
        gray = self._load_gray(image_path)
        brightness = float(gray.mean())
        contrast = float(gray.std())

        sharpness = self._effective_sharpness(gray, blur_score, face_regions)
        quality = self.combine(sharpness, brightness, contrast, faces_detected)
        usable = self._is_usable(sharpness, brightness)

        logger.info(
            "Quality '%s': score=%.2f usable=%s "
            "(sharpness=%.1f blur=%.1f brightness=%.1f contrast=%.1f faces=%s)",
            image_path,
            quality,
            usable,
            sharpness,
            blur_score,
            brightness,
            contrast,
            faces_detected,
        )
        return QualityResult(
            image_path=str(image_path),
            quality_score=quality,
            blur_score=float(blur_score),
            brightness=brightness,
            contrast=contrast,
            faces_detected=bool(faces_detected),
            face_count=int(face_count),
            sharpness=float(sharpness),
            usable=bool(usable),
        )

    def _is_usable(self, sharpness: float, brightness: float) -> bool:
        """
        Conservative, absolute "is this clearly unusable?" verdict.

        Independent of the quality ranking: a photo is usable unless its
        subject is severely blurred or the frame is near-black / blown out.
        """
        if sharpness < self.usable_sharpness_min:
            return False
        if not (self.usable_brightness_min <= brightness <= self.usable_brightness_max):
            return False
        return True

    def _effective_sharpness(
        self,
        gray: np.ndarray,
        blur_score: float,
        face_regions: Sequence[tuple[float, float, float, float]],
    ) -> float:
        """
        Subject-aware sharpness: max face-region Variance-of-Laplacian when
        faces are present and yield a usable crop, else the whole-frame
        ``blur_score`` supplied by the blur stage.
        """
        if not face_regions:
            return float(blur_score)

        height, width = gray.shape[:2]
        best: Optional[float] = None
        for (rx, ry, rw, rh) in face_regions:
            x0 = max(0, int(round(rx * width)))
            y0 = max(0, int(round(ry * height)))
            x1 = min(width, int(round((rx + rw) * width)))
            y1 = min(height, int(round((ry + rh) * height)))
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue  # crop too small to measure
            crop = gray[y0:y1, x0:x1]
            region_var = float(cv2.Laplacian(crop, cv2.CV_64F).var())
            if best is None or region_var > best:
                best = region_var
        # No usable crop (e.g. degenerate boxes) -> fall back to whole frame.
        return float(blur_score) if best is None else best

    def combine(
        self,
        blur_score: float,
        brightness: float,
        contrast: float,
        faces_detected: bool = False,
    ) -> float:
        """
        Combine the signals into a 0-100 quality score.

        Args:
            blur_score: Variance of the Laplacian (>= 0; higher is sharper).
            brightness: Mean grayscale intensity in ``[0, 255]``.
            contrast: Standard deviation of grayscale intensity (>= 0).
            faces_detected: Whether the image contains a face.

        Returns:
            Quality in ``[0, 100]``, rounded to two decimals. With equal blur,
            brightness, and contrast, an image with a face scores strictly
            higher than one without (whenever ``face_weight`` > 0).
        """
        blur_sub = self._blur_subscore(blur_score)
        exposure_sub = 0.5 * (
            self._brightness_subscore(brightness) + self._contrast_subscore(contrast)
        )
        face_sub = 1.0 if faces_detected else 0.0

        weighted = (
            self.blur_weight * blur_sub
            + self.exposure_weight * exposure_sub
            + self.face_weight * face_sub
        )
        fraction = weighted / self._active_weight
        score = _MAX_SCORE * _clamp(fraction, 0.0, 1.0)
        return round(score, 2)

    # ----------------------------------------------------------------- #
    # Sub-scores (each maps a raw signal into [0, 1])
    # ----------------------------------------------------------------- #
    def _blur_subscore(self, blur_score: float) -> float:
        """Smooth saturating curve: 0 at 0, 0.5 at ``blur_score_min``, ->1 high."""
        if blur_score <= 0:
            return 0.0
        return blur_score / (blur_score + self.blur_score_min)

    @staticmethod
    def _brightness_subscore(brightness: float) -> float:
        """Triangular: 1.0 at mid-gray, 0.0 at pure black/white."""
        distance = abs(brightness - _BRIGHTNESS_IDEAL)
        return _clamp(1.0 - distance / _BRIGHTNESS_IDEAL, 0.0, 1.0)

    def _contrast_subscore(self, contrast: float) -> float:
        """Linear ramp to full marks at ``contrast_reference`` and above."""
        return _clamp(contrast / self.contrast_reference, 0.0, 1.0)

    # ----------------------------------------------------------------- #
    # Image measurements
    # ----------------------------------------------------------------- #
    def _load_gray(self, image_path: PathLike) -> np.ndarray:
        """
        Decode an image to a single-channel grayscale array.

        Reads raw bytes and decodes via ``cv2.imdecode`` so non-ASCII paths
        work across platforms. Brightness, contrast, and subject-aware
        sharpness are all derived from this one decode.
        """
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            raise QualityScoringError(f"Image does not exist: {path}")

        try:
            raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        except OSError as exc:
            raise QualityScoringError(f"Failed to read image '{path}': {exc}") from exc
        if raw.size == 0:
            raise QualityScoringError(f"Image file is empty: {path}")

        gray = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise QualityScoringError(
                f"Could not decode image (corrupt or unsupported): {path}"
            )
        return gray


def _clamp(value: float, low: float, high: float) -> float:
    """Constrain ``value`` to the inclusive range ``[low, high]``."""
    return max(low, min(high, value))
