"""
Face embedding for PhotoFlow album identity (Phase 1).

Detection answers "is there a face, and where?"; *embedding* answers "is this
the same person as that one?". This module crops each detected face region and
turns it into a fixed-length, L2-normalized vector so that faces of the same
person land close together (small cosine distance) and different people land
far apart. Those vectors are what :mod:`core.person_cluster` groups into people,
which in turn powers the album's bride/groom/family sheets.

PhotoFlow stays local-only: embeddings are computed on-device and used solely to
group the photos of *this* shoot. They are not identity lookups against any
external database.

The heavyweight recognition model is kept behind an **injectable backend** -- a
callable mapping a list of face crops (RGB arrays) to a list of vectors. This
mirrors the rest of ``core``: the wrapper's own responsibilities (path
validation, cropping, normalization, error handling) are testable without the
model, and a concrete model (e.g. an ArcFace/InsightFace embedder) can be wired
in without touching this logic. When no backend is configured, embedding fails
fast with a clear error rather than silently returning nothing.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Sequence, Union

import cv2
import numpy as np

from utils.logger import get_logger

if TYPE_CHECKING:
    from utils.config import AppConfig

logger = get_logger(__name__)

PathLike = Union[str, Path]
FaceBox = tuple[float, float, float, float]

# A backend maps a list of RGB face crops to a list of raw embedding vectors
# (one per crop, same order). Normalization is handled by FaceEmbedder.
EmbedBackend = Callable[[list[np.ndarray]], Sequence[np.ndarray]]

DEFAULT_SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
)

# Smallest face crop (in pixels, per side) worth embedding. Tiny background
# faces produce unreliable vectors, so they are skipped.
DEFAULT_MIN_FACE_PX: int = 24


class FaceEmbeddingError(Exception):
    """Raised when face embedding cannot proceed (bad input, missing backend)."""


@dataclasses.dataclass(frozen=True)
class FaceEmbedding:
    """
    One embedded face.

    Attributes:
        image_path: Source image path, as a string.
        face_index: Index of the face within that image's detected regions.
        vector: L2-normalized embedding (unit length), as a float32 array.
    """

    image_path: str
    face_index: int
    vector: np.ndarray


class FaceEmbedder:
    """
    Turns detected face regions into L2-normalized embedding vectors.

    Args:
        embed_backend: Callable mapping a list of RGB face crops to a list of
            raw vectors. Injected so the heavy recognition model is decoupled
            from (and testable independently of) this wrapper. When ``None``,
            :meth:`embed` raises :class:`FaceEmbeddingError`.
        supported_extensions: Accepted file extensions, each starting with a
            dot. Matched case-insensitively. Must be non-empty.
        min_face_px: Minimum face crop side length (pixels) to embed; smaller
            faces are skipped. Must be >= 1.

    Raises:
        FaceEmbeddingError: if any argument is out of range.
    """

    def __init__(
        self,
        embed_backend: Optional[EmbedBackend] = None,
        supported_extensions: tuple[str, ...] = DEFAULT_SUPPORTED_EXTENSIONS,
        min_face_px: int = DEFAULT_MIN_FACE_PX,
    ) -> None:
        if not supported_extensions:
            raise FaceEmbeddingError("supported_extensions must not be empty")
        for ext in supported_extensions:
            if not ext.startswith("."):
                raise FaceEmbeddingError(
                    f"supported_extensions entries must start with '.', got '{ext}'"
                )
        if min_face_px < 1:
            raise FaceEmbeddingError(f"min_face_px must be >= 1, got {min_face_px}")

        self._embed_backend = embed_backend
        self._extensions = frozenset(ext.lower() for ext in supported_extensions)
        self.min_face_px = int(min_face_px)

    @classmethod
    def from_config(
        cls, config: "AppConfig", embed_backend: Optional[EmbedBackend] = None
    ) -> "FaceEmbedder":
        """Build from a validated :class:`~utils.config.AppConfig`.

        Reads ``io.supported_extensions``. The recognition backend is supplied
        separately (it depends on a model the caller chooses/installs)."""
        return cls(
            embed_backend=embed_backend,
            supported_extensions=config.io.supported_extensions,
        )

    def embed(
        self, image_path: PathLike, regions: Sequence[FaceBox]
    ) -> list[FaceEmbedding]:
        """
        Embed each face region of one image.

        Args:
            image_path: Path to the image (already analyzed for faces).
            regions: Relative ``(xmin, ymin, width, height)`` boxes in
                ``[0, 1]`` from the face stage.

        Returns:
            A :class:`FaceEmbedding` per region large enough to embed, in the
            same order as ``regions`` (skipped tiny faces leave gaps in
            ``face_index`` but never a wrong index).

        Raises:
            FaceEmbeddingError: if the path is missing/unsupported/undecodable,
                or no embedding backend is configured.
        """
        if not regions:
            return []

        path = Path(image_path)
        if not path.exists():
            raise FaceEmbeddingError(f"Image does not exist: {path}")
        if not path.is_file():
            raise FaceEmbeddingError(f"Path is not a file: {path}")
        if path.suffix.lower() not in self._extensions:
            raise FaceEmbeddingError(
                f"Unsupported file extension '{path.suffix}' for '{path}'."
            )

        rgb = self._load_rgb(path)
        crops: list[np.ndarray] = []
        indices: list[int] = []
        for index, region in enumerate(regions):
            crop = self._crop(rgb, region)
            if crop is None:
                continue
            crops.append(crop)
            indices.append(index)

        if not crops:
            return []

        vectors = self._embed_crops(crops)
        if len(vectors) != len(crops):
            raise FaceEmbeddingError(
                f"Embedding backend returned {len(vectors)} vectors for "
                f"{len(crops)} crops."
            )

        results: list[FaceEmbedding] = []
        for index, vector in zip(indices, vectors):
            normalized = self._normalize(vector)
            # A zero-norm (degenerate) embedding has cosine distance ~1 to
            # everything and would seed a spurious one-face "person" cluster;
            # drop it rather than emit it.
            if not np.any(normalized):
                logger.debug(
                    "Dropping degenerate (zero) embedding for face %d in '%s'.",
                    index,
                    path,
                )
                continue
            results.append(
                FaceEmbedding(
                    image_path=str(path),
                    face_index=index,
                    vector=normalized,
                )
            )
        logger.info("Embedded %d face(s) for '%s'.", len(results), path)
        return results

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #
    def _crop(self, rgb: np.ndarray, region: FaceBox) -> Optional[np.ndarray]:
        """Crop a relative face box to pixels; ``None`` if it's too small."""
        height, width = rgb.shape[:2]
        rx, ry, rw, rh = region
        x0 = max(0, int(round(rx * width)))
        y0 = max(0, int(round(ry * height)))
        x1 = min(width, int(round((rx + rw) * width)))
        y1 = min(height, int(round((ry + rh) * height)))
        if (x1 - x0) < self.min_face_px or (y1 - y0) < self.min_face_px:
            return None
        return rgb[y0:y1, x0:x1]

    def _embed_crops(self, crops: list[np.ndarray]) -> Sequence[np.ndarray]:
        """
        Run the recognition backend on face crops.

        Isolated as the single seam over the heavy model: tests inject a fake
        backend, and a real model is wired via the ``embed_backend`` argument.
        """
        if self._embed_backend is None:
            raise FaceEmbeddingError(
                "No face embedding backend is configured. Provide an "
                "'embed_backend' callable (e.g. an ArcFace/InsightFace "
                "embedder) mapping face crops to vectors."
            )
        return self._embed_backend(crops)

    @staticmethod
    def _normalize(vector: np.ndarray) -> np.ndarray:
        """Return ``vector`` as a unit-length float32 array."""
        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return vec
        return vec / norm

    def _load_rgb(self, path: Path) -> np.ndarray:
        """Load an image as an RGB array (bytes -> imdecode for non-ASCII paths)."""
        try:
            raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        except OSError as exc:
            raise FaceEmbeddingError(f"Failed to read image '{path}': {exc}") from exc
        if raw.size == 0:
            raise FaceEmbeddingError(f"Image file is empty: {path}")
        bgr = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if bgr is None:
            raise FaceEmbeddingError(
                f"Could not decode image (corrupt or unsupported): {path}"
            )
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
