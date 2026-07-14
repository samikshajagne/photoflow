"""
Phase 2 integration tests: person-aware album generation.

The real InsightFace model can't run here, so a fake face detector + fake
embedder inject deterministic identities, exercising the full pipeline:
folder -> detect -> embed -> cluster -> label -> identity-aware story -> manifest.
Also covers label persistence across runs, embedding caching, and the
no-identity regression (Phase 1 behavior preserved).
"""

import os
from pathlib import Path

import cv2
import numpy as np

from core.album.orchestrator import AlbumOrchestrator
from core.album.project import (
    AlbumProject,
    ROLE_BRIDE,
    ROLE_GROOM,
    ROLE_MOTHER,
    SIDE_BRIDE,
)
from core.face_detector import FaceResult
from core.face_embedder import FaceEmbedding
from core.person_cluster import PersonClusterer

BRIDE = np.array([1.0, 0.0, 0.0], np.float32)
GROOM = np.array([0.0, 1.0, 0.0], np.float32)
MOM = np.array([0.0, 0.0, 1.0], np.float32)

# Which identities appear in each photo (by file name).
SCENE = {
    "couple.png": [BRIDE, GROOM],
    "bride1.png": [BRIDE],
    "groom1.png": [GROOM],
    "bridemom.png": [BRIDE, MOM],
}


class FakeDetector:
    def detect(self, image_path):
        name = Path(image_path).name
        n = len(SCENE.get(name, []))
        regions = tuple((0.0, 0.0, 1.0, 1.0) for _ in range(n))
        return FaceResult(str(image_path), face_count=n, faces_detected=n > 0, regions=regions)


class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, image_path, regions):
        self.calls += 1
        name = Path(image_path).name
        vecs = SCENE.get(name, [])
        return [FaceEmbedding(str(image_path), i, v) for i, v in enumerate(vecs)]


def _distinct(path, kind):
    # Sharp, distinct noise so every fixture photo is a usable, non-duplicate
    # candidate (a smooth image would be flagged unusable and dropped).
    img = np.random.RandomState(kind + 1).randint(0, 256, (128, 128), np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


def _folder(tmp_path) -> Path:
    src = tmp_path / "shoot"
    src.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(SCENE):
        _distinct(src / name, i)
    return src


def _orch(face_detector=None, embedder=None, **kw):
    return AlbumOrchestrator(
        face_detector=face_detector or FakeDetector(),
        embedder=embedder or FakeEmbedder(),
        clusterer=PersonClusterer(distance_max=0.3),
        **kw,
    )


def _cluster_id_containing(project, name):
    for c in project.clusters:
        if any(Path(p).name == name for p in c.photos):
            return c.cluster_id
    raise AssertionError(f"no cluster contains {name}")


def _label_principals(project):
    """Label bride/groom/mother clusters using their member photos."""
    bride_id = _cluster_id_containing(project, "bride1.png")
    groom_id = _cluster_id_containing(project, "groom1.png")
    # The mother cluster is the one whose only photo is bridemom.png.
    mom_id = next(
        c.cluster_id
        for c in project.clusters
        if [Path(p).name for p in c.photos] == ["bridemom.png"]
    )
    project.label_cluster(bride_id, ROLE_BRIDE)
    project.label_cluster(groom_id, ROLE_GROOM)
    project.label_cluster(mom_id, ROLE_MOTHER, side=SIDE_BRIDE)


# --------------------------------------------------------------------------- #
# Clustering + labelling + identity-aware story (end to end)
# --------------------------------------------------------------------------- #
def test_clusters_discovered_unlabeled_first(tmp_path):
    src = _folder(tmp_path)
    out = tmp_path / "album"
    project = _orch().generate(src, output_dir=out)

    # Three people discovered; nothing labelled yet -> Phase 1 story.
    assert len(project.clusters) == 3
    assert not project.has_identity()
    assert "Couple" not in {s.name for s in project.sections}


def test_label_then_regenerate_produces_person_sections(tmp_path):
    src = _folder(tmp_path)
    out = tmp_path / "album"

    first = _orch().generate(src, output_dir=out)
    _label_principals(first)
    first.save(out)  # persist labels

    # Re-generate: labels re-bind by centroid and drive the story.
    project = _orch().generate(src, output_dir=out)
    assert project.has_identity()

    sections = {s.name: [Path(p).name for p in s.photos] for s in project.sections}
    assert {"Couple", "Bride", "Groom", "Bride Family", "Close Family"} <= set(sections)
    assert sections["Couple"] == ["couple.png"]
    assert sections["Bride"] == ["bride1.png"]
    assert sections["Groom"] == ["groom1.png"]
    assert "bridemom.png" in sections["Bride Family"]
    assert "bridemom.png" in sections["Close Family"]
    # No groom-side family was labelled, so that sheet is absent.
    assert "Groom Family" not in sections


def test_label_propagates_to_all_cluster_photos(tmp_path):
    src = _folder(tmp_path)
    project = _orch().generate(src, output_dir=tmp_path / "album")
    bride_id = _cluster_id_containing(project, "bride1.png")
    project.label_cluster(bride_id, ROLE_BRIDE)

    # Every photo in the bride cluster now carries the 'bride' token.
    for rec in project.photos.values():
        if Path(rec.source_path).name in ("couple.png", "bride1.png", "bridemom.png"):
            assert ROLE_BRIDE in rec.persons


def test_identity_persists_across_reload(tmp_path):
    src = _folder(tmp_path)
    out = tmp_path / "album"
    project = _orch().generate(src, output_dir=out)
    _label_principals(project)
    project.save(out)

    reloaded = AlbumProject.load(out)
    assert reloaded.has_identity()
    labels = {c.label for c in reloaded.clusters if c.label}
    assert {ROLE_BRIDE, ROLE_GROOM, ROLE_MOTHER} <= labels


# --------------------------------------------------------------------------- #
# People-first flow (prepare_people before generate)
# --------------------------------------------------------------------------- #
def test_prepare_people_discovers_clusters_without_layout(tmp_path):
    src = _folder(tmp_path)
    out = tmp_path / "album"
    project = _orch().prepare_people(src, output_dir=out)

    # People discovered, but the album layout is NOT built yet.
    assert len(project.clusters) == 3
    assert project.sections == []
    assert project.spreads == []
    # Manifest persisted so labels set now round-trip into generate().
    assert (out / "album_manifest.json").is_file()


def test_prepare_people_labels_flow_into_generate(tmp_path):
    src = _folder(tmp_path)
    out = tmp_path / "album"

    # First interactive step: cluster people, label them, persist.
    prepared = _orch().prepare_people(src, output_dir=out)
    _label_principals(prepared)
    prepared.save(out)

    # Then build the album: labels re-bind by centroid and drive the story.
    project = _orch().generate(src, output_dir=out)
    assert project.has_identity()
    assert {"Couple", "Bride", "Groom"} <= {s.name for s in project.sections}


def test_prepare_then_generate_reuses_embeddings(tmp_path):
    src = _folder(tmp_path)
    out = tmp_path / "album"

    e1 = FakeEmbedder()
    _orch(embedder=e1).prepare_people(src, output_dir=out)
    assert e1.calls >= 1  # embedded during the people pass

    # Building the album afterwards must not re-embed — shared cache.
    e2 = FakeEmbedder()
    _orch(embedder=e2).generate(src, output_dir=out)
    assert e2.calls == 0


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def test_embeddings_are_cached(tmp_path):
    src = _folder(tmp_path)
    out = tmp_path / "album"

    e1 = FakeEmbedder()
    _orch(embedder=e1).generate(src, output_dir=out)
    assert e1.calls >= 1  # embedded the candidate photos with faces

    e2 = FakeEmbedder()
    _orch(embedder=e2).generate(src, output_dir=out)
    assert e2.calls == 0  # all embeddings served from cache


# --------------------------------------------------------------------------- #
# No-identity regression (Phase 1 preserved)
# --------------------------------------------------------------------------- #
def test_identity_disabled_is_phase1(tmp_path):
    src = _folder(tmp_path)
    project = AlbumOrchestrator(enable_identity=False).generate(
        src, output_dir=tmp_path / "album"
    )
    assert project.clusters == []
    assert not project.has_identity()
    names = {s.name for s in project.sections}
    assert "Ceremony" in names           # Phase 1 backbone present
    assert "Couple" not in names         # no person sections
