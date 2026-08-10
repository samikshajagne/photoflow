"""
Person clustering for PhotoFlow album identity (Phase 1).

Groups embedded faces (from :mod:`core.face_embedder`) into *people*: each
cluster is one person appearing across the shoot. This is what lets the album
build bride-only / groom-only / family sheets -- once the photographer labels a
cluster ("this is the bride"), every photo containing a face in that cluster is
known to contain the bride.

The clustering is intentionally simple, dependency-free, and deterministic: a
greedy single-pass assignment by cosine distance to running cluster centroids.
Faces are matched to the nearest existing centroid within ``distance_max``;
otherwise they seed a new cluster. Processing in a stable input order makes the
output reproducible. This avoids a heavyweight clustering dependency while being
good enough for the modest number of people in a wedding; it can be swapped for
HDBSCAN/agglomerative later behind the same interface.

Embeddings are assumed (but not required) to be L2-normalized; the module
normalizes defensively so cosine distance is well-defined.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Optional

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

# Max cosine distance (1 - cosine similarity) for a face to join a cluster.
# 0 = identical direction, 1 = orthogonal. InsightFace buffalo_l (ArcFace
# w600k_r50, 512-d) embeddings are stable enough to use 0.55 for wedding
# photos: same person at different angles / lighting typically lands at 0.35–0.50.
# Using 0.40 was over-fragmenting (15 clusters for 45 faces in a wedding shoot).
DEFAULT_DISTANCE_MAX: float = 0.55

# IMPORTANT: this threshold is a property of the *embedding model*, not of
# clustering in general. Different backends put same-person and
# different-person pairs at different distances, so swapping the embedder
# without re-tuning silently wrecks clustering -- too low over-fragments (one
# person becomes many), too high merges different guests into one person.
#
# ARCFACE_DISTANCE_MAX is the value above, kept as a named alias so call sites
# can be explicit about which model they mean.
ARCFACE_DISTANCE_MAX: float = DEFAULT_DISTANCE_MAX

# SFace (128-d, Apache-2.0; see core.sface_backend) is a smaller model with
# less-separated embeddings, so it needs a tighter threshold than ArcFace.
# PROVISIONAL: derive the real value for your photos with
# ``scripts/benchmark_embedders.py``, which reports the same-person vs
# different-person distance distributions and the best separating threshold.
SFACE_DISTANCE_MAX: float = 0.40

# Backend name -> tuned threshold, for callers that select a backend by name.
DISTANCE_MAX_BY_BACKEND: dict[str, float] = {
    "arcface": ARCFACE_DISTANCE_MAX,
    "insightface": ARCFACE_DISTANCE_MAX,
    "buffalo_l": ARCFACE_DISTANCE_MAX,
    "sface": SFACE_DISTANCE_MAX,
}


def distance_max_for_backend(backend_name: str) -> float:
    """
    Tuned cosine-distance threshold for a named embedding backend.

    Falls back to the ArcFace default for unknown names (with a warning) since
    that is the historical behaviour, but prefer adding an explicit entry to
    :data:`DISTANCE_MAX_BY_BACKEND` -- an untuned threshold is a silent
    clustering-quality bug, not a crash.
    """
    key = (backend_name or "").strip().lower()
    if key in DISTANCE_MAX_BY_BACKEND:
        return DISTANCE_MAX_BY_BACKEND[key]
    logger.warning(
        "No tuned clustering threshold for embedding backend %r; falling back "
        "to the ArcFace value (%.2f). Tune it with "
        "scripts/benchmark_embedders.py and add it to DISTANCE_MAX_BY_BACKEND.",
        backend_name,
        DEFAULT_DISTANCE_MAX,
    )
    return DEFAULT_DISTANCE_MAX

# Clusters with fewer than this many faces are treated as noise (background
# guests, mis-detections) and filtered out before showing in the UI. A
# cluster of 1 face from 1 photo is rarely a meaningful identity.
DEFAULT_MIN_CLUSTER_SIZE: int = 2


class PersonClusteringError(Exception):
    """Raised when clustering inputs are invalid."""


@dataclasses.dataclass(frozen=True)
class FaceRef:
    """
    One face to be clustered.

    Attributes:
        image_path: Source image path (string).
        face_index: Index of the face within that image.
        vector: The face embedding (any length; normalized internally).
    """

    image_path: str
    face_index: int
    vector: np.ndarray


@dataclasses.dataclass
class PersonCluster:
    """
    A group of faces believed to be the same person.

    Attributes:
        cluster_id: Stable integer id (assignment order).
        faces: The member faces.
        centroid: Mean of member embeddings, L2-normalized (the cluster's
            representative direction; used to match labels across runs).
    """

    cluster_id: int
    faces: list[FaceRef]
    centroid: np.ndarray

    @property
    def photo_paths(self) -> set[str]:
        """Distinct images in which this person appears."""
        return {face.image_path for face in self.faces}

    @property
    def size(self) -> int:
        """Number of member faces."""
        return len(self.faces)


class PersonClusterer:
    """
    Greedy cosine clustering of face embeddings into people.

    Args:
        distance_max: Maximum cosine distance for a face to join an existing
            cluster. Must be in ``[0, 2]``.

    Raises:
        PersonClusteringError: if ``distance_max`` is out of range.
    """

    def __init__(
        self,
        distance_max: float = DEFAULT_DISTANCE_MAX,
        method: str = "auto",
        min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    ) -> None:
        if not 0.0 <= distance_max <= 2.0:
            raise PersonClusteringError(
                f"distance_max must be in [0, 2], got {distance_max}"
            )
        if min_cluster_size < 1:
            raise PersonClusteringError(
                f"min_cluster_size must be >= 1, got {min_cluster_size}"
            )
        self.distance_max = float(distance_max)
        self.min_cluster_size = int(min_cluster_size)
        # "auto"  -> agglomerative (scipy) when available, else greedy
        # "agglomerative" -> force agglomerative (error if scipy missing)
        # "greedy" -> the original single-pass method
        self.method = method

    def cluster(self, faces: Iterable[FaceRef]) -> list[PersonCluster]:
        """
        Cluster ``faces`` into people.

        Each face joins the nearest existing cluster whose centroid is within
        ``distance_max`` (cosine), updating that centroid; otherwise it seeds a
        new cluster. Deterministic for a fixed input order. Clusters are
        returned largest-first (ties by ascending ``cluster_id``).

        Returns:
            A list of :class:`PersonCluster`. Empty input yields ``[]``.
        """
        faces = list(faces)

        # Prefer agglomerative clustering (scipy): it corrects the greedy pass's
        # early mistakes and doesn't need the number of people in advance. Falls
        # back to greedy when scipy is unavailable or ``method="greedy"``.
        if self.method != "greedy" and len(faces) >= 2:
            agglomerative = self._cluster_agglomerative(faces)
            if agglomerative is not None:
                return agglomerative
            if self.method == "agglomerative":
                raise PersonClusteringError(
                    "agglomerative clustering requires SciPy (pip install scipy)"
                )

        clusters: list[_MutableCluster] = []
        for face in faces:
            unit = self._unit(face.vector)
            best: _MutableCluster | None = None
            best_distance = self.distance_max
            for cluster in clusters:
                distance = self._cosine_distance(unit, cluster.centroid)
                if distance <= best_distance:
                    best = cluster
                    best_distance = distance
            if best is None:
                clusters.append(_MutableCluster(len(clusters), face, unit))
            else:
                best.add(face, unit)

        result = [c.freeze() for c in clusters]
        result.sort(key=lambda c: (-c.size, c.cluster_id))
        # Filter out noise singletons before returning.
        result = [c for c in result if c.size >= self.min_cluster_size]
        logger.info(
            "Clustered %d face(s) into %d person cluster(s) (greedy, distance_max=%.2f, min_size=%d).",
            sum(c.size for c in result),
            len(result),
            self.distance_max,
            self.min_cluster_size,
        )
        return result

    @staticmethod
    def _unit(vector: np.ndarray) -> np.ndarray:
        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        return vec if norm == 0.0 else vec / norm

    @staticmethod
    def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine distance in ``[0, 2]`` for unit vectors (1 - cosine sim)."""
        return float(1.0 - np.dot(a, b))

    def _cluster_agglomerative(self, faces: list["FaceRef"]) -> Optional[list[PersonCluster]]:
        """
        Agglomerative (hierarchical) clustering via SciPy, or ``None`` if SciPy
        isn't installed so the caller can fall back to greedy.

        Average-linkage on cosine distance with a flat cut at ``distance_max``:
        faces merge into a person while their mean cosine distance stays under the
        threshold. Unlike the greedy pass, a later face can pull two tentative
        clusters together, so early mistakes self-correct. Deterministic.
        """
        try:
            from scipy.cluster.hierarchy import fcluster, linkage
            from scipy.spatial.distance import pdist
        except Exception:  # noqa: BLE001 - SciPy optional -> greedy fallback
            return None

        units = np.vstack([self._unit(f.vector) for f in faces]).astype(np.float64)
        # Degenerate: identical/zero vectors -> pdist all zeros -> one cluster.
        condensed = pdist(units, metric="cosine")
        condensed = np.nan_to_num(condensed, nan=0.0, posinf=2.0, neginf=0.0)
        linkage_matrix = linkage(condensed, method="average")
        labels = fcluster(linkage_matrix, t=self.distance_max, criterion="distance")

        # Group faces by label, build a PersonCluster per group.
        groups: dict[int, list[FaceRef]] = {}
        for face, label in zip(faces, labels):
            groups.setdefault(int(label), []).append(face)

        clusters: list[PersonCluster] = []
        for members in groups.values():
            centroid = np.mean(
                np.vstack([self._unit(f.vector) for f in members]), axis=0
            )
            clusters.append(PersonCluster(cluster_id=0, faces=members, centroid=self._unit(centroid)))

        # Largest-first, then re-number ids so they're stable/sequential.
        clusters.sort(key=lambda c: -c.size)
        for new_id, cluster in enumerate(clusters):
            cluster.cluster_id = new_id

        # Drop noise singletons (background faces detected only once).
        clusters = [c for c in clusters if c.size >= self.min_cluster_size]

        logger.info(
            "Clustered %d face(s) into %d person cluster(s) "
            "(agglomerative, distance_max=%.2f, min_size=%d).",
            len(faces),
            len(clusters),
            self.distance_max,
            self.min_cluster_size,
        )
        return clusters


@dataclasses.dataclass
class _MutableCluster:
    """Accumulates member faces and a running, L2-normalized centroid."""

    cluster_id: int
    _first: dataclasses.InitVar[FaceRef]
    _first_unit: dataclasses.InitVar[np.ndarray]

    def __post_init__(self, _first: FaceRef, _first_unit: np.ndarray) -> None:
        self.faces: list[FaceRef] = [_first]
        self._sum: np.ndarray = _first_unit.astype(np.float32).copy()
        self.centroid: np.ndarray = self._normalize(self._sum)

    def add(self, face: FaceRef, unit: np.ndarray) -> None:
        self.faces.append(face)
        self._sum = self._sum + unit
        self.centroid = self._normalize(self._sum)

    def freeze(self) -> PersonCluster:
        return PersonCluster(
            cluster_id=self.cluster_id, faces=list(self.faces), centroid=self.centroid
        )

    @staticmethod
    def _normalize(vec: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(vec))
        return vec if norm == 0.0 else (vec / norm).astype(np.float32)
