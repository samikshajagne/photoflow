"""
Integration tests for quality scoring inside the pipeline.

Covers two things:

1. ``PhotoFlowPipeline._rerank_representatives`` — choosing each duplicate
   group's representative by highest quality (unit-tested directly).
2. End-to-end: a degraded near-duplicate and a pristine original group
   together; the pristine one is kept (routed to Review) while the degraded one
   is routed to Duplicates, proving quality overrode the default
   lexicographic-first choice. These synthetic images have no faces, so neither
   reaches BestShots (a face-less frame caps at the quality floor); BestShots
   selection itself is covered in test_pipeline_faces.py.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.organizer import FOLDER_BEST_SHOTS, FOLDER_DUPLICATES, FOLDER_REVIEW
from core.pipeline import PhotoFlowPipeline
from tests.conftest import StubFaceDetector
from utils.config import load_config


def _pipeline() -> PhotoFlowPipeline:
    """
    Pipeline with a stub face detector that succeeds and finds no faces.

    These fixtures are synthetic (checkerboards/gradients) so a real detector
    finds nothing in them anyway -- but only if it *runs*. Without MediaPipe
    installed, detection instead *fails* for every image, which makes the
    pipeline bypass face scoring altogether and changes the resulting
    categories. Injecting the stub keeps these assertions true on any machine.
    See ``tests/conftest.py::StubFaceDetector``.
    """
    pipe = PhotoFlowPipeline.from_config(load_config())
    pipe.face_detector = StubFaceDetector()
    return pipe


# --------------------------------------------------------------------------- #
# Representative re-ranking (unit)
# --------------------------------------------------------------------------- #
def test_rerank_picks_highest_quality():
    pipe = _pipeline()
    dup = {"groups": [{"representative": "/imgs/a.png", "duplicates": ["/imgs/b.png"]}]}
    quality = {
        pipe._normalize("/imgs/a.png"): 40.0,
        pipe._normalize("/imgs/b.png"): 90.0,  # higher quality
    }

    ranked = pipe._rerank_representatives(dup, quality)

    group = ranked["groups"][0]
    assert group["representative"] == "/imgs/b.png"
    assert group["duplicates"] == ["/imgs/a.png"]


def test_rerank_breaks_ties_lexicographically():
    pipe = _pipeline()
    dup = {"groups": [{"representative": "/imgs/z.png", "duplicates": ["/imgs/a.png"]}]}
    quality = {
        pipe._normalize("/imgs/z.png"): 50.0,
        pipe._normalize("/imgs/a.png"): 50.0,  # tie -> smallest path wins
    }

    ranked = pipe._rerank_representatives(dup, quality)

    assert ranked["groups"][0]["representative"] == "/imgs/a.png"


def test_rerank_sorts_unscored_members_last():
    pipe = _pipeline()
    dup = {"groups": [{"representative": "/imgs/a.png", "duplicates": ["/imgs/b.png"]}]}
    # Only a.png has a score; b.png (unscored) must not become representative.
    quality = {pipe._normalize("/imgs/a.png"): 10.0}

    ranked = pipe._rerank_representatives(dup, quality)

    assert ranked["groups"][0]["representative"] == "/imgs/a.png"


def test_select_best_shots_applies_floor():
    pipe = _pipeline()
    imgs = [Path(f"/imgs/{c}.png") for c in "abcde"]
    # 75 is the inclusive floor; 74.9 is just below it.
    scores = [95.0, 85.0, 75.0, 74.9, 60.0]
    quality = {pipe._normalize(p): s for p, s in zip(imgs, scores)}
    usable = {pipe._normalize(p): True for p in imgs}

    selected = pipe._select_best_shots(imgs, quality, usable, set())

    # Only the three at/above the 75 floor qualify; 74.9 and 60 are dropped.
    assert len(selected) == 3
    assert not any(s.endswith("d.png") or s.endswith("e.png") for s in selected)
    # Ordered best-first.
    assert selected[0].endswith("a.png")


def test_select_best_shots_excludes_duplicates_and_unusable():
    pipe = _pipeline()
    a, b, c = (Path(f"/imgs/{name}.png") for name in ("a", "b", "c"))
    quality = {pipe._normalize(p): 90.0 for p in (a, b, c)}
    usable = {pipe._normalize(a): True, pipe._normalize(b): False, pipe._normalize(c): True}
    dup_paths = {pipe._normalize(a)}  # a is a non-representative duplicate

    selected = pipe._select_best_shots([a, b, c], quality, usable, dup_paths)

    # a excluded (duplicate), b excluded (unusable) -> only c remains.
    assert len(selected) == 1
    assert selected[0].endswith("c.png")


def test_select_best_shots_weak_shoot_is_not_padded():
    # Many photos, but only a handful are good. No minimum cap pads the set:
    # BestShots is exactly the above-floor photos, however few.
    pipe = _pipeline()
    imgs = [Path(f"/imgs/{i:03d}.png") for i in range(900)]
    quality = {
        pipe._normalize(p): (90.0 if i < 5 else 50.0) for i, p in enumerate(imgs)
    }
    usable = {pipe._normalize(p): True for p in imgs}

    selected = pipe._select_best_shots(imgs, quality, usable, set())

    assert len(selected) == 5


def test_select_best_shots_great_shoot_keeps_all_above_floor():
    # A strong shoot: every above-floor photo is kept -- no top-% or max cap
    # trims excellent photos.
    pipe = _pipeline()
    imgs = [Path(f"/imgs/{i:05d}.png") for i in range(900)]
    # 200 excellent (>=75), the rest below the floor.
    quality = {
        pipe._normalize(p): (95.0 if i < 200 else 40.0) for i, p in enumerate(imgs)
    }
    usable = {pipe._normalize(p): True for p in imgs}

    selected = pipe._select_best_shots(imgs, quality, usable, set())

    assert len(selected) == 200


def test_rerank_does_not_mutate_input():
    pipe = _pipeline()
    dup = {"groups": [{"representative": "/imgs/a.png", "duplicates": ["/imgs/b.png"]}]}
    pipe._rerank_representatives(dup, {pipe._normalize("/imgs/b.png"): 99.0})

    assert dup["groups"][0]["representative"] == "/imgs/a.png"


# --------------------------------------------------------------------------- #
# End-to-end
# --------------------------------------------------------------------------- #
def _checkerboard(size: int = 128, square: int = 8) -> np.ndarray:
    rows = []
    for r in range(size // square):
        row = [(0 if (r + c) % 2 else 255) for c in range(size // square)]
        rows.append(np.repeat(row, square))
    return np.repeat(np.array(rows, dtype=np.uint8), square, axis=0)[:size, :size]


def _build_quality_pair(tmp_path: Path) -> Path:
    """
    Folder with two near-duplicates: a lexicographically-FIRST degraded image
    and a lexicographically-LATER pristine one (higher quality).
    """
    src = tmp_path / "photos"
    src.mkdir(parents=True, exist_ok=True)
    checker = _checkerboard()
    # 'a_' sorts first but is blurrier -> lower quality.
    cv2.imwrite(str(src / "a_degraded.png"), cv2.GaussianBlur(checker, (5, 5), 0))
    # 'b_' sorts later but is pristine -> higher quality.
    cv2.imwrite(str(src / "b_pristine.png"), checker)
    return src


def test_quality_selects_best_representative_over_lexicographic(tmp_path: Path):
    src = _build_quality_pair(tmp_path)
    dest = tmp_path / "out"

    result = _pipeline().run(input_folder=src, destination_root=dest)

    output_root = Path(result.output_root)
    duplicates = {p.name for p in (output_root / FOLDER_DUPLICATES).iterdir()}
    review = {p.name for p in (output_root / FOLDER_REVIEW).iterdir()}

    # The pristine image is kept as the group representative (-> Review, since
    # it has no face to clear the BestShots floor); the degraded near-duplicate
    # is the duplicate. (Lexicographic selection would have kept 'a_degraded'.)
    assert duplicates == {"a_degraded.png"}
    assert "b_pristine.png" in review


def test_representative_is_the_pristine_image(tmp_path: Path):
    src = _build_quality_pair(tmp_path)

    result = _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    # Quality re-ranking keeps the pristine image as the kept member of the
    # group; the degraded copy is the duplicate. No face here, so it is not a
    # BestShot, but it must not be the discarded duplicate either.
    output_root = Path(result.output_root)
    duplicates = {p.name for p in (output_root / FOLDER_DUPLICATES).iterdir()}
    assert duplicates == {"a_degraded.png"}
    assert result.best_shot_candidates == ()


def test_quality_results_are_populated(tmp_path: Path):
    src = _build_quality_pair(tmp_path)

    result = _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    assert len(result.quality_results) == 2
    for q in result.quality_results:
        assert 0.0 <= q.quality_score <= 100.0


def test_unique_faceless_image_is_not_a_best_shot(tmp_path: Path):
    # A single unique image with no face cannot clear the BestShots quality
    # floor under the default weights, so it is not selected (it is a usable
    # Review photo). Unique *high-quality* selection is covered with a face in
    # test_pipeline_faces.py.
    src = tmp_path / "photos"
    src.mkdir()
    cv2.imwrite(str(src / "only.png"), _checkerboard())

    result = _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    assert result.best_shot_candidates == ()
    assert len(result.quality_results) == 1


def test_quality_runs_in_dry_run_without_writing(tmp_path: Path):
    src = _build_quality_pair(tmp_path)

    result = _pipeline().run(input_folder=src, dry_run=True)

    assert result.dry_run is True
    assert result.output_root is None
    assert len(result.quality_results) == 2
    # No faces -> no BestShots in this fixture; quality still computed for both.
    assert result.best_shot_candidates == ()
    assert not (src / "PhotoFlow_Output").exists()
