"""
Persistence for person labels (PhotoFlow album identity, Phase 1).

Clustering is recomputed every run, so cluster ids are not stable across runs.
To make labelling a *one-time* effort, we persist each label together with its
cluster centroid (a unit vector). On a later run, saved labels are matched back
onto the freshly computed clusters by nearest centroid (cosine distance) within
a threshold -- so "this cluster is the bride" survives re-analysis.

The store is a small JSON file; this module has no Qt and no heavy
dependencies (numpy only), so it can be tested directly.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Iterable, Union

import numpy as np

from core.identity import PersonIndex
from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]

# Max cosine distance for a saved label to bind to a fresh cluster's centroid.
DEFAULT_MATCH_DISTANCE_MAX: float = 0.4


class IdentityStoreError(Exception):
    """Raised when the identity store cannot be read or is malformed."""


@dataclasses.dataclass(frozen=True)
class SavedLabel:
    """A persisted label and the centroid of the cluster it was applied to."""

    label: str
    centroid: tuple[float, ...]


def save_labels(path: PathLike, index: PersonIndex) -> Path:
    """
    Write the labelled clusters of ``index`` to a JSON file.

    Only labelled clusters are saved (each as ``{label, centroid}``). The parent
    directory is created if needed. Returns the written path.
    """
    payload = {
        "version": 1,
        "labels": [
            {"label": label, "centroid": [float(x) for x in cluster.centroid]}
            for cluster, label in index.labelled_clusters()
        ],
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Saved %d label(s) to '%s'.", len(payload["labels"]), out)
    return out


def load_labels(path: PathLike) -> list[SavedLabel]:
    """Read saved labels; returns ``[]`` if the file does not exist."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [
            SavedLabel(label=str(item["label"]), centroid=tuple(item["centroid"]))
            for item in data["labels"]
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IdentityStoreError(f"Malformed identity store '{p}': {exc}") from exc


def apply_saved_labels(
    index: PersonIndex,
    saved: Iterable[SavedLabel],
    distance_max: float = DEFAULT_MATCH_DISTANCE_MAX,
) -> int:
    """
    Bind saved labels onto ``index``'s clusters by nearest centroid.

    Each saved label is matched to the closest still-unlabelled cluster whose
    centroid is within ``distance_max`` (cosine). Saved labels are applied
    largest-cluster-bias by processing in input order; a cluster receives at
    most one label. Mutates ``index`` in place and returns the number of labels
    successfully applied.
    """
    clusters = index.clusters
    taken: set[int] = set(index.labels.keys())
    applied = 0

    for entry in saved:
        target = np.asarray(entry.centroid, dtype=np.float32).reshape(-1)
        target = _unit(target)
        best_id: int | None = None
        best_distance = distance_max
        for cluster in clusters:
            if cluster.cluster_id in taken:
                continue
            distance = float(1.0 - np.dot(target, _unit(np.asarray(cluster.centroid))))
            if distance <= best_distance:
                best_id = cluster.cluster_id
                best_distance = distance
        if best_id is not None:
            index.set_label(best_id, entry.label)
            taken.add(best_id)
            applied += 1

    logger.info("Applied %d saved label(s) to current clusters.", applied)
    return applied


def _unit(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vec))
    return vec if norm == 0.0 else vec / norm
