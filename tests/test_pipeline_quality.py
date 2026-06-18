"""
Integration tests for quality scoring inside the pipeline.

Covers two things added by the quality milestone:

1. ``PhotoFlowPipeline._rerank_representatives`` — choosing each duplicate
   group's representative by highest quality (unit-tested directly).
2. End-to-end: a degraded near-duplicate and a pristine original group
   together; the pristine one must be kept (Review) while the degraded one is
   routed to Duplicates, proving quality overrode the default
   lexicographic-first choice.

Existing test files are left untouched.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.organizer import FOLDER_DUPLICATES, FOLDER_REVIEW
from core.pipeline import PhotoFlowPipeline
from utils.config import load_config


def _pipeline() -> PhotoFlowPipeline:
    return PhotoFlowPipeline.from_config(load_config())


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
    review = {p.name for p in (output_root / FOLDER_REVIEW).iterdir()}
    duplicates = {p.name for p in (output_root / FOLDER_DUPLICATES).iterdir()}

    # The pristine image is kept; the degraded near-duplicate is the duplicate.
    # (Default lexicographic selection would have kept 'a_degraded' instead.)
    assert review == {"b_pristine.png"}
    assert duplicates == {"a_degraded.png"}


def test_best_shot_candidate_is_the_pristine_image(tmp_path: Path):
    src = _build_quality_pair(tmp_path)

    result = _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    assert len(result.best_shot_candidates) == 1
    assert result.best_shot_candidates[0].endswith("b_pristine.png")


def test_quality_results_are_populated(tmp_path: Path):
    src = _build_quality_pair(tmp_path)

    result = _pipeline().run(input_folder=src, destination_root=tmp_path / "out")

    assert len(result.quality_results) == 2
    for q in result.quality_results:
        assert 0.0 <= q.quality_score <= 100.0


def test_no_duplicates_means_no_best_shot_candidates(tmp_path: Path):
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
    assert len(result.best_shot_candidates) == 1
    assert not (src / "PhotoFlow_Output").exists()
