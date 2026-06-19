"""
Integration tests for face detection inside the pipeline.

Real MediaPipe faces are hard to synthesize deterministically, so these
tests inject a fake face detector (the pipeline supports component
injection). That lets us prove, end to end, that:

1. face presence raises an image's quality score, and
2. best-shot selection within a duplicate group prefers the image with a
   face when other quality signals tie,

without depending on the MediaPipe model. Face-stage failures are also shown
to be non-fatal.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.blur_detector import BlurDetector
from core.duplicate_detector import DuplicateDetector
from core.face_detector import FaceDetectionError, FaceResult
from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
    PhotoOrganizer,
)
from core.pipeline import PhotoFlowPipeline
from core.quality_scorer import QualityScorer
from core.scanner import ImageScanner
from utils.config import load_config


class FakeFaceDetector:
    """Reports faces based on a per-path callable; optionally raises."""

    def __init__(self, face_for=None, raises: bool = False):
        self._face_for = face_for or (lambda p: 0)
        self._raises = raises

    def detect(self, image_path) -> FaceResult:
        if self._raises:
            raise FaceDetectionError(f"forced failure for {image_path}")
        count = self._face_for(str(image_path))
        return FaceResult(
            image_path=str(image_path), face_count=count, faces_detected=count > 0
        )


def _pipeline(face_detector) -> PhotoFlowPipeline:
    config = load_config()
    return PhotoFlowPipeline(
        scanner=ImageScanner.from_config(config),
        duplicate_detector=DuplicateDetector.from_config(config),
        blur_detector=BlurDetector.from_config(config),
        organizer=PhotoOrganizer.from_config(config),
        quality_scorer=QualityScorer.from_config(config),
        face_detector=face_detector,
    )


def _checkerboard(size: int = 128, square: int = 8) -> np.ndarray:
    rows = []
    for r in range(size // square):
        row = [(0 if (r + c) % 2 else 255) for c in range(size // square)]
        rows.append(np.repeat(row, square))
    return np.repeat(np.array(rows, dtype=np.uint8), square, axis=0)[:size, :size]


def _one_image(tmp_path: Path, name: str = "a.png") -> Path:
    src = tmp_path / "photos"
    src.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(src / name), _checkerboard())
    return src


def test_face_raises_quality_through_pipeline(tmp_path: Path):
    src = _one_image(tmp_path)

    face_run = _pipeline(FakeFaceDetector(face_for=lambda p: 1)).run(
        input_folder=src, destination_root=tmp_path / "o1"
    )
    noface_run = _pipeline(FakeFaceDetector(face_for=lambda p: 0)).run(
        input_folder=src, destination_root=tmp_path / "o2"
    )

    assert face_run.faces_detected_count == 1
    assert noface_run.faces_detected_count == 0
    assert face_run.quality_results[0].faces_detected is True
    # Same pixels; the only difference is the detected face.
    assert face_run.quality_results[0].quality_score > noface_run.quality_results[0].quality_score


def test_best_shot_prefers_the_image_with_a_face(tmp_path: Path):
    # Two exact copies => a duplicate group with identical blur/exposure.
    src = tmp_path / "photos"
    src.mkdir(parents=True, exist_ok=True)
    a = src / "a_first.png"
    cv2.imwrite(str(a), _checkerboard())
    b = src / "b_second.png"
    b.write_bytes(a.read_bytes())

    # Only the lexicographically-later copy has a face.
    face_for = lambda p: 1 if p.endswith("b_second.png") else 0
    result = _pipeline(FakeFaceDetector(face_for=face_for)).run(
        input_folder=src, destination_root=tmp_path / "out"
    )

    # The face-bearing copy is kept as the best shot (representative -> Review),
    # overriding the default lexicographic-first choice (a_first).
    assert len(result.best_shot_candidates) == 1
    assert result.best_shot_candidates[0].endswith("b_second.png")

    output_root = Path(result.output_root)
    best = {p.name for p in (output_root / FOLDER_BEST_SHOTS).iterdir()}
    duplicates = {p.name for p in (output_root / FOLDER_DUPLICATES).iterdir()}
    # The face-bearing copy is kept as the best shot; the other is a duplicate.
    assert best == {"b_second.png"}
    assert duplicates == {"a_first.png"}


def test_unique_high_quality_image_with_face_reaches_best_shots(tmp_path: Path):
    # A single UNIQUE image (no duplicates) with a face and strong quality must
    # be eligible for BestShots under the redesign (Goal 4).
    src = _one_image(tmp_path, "solo.png")

    result = _pipeline(FakeFaceDetector(face_for=lambda p: 1)).run(
        input_folder=src, destination_root=tmp_path / "out"
    )

    assert len(result.best_shot_candidates) == 1
    assert result.best_shot_candidates[0].endswith("solo.png")
    best = {p.name for p in (Path(result.output_root) / FOLDER_BEST_SHOTS).iterdir()}
    assert best == {"solo.png"}


def test_face_stage_failure_is_nonfatal(tmp_path: Path):
    src = _one_image(tmp_path, "a.png")

    result = _pipeline(FakeFaceDetector(raises=True)).run(
        input_folder=src, destination_root=tmp_path / "out"
    )

    # The failure is recorded; the run still completes and the image is scored
    # (as having no face) and organized.
    assert len(result.face_failures) == 1
    assert result.faces_detected_count == 0
    assert len(result.quality_results) == 1
    review = Path(result.output_root) / FOLDER_REVIEW
    assert "a.png" in {p.name for p in review.iterdir()}


def test_faces_detected_count_counts_only_face_images(tmp_path: Path):
    src = tmp_path / "photos"
    src.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(src / "with_face.png"), _checkerboard(square=8))
    cv2.imwrite(str(src / "no_face.png"), _checkerboard(square=16))

    face_for = lambda p: 3 if p.endswith("with_face.png") else 0
    result = _pipeline(FakeFaceDetector(face_for=face_for)).run(
        input_folder=src, destination_root=tmp_path / "out", dry_run=True
    )

    assert result.faces_detected_count == 1
