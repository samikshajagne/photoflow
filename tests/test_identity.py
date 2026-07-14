"""
Unit tests for core.identity.

A fake embedder (duck-typed: just an ``embed`` method) feeds synthetic
embeddings, so orchestration + clustering + labelling + queries are exercised
without any model or images.
"""

import numpy as np
import pytest

from core.face_embedder import FaceEmbedding
from core.identity import PersonIndex, build_face_refs
from core.person_cluster import PersonClusterer


class FakeEmbedder:
    """Returns prearranged vectors per image path."""

    def __init__(self, table: dict[str, list[np.ndarray]]):
        self._table = table

    def embed(self, image_path, regions):
        vecs = self._table.get(str(image_path), [])
        return [FaceEmbedding(str(image_path), i, v) for i, v in enumerate(vecs)]


BRIDE = np.array([1.0, 0.0, 0.0], np.float32)
GROOM = np.array([0.0, 1.0, 0.0], np.float32)
GUEST = np.array([0.0, 0.0, 1.0], np.float32)


def _scene():
    table = {
        "/p/1.jpg": [BRIDE],
        "/p/2.jpg": [BRIDE],
        "/p/3.jpg": [BRIDE, GROOM],   # couple
        "/p/4.jpg": [GROOM],
        "/p/5.jpg": [GUEST],
    }
    face_inputs = [(p, [(0.0, 0.0, 1.0, 1.0)] * len(v)) for p, v in table.items()]
    return FakeEmbedder(table), face_inputs


def _label(index: PersonIndex, photo: str, label: str) -> None:
    """Label whichever cluster contains ``photo``."""
    for cluster in index.clusters:
        if photo in cluster.photo_paths:
            index.set_label(cluster.cluster_id, label)
            return
    raise AssertionError(f"no cluster contains {photo}")


def test_build_face_refs_flattens_all_faces():
    embedder, face_inputs = _scene()
    refs = build_face_refs(face_inputs, embedder)
    # 1+1+2+1+1 = 6 faces.
    assert len(refs) == 6
    assert {r.image_path for r in refs} == {f"/p/{i}.jpg" for i in range(1, 6)}


def test_build_face_refs_skips_embedding_failures():
    class Boom:
        def embed(self, image_path, regions):
            from core.face_embedder import FaceEmbeddingError

            raise FaceEmbeddingError("nope")

    refs = build_face_refs([("/p/x.jpg", [(0, 0, 1, 1)])], Boom())
    assert refs == []


def test_person_index_build_and_queries():
    embedder, face_inputs = _scene()
    index = PersonIndex.build(face_inputs, embedder, PersonClusterer(distance_max=0.3))

    # Three people: bride, groom, guest.
    assert len(index.clusters) == 3

    _label(index, "/p/1.jpg", "bride")
    _label(index, "/p/4.jpg", "groom")
    # guest deliberately left unlabelled

    present = index.persons_present()
    assert present["/p/1.jpg"] == {"bride"}
    assert present["/p/3.jpg"] == {"bride", "groom"}
    # The guest photo has no *labelled* person, so it's absent.
    assert "/p/5.jpg" not in present

    assert index.photos_with("bride") == {"/p/1.jpg", "/p/2.jpg", "/p/3.jpg"}
    assert index.photos_with_all({"bride", "groom"}) == {"/p/3.jpg"}
    # Solo bride excludes the couple shot but not the (unlabelled) guest frames.
    assert index.photos_with_only("bride") == {"/p/1.jpg", "/p/2.jpg"}


def test_set_label_validates():
    index = PersonIndex.build(*reversed_args())  # built below
    with pytest.raises(KeyError):
        index.set_label(9999, "bride")
    # Empty label rejected.
    some_id = index.clusters[0].cluster_id
    with pytest.raises(ValueError):
        index.set_label(some_id, "  ")


def reversed_args():
    embedder, face_inputs = _scene()
    return (face_inputs, embedder)


def test_labelled_clusters_orders_largest_first():
    embedder, face_inputs = _scene()
    index = PersonIndex.build(face_inputs, embedder, PersonClusterer(distance_max=0.3))
    _label(index, "/p/1.jpg", "bride")   # 3 faces
    _label(index, "/p/4.jpg", "groom")   # 2 faces
    labelled = index.labelled_clusters()
    assert [label for _, label in labelled] == ["bride", "groom"]
