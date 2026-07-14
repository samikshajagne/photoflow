"""
Unit tests for core.person_cluster.

Clustering is pure and deterministic, so synthetic embeddings (distinct
directions = distinct people) exercise it fully without any model.
"""

import numpy as np
import pytest

from core.person_cluster import (
    FaceRef,
    PersonClusterer,
    PersonClusteringError,
)


def _face(path: str, idx: int, vec) -> FaceRef:
    return FaceRef(image_path=path, face_index=idx, vector=np.asarray(vec, np.float32))


# --------------------------------------------------------------------------- #
# Clustering behavior
# --------------------------------------------------------------------------- #
def test_empty_input_yields_no_clusters():
    assert PersonClusterer().cluster([]) == []


def test_single_face_one_cluster_with_unit_centroid():
    clusters = PersonClusterer().cluster([_face("/p/a.jpg", 0, [3.0, 4.0])])
    assert len(clusters) == 1
    assert clusters[0].size == 1
    # Centroid is L2-normalized: [3,4] -> [0.6, 0.8].
    assert np.allclose(clusters[0].centroid, [0.6, 0.8], atol=1e-5)


def test_same_direction_faces_merge():
    faces = [
        _face("/p/a.jpg", 0, [1.0, 0.0, 0.0]),
        _face("/p/b.jpg", 0, [0.98, 0.05, 0.0]),  # nearly same direction
    ]
    clusters = PersonClusterer(distance_max=0.4).cluster(faces)
    assert len(clusters) == 1
    assert clusters[0].size == 2
    assert clusters[0].photo_paths == {"/p/a.jpg", "/p/b.jpg"}


def test_orthogonal_faces_split():
    faces = [
        _face("/p/a.jpg", 0, [1.0, 0.0, 0.0]),
        _face("/p/b.jpg", 0, [0.0, 1.0, 0.0]),  # cosine distance 1.0 > 0.4
    ]
    clusters = PersonClusterer(distance_max=0.4).cluster(faces)
    assert len(clusters) == 2


def test_mixed_population_groups_and_sorts_largest_first():
    faces = [
        _face("/p/1.jpg", 0, [1.0, 0.0, 0.0]),
        _face("/p/2.jpg", 0, [0.0, 1.0, 0.0]),
        _face("/p/3.jpg", 0, [0.97, 0.1, 0.0]),   # person A
        _face("/p/4.jpg", 0, [0.99, 0.02, 0.0]),  # person A
        _face("/p/5.jpg", 0, [0.02, 0.99, 0.0]),  # person B
    ]
    clusters = PersonClusterer(distance_max=0.4).cluster(faces)

    assert len(clusters) == 2
    # Largest cluster first: 3 faces (A) then 2 faces (B).
    assert [c.size for c in clusters] == [3, 2]
    assert clusters[0].photo_paths == {"/p/1.jpg", "/p/3.jpg", "/p/4.jpg"}
    assert clusters[1].photo_paths == {"/p/2.jpg", "/p/5.jpg"}


def test_clustering_is_deterministic():
    faces = [
        _face("/p/1.jpg", 0, [1.0, 0.0]),
        _face("/p/2.jpg", 0, [0.0, 1.0]),
        _face("/p/3.jpg", 0, [0.99, 0.05]),
    ]
    a = PersonClusterer().cluster(faces)
    b = PersonClusterer().cluster(faces)
    assert [(c.cluster_id, c.size) for c in a] == [(c.cluster_id, c.size) for c in b]


def test_unnormalized_vectors_are_handled():
    # Same direction, very different magnitudes -> still one cluster.
    faces = [
        _face("/p/a.jpg", 0, [10.0, 0.0]),
        _face("/p/b.jpg", 0, [0.1, 0.0]),
    ]
    clusters = PersonClusterer(distance_max=0.1).cluster(faces)
    assert len(clusters) == 1


def test_zero_vector_does_not_crash():
    faces = [_face("/p/a.jpg", 0, [0.0, 0.0]), _face("/p/b.jpg", 0, [1.0, 0.0])]
    clusters = PersonClusterer().cluster(faces)
    assert sum(c.size for c in clusters) == 2


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #
def test_distance_out_of_range_raises():
    with pytest.raises(PersonClusteringError):
        PersonClusterer(distance_max=-0.1)
    with pytest.raises(PersonClusteringError):
        PersonClusterer(distance_max=2.5)
