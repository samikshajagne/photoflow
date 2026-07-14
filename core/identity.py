"""
Identity orchestration for PhotoFlow album generation (Phase 1).

Ties the identity engines together into something the album can query:

    detected faces (paths + regions)
        -> embed   (core.face_embedder)
        -> cluster (core.person_cluster)        => one cluster per person
        -> label   ("this cluster is the bride")
        => persons_present: which labelled people appear in each photo

That last map is what the section builder uses to assemble bride-only,
groom-only, couple, and family sheets.

Roles are just labels (free strings); a handful of well-known role constants are
provided for the common wedding sheets, but any label is accepted. Labelling is
done once by the photographer and persisted via
:mod:`persistence.identity_store`, which matches saved labels back onto freshly
computed clusters by centroid so a re-run doesn't lose the work.

This module adds no new analysis -- it sequences the existing engines and
reshapes their output.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Optional, Sequence

from core.face_embedder import FaceEmbedder, FaceEmbeddingError
from core.person_cluster import FaceRef, PersonCluster, PersonClusterer
from utils.logger import get_logger

logger = get_logger(__name__)

FaceBox = tuple[float, float, float, float]

# Well-known labels for the common wedding sheets. Any string is a valid label;
# these are conveniences so callers don't hard-code magic strings.
ROLE_BRIDE = "bride"
ROLE_GROOM = "groom"
ROLE_FAMILY = "family"
ROLE_CLOSE_FAMILY = "close_family"
ROLE_GUEST = "guest"


def build_face_refs(
    face_inputs: Iterable[tuple[str, Sequence[FaceBox]]],
    embedder: FaceEmbedder,
) -> list[FaceRef]:
    """
    Embed every detected face into a flat list of :class:`FaceRef`.

    Args:
        face_inputs: ``(image_path, regions)`` pairs from the face stage.
        embedder: A configured :class:`~core.face_embedder.FaceEmbedder`.

    Embedding failures for a single image are logged and skipped (non-fatal),
    so one unreadable file never aborts identity for the whole shoot.
    """
    refs: list[FaceRef] = []
    for image_path, regions in face_inputs:
        if not regions:
            continue
        try:
            embeddings = embedder.embed(image_path, regions)
        except FaceEmbeddingError as exc:
            logger.warning("Embedding failed for '%s': %s", image_path, exc)
            continue
        for emb in embeddings:
            refs.append(
                FaceRef(
                    image_path=emb.image_path,
                    face_index=emb.face_index,
                    vector=emb.vector,
                )
            )
    return refs


class PersonIndex:
    """
    People discovered in a shoot, plus optional per-cluster labels.

    Build with :meth:`build` (embeds + clusters), then assign labels with
    :meth:`set_label`. ``persons_present`` and the ``photos_with*`` queries only
    ever consider *labelled* clusters, so unlabelled guests in the background
    never affect a "solo" sheet.
    """

    def __init__(
        self,
        clusters: list[PersonCluster],
        labels: Optional[dict[int, str]] = None,
    ) -> None:
        self._clusters = list(clusters)
        self._by_id = {c.cluster_id: c for c in self._clusters}
        self._labels: dict[int, str] = {}
        for cluster_id, label in (labels or {}).items():
            self.set_label(cluster_id, label)

    # ----------------------------------------------------------------- #
    # Construction
    # ----------------------------------------------------------------- #
    @classmethod
    def build(
        cls,
        face_inputs: Iterable[tuple[str, Sequence[FaceBox]]],
        embedder: FaceEmbedder,
        clusterer: Optional[PersonClusterer] = None,
    ) -> "PersonIndex":
        """Embed faces, cluster them into people, and return an unlabelled index."""
        refs = build_face_refs(face_inputs, embedder)
        clusters = (clusterer or PersonClusterer()).cluster(refs)
        return cls(clusters)

    @classmethod
    def from_clusters(
        cls, clusters: list[PersonCluster], labels: Optional[dict[int, str]] = None
    ) -> "PersonIndex":
        """Construct directly from precomputed clusters (used in tests/persistence)."""
        return cls(clusters, labels)

    # ----------------------------------------------------------------- #
    # Labels
    # ----------------------------------------------------------------- #
    @property
    def clusters(self) -> list[PersonCluster]:
        return list(self._clusters)

    @property
    def labels(self) -> dict[int, str]:
        """Mapping of cluster id -> label for labelled clusters only."""
        return dict(self._labels)

    def set_label(self, cluster_id: int, label: str) -> None:
        """Label a cluster (e.g. ``ROLE_BRIDE``). Raises on unknown cluster id."""
        if cluster_id not in self._by_id:
            raise KeyError(f"No cluster with id {cluster_id}")
        if not label or not str(label).strip():
            raise ValueError("label must be a non-empty string")
        self._labels[cluster_id] = str(label)

    def clear_label(self, cluster_id: int) -> None:
        self._labels.pop(cluster_id, None)

    def cluster(self, cluster_id: int) -> PersonCluster:
        return self._by_id[cluster_id]

    # ----------------------------------------------------------------- #
    # Queries
    # ----------------------------------------------------------------- #
    def persons_present(self) -> dict[str, set[str]]:
        """
        Map each photo to the set of *labelled* people appearing in it.

        Photos containing only unlabelled clusters are omitted (they have no
        labelled people). The same photo may carry several labels.
        """
        present: dict[str, set[str]] = {}
        for cluster_id, label in self._labels.items():
            for photo in self._by_id[cluster_id].photo_paths:
                present.setdefault(photo, set()).add(label)
        return present

    def photos_with(self, label: str) -> set[str]:
        """Photos containing the given labelled person."""
        return {p for p, labels in self.persons_present().items() if label in labels}

    def photos_with_all(self, labels: Iterable[str]) -> set[str]:
        """Photos containing *all* of the given labelled people (e.g. couple)."""
        wanted = set(labels)
        return {
            p for p, present in self.persons_present().items() if wanted <= present
        }

    def photos_with_only(self, label: str) -> set[str]:
        """
        Photos whose only *labelled* person is ``label`` (e.g. a bride-solo
        sheet). Unlabelled people in the frame do not disqualify the photo.
        """
        return {
            p
            for p, present in self.persons_present().items()
            if present == {label}
        }

    def labelled_clusters(self) -> list[tuple[PersonCluster, str]]:
        """(cluster, label) pairs for labelled clusters, largest cluster first."""
        pairs = [
            (self._by_id[cid], label) for cid, label in self._labels.items()
        ]
        pairs.sort(key=lambda cl: (-cl[0].size, cl[0].cluster_id))
        return pairs
