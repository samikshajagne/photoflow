"""
Blur detection engine for PhotoFlow (Milestone 2).

This module decides whether a single image is sharp or blurry using the
classic *Variance of the Laplacian* method: a Laplacian kernel highlights
edges, and a sharp image has many strong edges (high response variance)
while a blurry one has few (low variance). The numeric variance is the
``blur_score``; an image is flagged ``is_blurry`` when its score falls below
a configurable threshold.

The public entry point is :class:`BlurDetector`, which can be constructed
directly or from a validated :class:`~utils.config.AppConfig` via
:meth:`BlurDetector.from_config` (reading ``thresholds.blur_score_min``).
Its :meth:`~BlurDetector.detect` method returns a :class:`BlurResult`
carrying both the raw score and the boolean verdict.

Scope: this module performs *only* blur detection. Face detection, quality
scoring, folder organization, UI, and persistence live elsewhere.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Union

import cv2
import numpy as np

from utils.logger import get_logger

if TYPE_CHECKING:
    from utils.config import AppConfig

logger = get_logger(__name__)

# File extensions this engine knows how to read. Compared case-insensitively
# against a file's suffix. Kept as a module default so the detector is usable
# without a full AppConfig (e.g. in tests or ad-hoc scripts).
DEFAULT_SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
)

# Default minimum Variance-of-Laplacian score for an image to count as
# "sharp". Mirrors the ``thresholds.blur_score_min`` default in
# data/default_config.yaml so behavior is consistent whether or not a config
# is supplied. Images scoring below this are flagged blurry.
DEFAULT_BLUR_SCORE_MIN: float = 100.0


class BlurDetectionError(Exception):
    """Raised when blur detection cannot proceed (bad path, unreadable image)."""


@dataclasses.dataclass(frozen=True)
class BlurResult:
    """
    Outcome of analyzing one image for blur.

    Attributes:
        path: The analyzed image's path, as a string.
        blur_score: Variance of the Laplacian. Higher means sharper.
        is_blurry: ``True`` when ``blur_score`` is below the detector's
            ``blur_score_min`` threshold.
    """

    path: str
    blur_score: float
    is_blurry: bool


class BlurDetector:
    """
    Detects whether an image is blurry via Variance of the Laplacian.

    The detector is stateless between calls, so a single instance can analyze
    many images.

    Args:
        blur_score_min: Minimum Variance-of-Laplacian score for an image to
            be considered sharp. Images scoring strictly below this are
            flagged blurry. Must be >= 0.
        supported_extensions: File extensions to accept, each starting with a
            dot (e.g. ``".jpg"``). Matched case-insensitively. Must be
            non-empty.

    Raises:
        BlurDetectionError: if any argument is out of range.
    """

    def __init__(
        self,
        blur_score_min: float = DEFAULT_BLUR_SCORE_MIN,
        supported_extensions: tuple[str, ...] = DEFAULT_SUPPORTED_EXTENSIONS,
    ) -> None:
        if blur_score_min < 0:
            raise BlurDetectionError(
                f"blur_score_min must be >= 0, got {blur_score_min}"
            )
        if not supported_extensions:
            raise BlurDetectionError("supported_extensions must not be empty")
        for ext in supported_extensions:
            if not ext.startswith("."):
                raise BlurDetectionError(
                    f"supported_extensions entries must start with '.', got '{ext}'"
                )

        self.blur_score_min = float(blur_score_min)
        # Normalize to lowercase once so suffix comparison is cheap and
        # case-insensitive (e.g. a ".JPG" file matches ".jpg").
        self._extensions = frozenset(ext.lower() for ext in supported_extensions)

    @classmethod
    def from_config(cls, config: "AppConfig") -> "BlurDetector":
        """
        Build a detector from a validated :class:`~utils.config.AppConfig`.

        Reads ``thresholds.blur_score_min`` and ``io.supported_extensions`` so
        runtime behavior is driven by the shipped/merged configuration rather
        than hard-coded defaults.
        """
        return cls(
            blur_score_min=config.thresholds.blur_score_min,
            supported_extensions=config.io.supported_extensions,
        )

    def detect(self, image_path: Union[str, Path]) -> BlurResult:
        """
        Analyze a single image and report its blur score and verdict.

        Args:
            image_path: Path to the image file to analyze.

        Returns:
            A :class:`BlurResult` with the raw ``blur_score`` and the
            ``is_blurry`` flag (``blur_score < blur_score_min``).

        Raises:
            BlurDetectionError: if the path is missing, is not a file, has an
                unsupported extension, or cannot be decoded as an image.
        """
        path = Path(image_path)
        if not path.exists():
            raise BlurDetectionError(f"Image does not exist: {path}")
        if not path.is_file():
            raise BlurDetectionError(f"Path is not a file: {path}")
        if path.suffix.lower() not in self._extensions:
            raise BlurDetectionError(
                f"Unsupported file extension '{path.suffix}' for '{path}'. "
                f"Supported: {sorted(self._extensions)}"
            )

        gray = self._load_grayscale(path)
        blur_score = self._variance_of_laplacian(gray)
        is_blurry = blur_score < self.blur_score_min

        logger.info(
            "Blur analysis '%s': score=%.2f threshold=%.2f -> %s",
            path,
            blur_score,
            self.blur_score_min,
            "blurry" if is_blurry else "sharp",
        )
        return BlurResult(path=str(path), blur_score=blur_score, is_blurry=is_blurry)

    def _load_grayscale(self, path: Path) -> np.ndarray:
        """
        Load an image as a single-channel grayscale array.

        Reads raw bytes and decodes via ``cv2.imdecode`` rather than
        ``cv2.imread`` so paths with non-ASCII characters are handled
        reliably across platforms. Both decode failures (corrupt/unsupported
        data) and read errors raise :class:`BlurDetectionError`.
        """
        try:
            raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        except OSError as exc:
            raise BlurDetectionError(f"Failed to read image '{path}': {exc}") from exc

        if raw.size == 0:
            raise BlurDetectionError(f"Image file is empty: {path}")

        image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise BlurDetectionError(
                f"Could not decode image (corrupt or unsupported): {path}"
            )
        return image

    @staticmethod
    def _variance_of_laplacian(gray: np.ndarray) -> float:
        """
        Compute the Variance of the Laplacian of a grayscale image.

        A 64-bit float Laplacian avoids the clipping that an 8-bit output
        would introduce on strong edges. The variance of that response is the
        blur score: higher means sharper.
        """
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        return float(laplacian.var())
