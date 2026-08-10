#!/usr/bin/env python3
"""
Compare face-embedding backends on *your own* photos.

Why this exists
---------------
PhotoFlow's album features rest on face recognition, but InsightFace's
pretrained weights (``buffalo_l`` / ArcFace) are licensed for non-commercial
research only, which blocks selling the product. ``core.sface_backend`` offers a
permissively-licensed alternative (SFace, Apache-2.0), but SFace is a smaller
model -- so the question isn't "is it legal" (it is) but **"is it accurate
enough on real wedding photos?"** Published benchmark numbers won't answer that
for your lighting, your lenses and your guests. This script does.

It reports, per backend:

* **Separation** -- mean distance between faces of the *same* person vs
  *different* people. Bigger gap = easier, more reliable clustering. This is
  the single most important number.
* **Best threshold and its accuracy** -- the cosine distance that best splits
  same-person from different-person pairs, plus how often that split is right.
  Feed this back into ``core.person_cluster.DISTANCE_MAX_BY_BACKEND``.
* **End-to-end clustering quality** -- if labels are available, how well the
  real clusterer recovers the true people.
* **Speed** -- seconds per face, which matters at 20k photos per wedding.

Usage
-----
Labelled mode (best -- gives accuracy, not just speed). One subfolder per
person, any number of photos inside::

    photos/
      priya/    img1.jpg img2.jpg ...
      arjun/    img3.jpg ...

    python scripts/benchmark_embedders.py --labelled photos/

Unlabelled mode (no ground truth; reports distance spread, speed, and how many
people each backend *thinks* it found -- useful as a smoke test)::

    python scripts/benchmark_embedders.py --folder photos/

Notes
-----
* Backends that aren't installed are skipped with a clear message, so this runs
  with only SFace available (no InsightFace needed).
* Face detection uses the normal ``FaceDetector``, so MediaPipe must be
  installed for face boxes.
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# Allow running as `python scripts/benchmark_embedders.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.face_detector import FaceDetector  # noqa: E402
from core.face_embedder import FaceEmbedder, FaceEmbeddingError  # noqa: E402
from core.person_cluster import FaceRef, PersonClusterer  # noqa: E402
from utils.config import load_config  # noqa: E402

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


# --------------------------------------------------------------------------- #
# Backend discovery
# --------------------------------------------------------------------------- #
def available_backends(names: Optional[list[str]] = None) -> dict[str, Callable]:
    """Build every requested backend that can actually be constructed here."""
    wanted = set(names or ["sface", "insightface"])
    built: dict[str, Callable] = {}

    if "sface" in wanted:
        try:
            from core.sface_backend import build_sface_backend

            built["sface"] = build_sface_backend()
        except Exception as exc:  # noqa: BLE001
            print(f"  ! sface unavailable: {exc}")

    if "insightface" in wanted:
        try:
            import insightface  # noqa: F401

            from core.insightface_backend import build_insightface_backend

            built["insightface"] = build_insightface_backend()
        except Exception as exc:  # noqa: BLE001
            print(f"  ! insightface unavailable ({exc}); skipping (expected if "
                  "you haven't installed it, or are avoiding it for licensing).")

    return built


# --------------------------------------------------------------------------- #
# Data collection
# --------------------------------------------------------------------------- #
def collect_labelled(root: Path) -> list[tuple[Path, str]]:
    """``[(image_path, person_label)]`` from one subfolder per person."""
    pairs: list[tuple[Path, str]] = []
    for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for img in sorted(person_dir.iterdir()):
            if img.suffix.lower() in _IMAGE_EXTS:
                pairs.append((img, person_dir.name))
    return pairs


def collect_flat(root: Path) -> list[tuple[Path, str]]:
    """``[(image_path, "")]`` for an unlabelled folder (recursive)."""
    return [
        (p, "")
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    ]


def embed_all(
    backend: Callable, items: list[tuple[Path, str]], detector: FaceDetector, config
) -> tuple[list[np.ndarray], list[str], float]:
    """Detect + embed every image. Returns (vectors, labels, seconds_spent)."""
    embedder = FaceEmbedder.from_config(config, embed_backend=backend)
    vectors: list[np.ndarray] = []
    labels: list[str] = []
    elapsed = 0.0

    for path, label in items:
        try:
            regions = detector.detect(path).regions
        except Exception as exc:  # noqa: BLE001
            print(f"    - detection failed for {path.name}: {exc}")
            continue
        if not regions:
            continue
        # Largest face only: benchmarking identity, not group composition.
        largest = max(regions, key=lambda r: r[2] * r[3])
        start = time.perf_counter()
        try:
            embeddings = embedder.embed(path, [largest])
        except FaceEmbeddingError as exc:
            print(f"    - embedding failed for {path.name}: {exc}")
            continue
        elapsed += time.perf_counter() - start
        for emb in embeddings:
            vec = np.asarray(emb.vector, dtype=np.float32)
            if np.any(vec):  # zero vector = backend found no face
                vectors.append(vec)
                labels.append(label)

    return vectors, labels, elapsed


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(1.0 - np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0))


def pair_distances(
    vectors: list[np.ndarray], labels: list[str]
) -> tuple[list[float], list[float]]:
    """Cosine distances split into (same-person, different-person)."""
    same: list[float] = []
    diff: list[float] = []
    for i, j in itertools.combinations(range(len(vectors)), 2):
        d = _cosine(vectors[i], vectors[j])
        (same if labels[i] == labels[j] else diff).append(d)
    return same, diff


def best_threshold(same: list[float], diff: list[float]) -> tuple[float, float]:
    """
    Threshold maximising pair-classification accuracy, and that accuracy.

    Sweeps candidate cutoffs and scores "same person if distance <= t".
    """
    if not same or not diff:
        return float("nan"), float("nan")
    candidates = sorted(set(round(d, 3) for d in same + diff))
    best_t, best_acc = candidates[0], 0.0
    total = len(same) + len(diff)
    for t in candidates:
        correct = sum(1 for d in same if d <= t) + sum(1 for d in diff if d > t)
        acc = correct / total
        if acc > best_acc:
            best_t, best_acc = t, acc
    return best_t, best_acc


def cluster_quality(
    vectors: list[np.ndarray], labels: list[str], distance_max: float
) -> dict[str, float]:
    """Run the real clusterer and score it against the true labels."""
    clusterer = PersonClusterer(distance_max=distance_max, min_cluster_size=1)
    # face_index carries the position in `vectors`/`labels` so cluster members
    # can be mapped back to their true label without parsing filenames.
    faces = [
        FaceRef(image_path=f"face_{i}.jpg", face_index=i, vector=v)
        for i, v in enumerate(vectors)
    ]
    try:
        clusters = clusterer.cluster(faces)
    except Exception as exc:  # noqa: BLE001
        print(f"    - clustering failed: {exc}")
        return {}

    assigned: dict[int, int] = {}
    for cid, cluster in enumerate(clusters):
        for member in cluster.faces:
            assigned[member.face_index] = cid

    if not assigned:
        return {}

    # Purity: how often the majority true label dominates each cluster.
    by_cluster: dict[int, list[str]] = {}
    for idx, cid in assigned.items():
        by_cluster.setdefault(cid, []).append(labels[idx])
    purity = sum(
        max(members.count(l) for l in set(members)) for members in by_cluster.values()
    ) / len(assigned)

    return {
        "clusters_found": float(len(by_cluster)),
        "true_people": float(len(set(labels))),
        "purity": purity,
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report(name: str, vectors, labels, elapsed: float, labelled: bool) -> None:
    print(f"\n=== {name} ===")
    if not vectors:
        print("  no faces embedded -- nothing to report")
        return

    per_face = elapsed / len(vectors)
    print(f"  faces embedded : {len(vectors)}")
    print(f"  embedding dim  : {len(vectors[0])}")
    print(f"  speed          : {per_face * 1000:.1f} ms/face "
          f"({per_face * 20000 / 60:.1f} min per 20,000 faces)")

    if not labelled or len(set(labels)) < 2:
        norms = [float(np.linalg.norm(v)) for v in vectors]
        print(f"  vector norms   : mean {np.mean(norms):.3f}")
        print("  (unlabelled: no accuracy figures -- use --labelled for those)")
        return

    same, diff = pair_distances(vectors, labels)
    if not same or not diff:
        print("  need at least 2 people with 2+ photos each for accuracy figures")
        return

    t, acc = best_threshold(same, diff)
    gap = float(np.mean(diff) - np.mean(same))
    print(f"  same-person    : mean {np.mean(same):.3f}  (max {max(same):.3f})")
    print(f"  different      : mean {np.mean(diff):.3f}  (min {min(diff):.3f})")
    print(f"  SEPARATION     : {gap:.3f}   <-- bigger is better")
    print(f"  best threshold : {t:.3f}  -> {acc * 100:.1f}% pair accuracy")
    print(f"                   (put this in DISTANCE_MAX_BY_BACKEND['{name}'])")

    quality = cluster_quality(vectors, labels, t)
    if quality:
        print(f"  clustering     : found {int(quality['clusters_found'])} people "
              f"vs {int(quality['true_people'])} actual, "
              f"purity {quality['purity'] * 100:.1f}%")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--labelled", type=Path,
                       help="Folder with one subfolder per person (gives accuracy).")
    group.add_argument("--folder", type=Path,
                       help="Flat/unlabelled folder (speed + distance spread only).")
    parser.add_argument("--backends", nargs="*", default=None,
                        help="Subset to test, e.g. --backends sface")
    parser.add_argument("--limit", type=int, default=0,
                        help="Cap the number of images (handy for a quick pass).")
    args = parser.parse_args()

    root = args.labelled or args.folder
    if not root.is_dir():
        print(f"Not a directory: {root}")
        return 2

    labelled = args.labelled is not None
    items = collect_labelled(root) if labelled else collect_flat(root)
    if args.limit:
        items = items[: args.limit]
    if not items:
        print(f"No images found under {root}")
        return 2

    print(f"Images: {len(items)}"
          + (f" across {len({l for _, l in items})} people" if labelled else ""))

    print("Building backends...")
    backends = available_backends(args.backends)
    if not backends:
        print("No embedding backends available -- nothing to compare.")
        return 1

    config = load_config()
    detector = FaceDetector.from_config(config)

    for name, backend in backends.items():
        print(f"\nRunning {name}...")
        vectors, labels, elapsed = embed_all(backend, items, detector, config)
        report(name, vectors, labels, elapsed, labelled)

    print("\nDone. Compare SEPARATION and clustering purity between backends: if "
          "SFace is close to ArcFace on your photos, the licensing blocker is "
          "solved for free.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
