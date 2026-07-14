"""
Unit tests for persistence.identity_store.

Labels are saved with their cluster centroid and re-bound to freshly computed
clusters by nearest centroid, so a one-time labelling survives re-analysis.
"""

import numpy as np
import pytest

from core.identity import PersonIndex
from core.person_cluster import PersonCluster
from persistence.identity_store import (
    IdentityStoreError,
    SavedLabel,
    apply_saved_labels,
    load_labels,
    save_labels,
)


def _cluster(cid: int, centroid) -> PersonCluster:
    return PersonCluster(
        cluster_id=cid, faces=[], centroid=np.asarray(centroid, np.float32)
    )


def _labelled_index() -> PersonIndex:
    index = PersonIndex.from_clusters(
        [_cluster(0, [1.0, 0.0, 0.0]), _cluster(1, [0.0, 1.0, 0.0])]
    )
    index.set_label(0, "bride")
    index.set_label(1, "groom")
    return index


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "labels.json"
    save_labels(path, _labelled_index())

    saved = load_labels(path)
    by_label = {s.label: s.centroid for s in saved}
    assert set(by_label) == {"bride", "groom"}
    assert np.allclose(by_label["bride"], [1.0, 0.0, 0.0], atol=1e-6)


def test_load_missing_file_returns_empty(tmp_path):
    assert load_labels(tmp_path / "nope.json") == []


def test_load_malformed_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(IdentityStoreError):
        load_labels(bad)


def test_apply_saved_labels_binds_by_nearest_centroid():
    saved = [
        SavedLabel("bride", (1.0, 0.0, 0.0)),
        SavedLabel("groom", (0.0, 1.0, 0.0)),
    ]
    # Fresh clusters with slightly rotated centroids and different ids/order.
    fresh = PersonIndex.from_clusters(
        [_cluster(7, [0.0, 0.98, 0.05]), _cluster(3, [0.99, 0.02, 0.0])]
    )

    applied = apply_saved_labels(fresh, saved, distance_max=0.4)

    assert applied == 2
    # Cluster near [1,0,0] -> bride; cluster near [0,1,0] -> groom.
    assert fresh.cluster(3).cluster_id == 3
    present_labels = fresh.labels
    assert present_labels[3] == "bride"
    assert present_labels[7] == "groom"


def test_apply_saved_labels_skips_when_too_far():
    saved = [SavedLabel("bride", (1.0, 0.0, 0.0))]
    fresh = PersonIndex.from_clusters([_cluster(0, [0.0, 1.0, 0.0])])  # orthogonal
    applied = apply_saved_labels(fresh, saved, distance_max=0.4)
    assert applied == 0
    assert fresh.labels == {}


def test_full_round_trip_relabels_new_run(tmp_path):
    path = tmp_path / "labels.json"
    save_labels(path, _labelled_index())

    # A new run produces equivalent clusters (new ids); reload and apply.
    new_run = PersonIndex.from_clusters(
        [_cluster(11, [0.97, 0.1, 0.0]), _cluster(12, [0.05, 0.99, 0.0])]
    )
    apply_saved_labels(new_run, load_labels(path))

    assert new_run.labels[11] == "bride"
    assert new_run.labels[12] == "groom"
