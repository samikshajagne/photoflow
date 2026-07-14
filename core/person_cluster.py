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
from typing import Iterable

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

# Max cosine distance (1 - cosine similarity) for a face to join a cluster.
# 0 = identical direction, 1 = orthogonal. ~0.4 is a reasonable starting bar
# for modern face embeddings; intended to be tuned on real data.
DEFAULT_DISTANCE_MAX: float = 0.4


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

    def __init__(self, distance_max: float = DEFAULT_DISTANCE_MAX) -> None:
        if not 0.0 <= distance_max <= 2.0:
            raise PersonClusteringError(
                f"distance_max must be in [0, 2], got {distance_max}"
            )
        self.distance_max = float(distance_max)

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
        logger.info(
            "Clustered %d face(s) into %d person cluster(s) (distance_max=%.2f).",
            sum(c.size for c in result),
            len(result),
            self.distance_max,
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
