"""
End-to-end integration tests for the Album Orchestrator.

Validates the full Phase 1 deliverable: folder -> AlbumProject -> manifest,
using the real engines (face detection has no model in this environment, so the
album degrades to time/quality sections, which is exactly the intended
graceful behavior). Also covers caching and sticky overrides.
"""

from pathlib import Path

import cv2
import numpy as np

from core.album.orchestrator import AlbumOrchestrator
from core.album.project import AlbumProject
from core.auto_edit import EditRecipe
from core.organizer import FOLDER_BLURRY, FOLDER_DUPLICATES, FOLDER_BEST_SHOTS, FOLDER_REVIEW
from core.pipeline import PhotoFlowPipeline
from utils.config import load_config


def _checker(size=128, square=8):
    rows = []
    for r in range(size // square):
        row = [(0 if (r + c) % 2 else 255) for c in range(size // square)]
        rows.append(np.repeat(row, square))
    return np.repeat(np.array(rows, dtype=np.uint8), square, axis=0)[:size, :size]


def _gradient(size=128):
    return np.tile(np.linspace(0, 255, size, dtype=np.uint8), (size, 1))


def _build_folder(tmp_path: Path) -> Path:
    src = tmp_path / "shoot"
    src.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(src / "a_sharp.png"), _checker(square=8))
    cv2.imwrite(str(src / "b_sharp.png"), _checker(square=16))
    cv2.imwrite(str(src / "c_noise.png"), np.random.RandomState(0).randint(0, 256, (128, 128), np.uint8))
    # Exact duplicate of a_sharp.
    (src / "d_copy.png").write_bytes((src / "a_sharp.png").read_bytes())
    # Smooth gradient -> clearly unusable.
    cv2.imwrite(str(src / "z_blur.png"), _gradient())
    return src


class CountingPipeline:
    """Wraps a real pipeline and counts run() calls."""

    def __init__(self, real):
        self._real = real
        self.runs = 0

    def run(self, folder, dry_run=False, cache=None):
        self.runs += 1
        return self._real.run(folder, dry_run=dry_run, cache=cache)


class CountingEditor:
    """Stand-in AutoEditor that counts analyze() calls."""

    def __init__(self):
        self.calls = 0

    def analyze(self, image_path, face_regions=()):
        self.calls += 1
        return EditRecipe.identity()


# --------------------------------------------------------------------------- #
# End-to-end
# --------------------------------------------------------------------------- #
def test_folder_to_manifest_end_to_end(tmp_path):
    src = _build_folder(tmp_path)
    out = tmp_path / "album"

    project = AlbumOrchestrator().generate(src, output_dir=out)

    # Manifest written and reloadable, round-tripping exactly.
    manifest = out / "album_manifest.json"
    assert manifest.is_file()
    assert AlbumProject.load(out).to_dict() == project.to_dict()

    # Categories: the gradient is unusable; the exact copy is a duplicate.
    assert project.get(str(src / "z_blur.png")).category == FOLDER_BLURRY
    categories = {p.category for p in project.photos.values()}
    assert FOLDER_DUPLICATES in categories

    # Candidate pool excludes duplicates and blurry.
    pool = {Path(r.source_path).name for r in project.candidate_pool()}
    assert "z_blur.png" not in pool
    assert all(
        r.category in (FOLDER_BEST_SHOTS, FOLDER_REVIEW) for r in project.candidate_pool()
    )

    # Story + layout produced content; Ceremony is always present with candidates.
    section_names = [s.name for s in project.sections]
    assert "Ceremony" in section_names
    assert len(project.spreads) >= 1

    # Auto-edit recipes attached to every candidate.
    assert all(r.edit_recipe is not None for r in project.candidate_pool())

    # Each spread is tagged with a real section name.
    valid_sections = set(section_names)
    assert all(s.section in valid_sections for s in project.spreads)


def test_determinism(tmp_path):
    src = _build_folder(tmp_path)
    p1 = AlbumOrchestrator().generate(src, output_dir=tmp_path / "o1").to_dict()
    p2 = AlbumOrchestrator().generate(src, output_dir=tmp_path / "o2").to_dict()
    # Manifests differ only by metadata timestamp; compare the substantive parts.
    for key in ("photos", "sections", "spreads"):
        assert p1[key] == p2[key]


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def test_cache_avoids_recompute_on_second_run(tmp_path):
    src = _build_folder(tmp_path)
    out = tmp_path / "album"

    pipe1 = CountingPipeline(PhotoFlowPipeline.from_config(load_config()))
    editor1 = CountingEditor()
    AlbumOrchestrator(pipeline=pipe1, auto_editor=editor1).generate(src, output_dir=out)
    assert pipe1.runs == 1
    assert editor1.calls >= 1  # analyzed the candidates the first time

    # Second run, fresh spies, same output dir (cache on disk) -> no recompute.
    pipe2 = CountingPipeline(PhotoFlowPipeline.from_config(load_config()))
    editor2 = CountingEditor()
    AlbumOrchestrator(pipeline=pipe2, auto_editor=editor2).generate(src, output_dir=out)
    assert pipe2.runs == 0     # quality came from cache
    assert editor2.calls == 0  # edit recipes came from cache


def test_reanalyze_forces_pipeline(tmp_path):
    src = _build_folder(tmp_path)
    out = tmp_path / "album"
    AlbumOrchestrator().generate(src, output_dir=out)

    pipe = CountingPipeline(PhotoFlowPipeline.from_config(load_config()))
    AlbumOrchestrator(pipeline=pipe).generate(src, output_dir=out, reanalyze=True)
    assert pipe.runs == 1


# --------------------------------------------------------------------------- #
# Overrides
# --------------------------------------------------------------------------- #
def test_override_moves_photo_and_persists(tmp_path):
    src = _build_folder(tmp_path)
    out = tmp_path / "album"

    # Force a normally-eligible photo into Blurry.
    target = str(src / "b_sharp.png")
    project = AlbumOrchestrator().generate(
        src, output_dir=out, overrides={target: FOLDER_BLURRY}
    )
    assert project.get(target).category == FOLDER_BLURRY
    assert target not in {r.source_path for r in project.candidate_pool()}
    assert project.overrides.get(target) == FOLDER_BLURRY

    # A later run with no explicit overrides recovers it from the manifest.
    project2 = AlbumOrchestrator().generate(src, output_dir=out)
    assert project2.get(target).category == FOLDER_BLURRY
    assert project2.overrides.get(target) == FOLDER_BLURRY
