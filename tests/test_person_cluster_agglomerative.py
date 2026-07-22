"""Component 2 tests: agglomerative (scipy) person clustering."""

from __future__ import annotations

import numpy as np
import pytest

from core.person_cluster import FaceRef, PersonClusterer

scipy = pytest.importorskip("scipy")  # agglomerative path needs scipy


def _face(path, idx, vec):
    return FaceRef(image_path=path, face_index=idx, vector=np.asarray(vec, dtype=np.float32))


def _person(base, n, jitter=0.02, seed=0):
    """n near-identical unit vectors around a base direction (same person)."""
    rng = np.random.default_rng(seed)
    base = np.asarray(base, dtype=np.float64)
    out = []
    for _ in range(n):
        v = base + rng.normal(0, jitter, size=base.shape)
        out.append(v / np.linalg.norm(v))
    return out


def test_three_well_separated_people():
    # Three orthogonal directions -> three clusters.
    a = _person([1, 0, 0], 4, seed=1)
    b = _person([0, 1, 0], 3, seed=2)
    c = _person([0, 0, 1], 2, seed=3)
    faces = []
    for i, v in enumerate(a):
        faces.append(_face("a", i, v))
    for i, v in enumerate(b):
        faces.append(_face("b", i, v))
    for i, v in enumerate(c):
        faces.append(_face("c", i, v))

    clusters = PersonClusterer(distance_max=0.4, method="agglomerative").cluster(faces)
    assert len(clusters) == 3
    # Largest first: person A (4) then B (3) then C (2).
    assert [c.size for c in clusters] == [4, 3, 2]
    # cluster_ids are sequential.
    assert [c.cluster_id for c in clusters] == [0, 1, 2]


def test_corrects_a_chain_greedy_would_split():
    # Two tight groups; agglomerative keeps each whole.
    g1 = _person([1, 0, 0], 5, jitter=0.03, seed=10)
    g2 = _person([-1, 0, 0], 5, jitter=0.03, seed=11)
    faces = [_face("g1", i, v) for i, v in enumerate(g1)]
    faces += [_face("g2", i, v) for i, v in enumerate(g2)]
    clusters = PersonClusterer(distance_max=0.4, method="agglomerative").cluster(faces)
    assert len(clusters) == 2
    assert sorted(c.size for c in clusters) == [5, 5]


def test_single_face_is_one_cluster():
    faces = [_face("a", 0, [1, 0, 0])]
    clusters = PersonClusterer(method="agglomerative").cluster(faces)
    assert len(clusters) == 1
    assert clusters[0].size == 1


def test_empty_input():
    assert PersonClusterer(method="agglomerative").cluster([]) == []


def test_auto_uses_agglomerative_when_scipy_present():
    a = _person([1, 0, 0], 3, seed=1)
    b = _person([0, 1, 0], 3, seed=2)
    faces = [_face("a", i, v) for i, v in enumerate(a)]
    faces += [_face("b", i, v) for i, v in enumerate(b)]
    # Default method="auto" should find the 2 people just like explicit agglomerative.
    clusters = PersonClusterer(distance_max=0.4).cluster(faces)
    assert len(clusters) == 2


def test_greedy_still_available():
    a = _person([1, 0, 0], 3, seed=1)
    b = _person([0, 1, 0], 3, seed=2)
    faces = [_face("a", i, v) for i, v in enumerate(a)]
    faces += [_face("b", i, v) for i, v in enumerate(b)]
    clusters = PersonClusterer(distance_max=0.4, method="greedy").cluster(faces)
    assert len(clusters) == 2
