"""
Duplicate detection engine for PhotoFlow (Milestone 2).

This module finds visually identical and visually similar images inside a
folder tree using *perceptual hashing*. Unlike a byte-level checksum, a
perceptual hash (here, ``imagehash.phash``) produces similar hashes for
images that look alike, so re-encoded, resized, or lightly edited copies
still cluster together.

Two images are considered related when the Hamming distance between their
perceptual hashes is at most a configurable threshold:

- distance ``0``                       -> exact (visually identical) duplicate
- ``0 < distance <= threshold``        -> near duplicate

Images whose distance exceeds the threshold are treated as distinct.

The public entry point is :class:`DuplicateDetector`, which can be built
directly or from a validated :class:`~utils.config.AppConfig` via
:meth:`DuplicateDetector.from_config`. Its :meth:`~DuplicateDetector.detect`
method returns a plain, JSON-serializable dictionary::

    {
        "groups": [
            {
                "representative": "path/to/keep.jpg",
                "duplicates": ["path/to/copy1.jpg", "path/to/copy2.png"],
            },
            ...
        ]
    }

Only groups containing at least one duplicate are returned; unique images
are omitted. This module deliberately implements *only* duplicate
detection — no blur detection, face detection, quality scoring, UI, or
persistence (those belong to other Milestone 2+ modules).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional, Union

import imagehash
from PIL import Image, UnidentifiedImageError

from utils.logger import get_logger

if TYPE_CHECKING:
    from utils.config import AppConfig

logger = get_logger(__name__)

# File extensions this engine knows how to read. Compared case-insensitively
# against each file's suffix. Kept as a module default so the detector is
# usable without a full AppConfig (e.g. in tests or ad-hoc scripts).
DEFAULT_SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
)

# Default maximum Hamming distance to count two hashes as related. Mirrors
# the ``thresholds.duplicate_hash_distance_max`` default in
# data/default_config.yaml so behavior is consistent whether or not a config
# is supplied.
DEFAULT_HASH_DISTANCE_MAX: int = 5

# Side length of the perceptual hash grid. A hash_size of 8 yields a 64-bit
# hash, the imagehash default and a sensible balance of speed vs. precision.
DEFAULT_HASH_SIZE: int = 8


class DuplicateDetectionError(Exception):
    """Raised when duplicate detection cannot proceed (e.g. bad input folder)."""


@dataclasses.dataclass(frozen=True)
class _HashedImage:
    """An image file paired with its computed perceptual hash."""

    path: Path
    image_hash: imagehash.ImageHash


@dataclasses.dataclass
class _Cluster:
    """A mutable group of images sharing a representative perceptual hash."""

    representative_hash: imagehash.ImageHash
    paths: list[Path]


class DuplicateDetector:
    """
    Detects exact and near-duplicate images within a folder tree.

    The detector is stateless between calls: each :meth:`detect` invocation
    scans, hashes, and clusters independently, so a single instance can be
    reused across folders.

    Args:
        hash_distance_max: Maximum Hamming distance between two perceptual
            hashes for the images to be grouped as duplicates. ``0`` matches
            only visually identical images; larger values also catch near
            duplicates. Must be >= 0.
        supported_extensions: File extensions to scan, each starting with a
            dot (e.g. ``".jpg"``). Matched case-insensitively. Must be
            non-empty.
        hash_size: Side length of the perceptual hash grid passed to
            ``imagehash.phash``. Must be >= 2.

    Raises:
        DuplicateDetectionError: if any argument is out of range.
    """

    def __init__(
        self,
        hash_distance_max: int = DEFAULT_HASH_DISTANCE_MAX,
        supported_extensions: tuple[str, ...] = DEFAULT_SUPPORTED_EXTENSIONS,
        hash_size: int = DEFAULT_HASH_SIZE,
    ) -> None:
        if hash_distance_max < 0:
            raise DuplicateDetectionError(
                f"hash_distance_max must be >= 0, got {hash_distance_max}"
            )
        if not supported_extensions:
            raise DuplicateDetectionError("supported_extensions must not be empty")
        for ext in supported_extensions:
            if not ext.startswith("."):
                raise DuplicateDetectionError(
                    f"supported_extensions entries must start with '.', got '{ext}'"
                )
        if hash_size < 2:
            raise DuplicateDetectionError(f"hash_size must be >= 2, got {hash_size}")

        self.hash_distance_max = hash_distance_max
        # Normalize to lowercase once so suffix comparison is cheap and
        # case-insensitive (e.g. a ".JPG" file matches ".jpg").
        self._extensions = frozenset(ext.lower() for ext in supported_extensions)
        self.hash_size = hash_size

    @classmethod
    def from_config(cls, config: "AppConfig") -> "DuplicateDetector":
        """
        Build a detector from a validated :class:`~utils.config.AppConfig`.

        Reads ``thresholds.duplicate_hash_distance_max`` and
        ``io.supported_extensions`` so runtime behavior is driven by the
        shipped/merged configuration rather than hard-coded defaults.
        """
        return cls(
            hash_distance_max=config.thresholds.duplicate_hash_distance_max,
            supported_extensions=config.io.supported_extensions,
        )

    def detect(
        self,
        folder: Union[str, Path],
        image_paths: Optional[Iterable[Union[str, Path]]] = None,
    ) -> dict[str, list[dict[str, object]]]:
        """
        Group duplicate/near-duplicate images.

        Args:
            folder: Root directory the images came from (used for validation
                and logging).
            image_paths: Optional pre-enumerated image files to consider. When
                provided (e.g. the pipeline's single authoritative scan), the
                folder is **not** re-walked, avoiding a second directory scan
                and keeping the file set identical to the rest of the pipeline.
                When ``None``, ``folder`` is scanned recursively as before.

        Returns:
            A dictionary of the form::

                {"groups": [{"representative": <path>, "duplicates": [<path>, ...]}, ...]}

            Paths are strings. Only groups with at least one duplicate are
            included; unique images are omitted. Returns ``{"groups": []}``
            when no duplicates are found.

        Raises:
            DuplicateDetectionError: if ``folder`` does not exist or is not a
                directory.
        """
        root = Path(folder)
        if not root.exists():
            raise DuplicateDetectionError(f"Folder does not exist: {root}")
        if not root.is_dir():
            raise DuplicateDetectionError(f"Path is not a directory: {root}")

        if image_paths is not None:
            paths = [Path(p) for p in image_paths]
            logger.info("Using %d pre-scanned image file(s) from '%s'.", len(paths), root)
        else:
            logger.info("Scanning '%s' for duplicate images.", root)
            paths = self._scan_folder(root)
            logger.info("Found %d candidate image file(s).", len(paths))
        image_paths = paths

        hashed = self._hash_images(image_paths)
        logger.info("Successfully hashed %d image(s).", len(hashed))

        clusters = self._cluster(hashed)
        groups = self._build_groups(clusters)
        logger.info(
            "Detected %d duplicate group(s) (threshold=%d).",
            len(groups),
            self.hash_distance_max,
        )
        return {"groups": groups}

    def _scan_folder(self, root: Path) -> list[Path]:
        """
        Recursively collect supported image files under ``root``.

        Results are sorted for deterministic, reproducible output. Individual
        unreadable directory entries are logged and skipped rather than
        aborting the whole scan.
        """
        matches: list[Path] = []
        try:
            candidates = sorted(root.rglob("*"))
        except OSError as exc:
            raise DuplicateDetectionError(
                f"Failed to scan folder '{root}': {exc}"
            ) from exc

        for path in candidates:
            try:
                if path.is_file() and path.suffix.lower() in self._extensions:
                    matches.append(path)
            except OSError as exc:
                # A broken symlink or permission issue on a single entry
                # shouldn't sink the entire scan.
                logger.warning("Skipping unreadable path '%s': %s", path, exc)
        return matches

    def _hash_images(self, paths: list[Path]) -> list[_HashedImage]:
        """Compute the perceptual hash for each path, skipping failures."""
        hashed: list[_HashedImage] = []
        for path in paths:
            image_hash = self._hash_image(path)
            if image_hash is not None:
                hashed.append(_HashedImage(path=path, image_hash=image_hash))
        return hashed

    def _hash_image(self, path: Path) -> Optional[imagehash.ImageHash]:
        """
        Compute one image's perceptual hash.

        Returns ``None`` (and logs a warning) for files that cannot be opened
        or decoded as images, so a single corrupt file never aborts a run.
        """
        try:
            with Image.open(path) as img:
                # Force a load inside the context manager so truncated files
                # raise here (where we can catch them) rather than later.
                return imagehash.phash(img, hash_size=self.hash_size)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            logger.warning("Could not hash image '%s': %s", path, exc)
            return None

    def _cluster(self, hashed: list[_HashedImage]) -> list[_Cluster]:
        """
        Greedily group hashed images by Hamming distance.

        Each image joins the first existing cluster whose representative hash
        is within ``hash_distance_max``; otherwise it seeds a new cluster.
        Input is assumed sorted by path (from :meth:`_scan_folder`), making
        the representative of each cluster the lexicographically-first path
        and the whole result deterministic.
        """
        clusters: list[_Cluster] = []
        for item in hashed:
            placed = False
            for cluster in clusters:
                # imagehash defines subtraction as Hamming distance.
                if (item.image_hash - cluster.representative_hash) <= self.hash_distance_max:
                    cluster.paths.append(item.path)
                    placed = True
                    break
            if not placed:
                clusters.append(
                    _Cluster(representative_hash=item.image_hash, paths=[item.path])
                )
        return clusters

    @staticmethod
    def _build_groups(clusters: list[_Cluster]) -> list[dict[str, object]]:
        """
        Convert clusters into the public result structure.

        Singleton clusters (unique images) are dropped. For each remaining
        cluster the first path is the representative and the rest are its
        duplicates. Paths are emitted as strings.
        """
        groups: list[dict[str, object]] = []
        for cluster in clusters:
            if len(cluster.paths) < 2:
                continue
            representative, *duplicates = cluster.paths
            groups.append(
                {
                    "representative": str(representative),
                    "duplicates": [str(p) for p in duplicates],
                }
            )
        return groups
