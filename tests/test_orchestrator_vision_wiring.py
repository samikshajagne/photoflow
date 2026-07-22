"""
Wiring tests for the Vision Brain integration in the album orchestrator.

Exercises the two methods added by the live wiring — `_faces_by_path` (prefers
brain boxes + landmark-derived boxes, falls back to cached MediaPipe faces) and
`_run_vision_brain` (graceful cache-backed brain run) — without the heavy
pipeline / network.
"""

from __future__ import annotations

import types

from core.album.orchestrator import AlbumOrchestrator
from core.vision_brain import PhotoBrain


def _cand(path):
    return types.SimpleNamespace(source_path=path)


class _Cache:
    def __init__(self, faces=None):
        self._faces = faces or {}

    def get(self, ns, path):
        return self._faces.get(path) if ns == "faces" else None


# --------------------------------------------------------------------------- #
# _faces_by_path
# --------------------------------------------------------------------------- #
def test_prefers_landmark_box_when_landmarks_present():
    # A face with 5 landmarks -> box comes from face_box_from_landmarks, not the
    # raw brain box.
    lm = [(0.45, 0.30), (0.55, 0.30), (0.50, 0.36), (0.46, 0.42), (0.54, 0.42)]
    pb = PhotoBrain(path="a.jpg", face_count=1, face_boxes=[(0.4, 0.25, 0.2, 0.25)],
                    face_landmarks=[lm])
    faces = AlbumOrchestrator._faces_by_path([_cand("a.jpg")], _Cache(), {"a.jpg": pb})
    assert "a.jpg" in faces
    box = faces["a.jpg"][0]
    # Landmark box is centered on the eye midpoint (0.5) and taller than the raw box.
    x, y, w, h = box
    assert abs((x + w / 2) - 0.5) < 1e-6
    assert y < 0.30 and (y + h) > 0.42  # crown above eyes, chin below mouth


def test_uses_brain_box_when_no_landmarks():
    pb = PhotoBrain(path="a.jpg", face_count=1, face_boxes=[(0.4, 0.25, 0.2, 0.25)],
                    face_landmarks=[])
    faces = AlbumOrchestrator._faces_by_path([_cand("a.jpg")], _Cache(), {"a.jpg": pb})
    assert faces["a.jpg"] == ((0.4, 0.25, 0.2, 0.25),)


def test_falls_back_to_cached_faces_without_brain():
    cache = _Cache(faces={"a.jpg": [[0.1, 0.2, 0.3, 0.4]]})
    faces = AlbumOrchestrator._faces_by_path([_cand("a.jpg")], cache, {})
    assert faces["a.jpg"] == ((0.1, 0.2, 0.3, 0.4),)


def test_omits_photos_with_no_faces_anywhere():
    faces = AlbumOrchestrator._faces_by_path([_cand("a.jpg")], _Cache(), {})
    assert faces == {}


def test_brain_takes_precedence_over_cache():
    # Brain has a face; the stale cache face should be ignored.
    pb = PhotoBrain(path="a.jpg", face_count=1, face_boxes=[(0.5, 0.5, 0.1, 0.1)], face_landmarks=[])
    cache = _Cache(faces={"a.jpg": [[0.0, 0.0, 0.9, 0.9]]})
    faces = AlbumOrchestrator._faces_by_path([_cand("a.jpg")], cache, {"a.jpg": pb})
    assert faces["a.jpg"] == ((0.5, 0.5, 0.1, 0.1),)


# --------------------------------------------------------------------------- #
# _run_vision_brain
# --------------------------------------------------------------------------- #
def _orchestrator():
    return AlbumOrchestrator(enable_identity=False)


def test_run_vision_brain_returns_cached_brains(monkeypatch):
    import core.brain_stage as bs

    canned = {"a.jpg": PhotoBrain(path="a.jpg", scene_labels=["haldi"])}
    monkeypatch.setattr(bs, "analyze_and_cache", lambda paths, cache, **kw: canned)

    orch = _orchestrator()
    out = orch._run_vision_brain([_cand("a.jpg")], _Cache())
    assert out is canned


def test_run_vision_brain_graceful_on_failure(monkeypatch):
    import core.brain_stage as bs

    def boom(paths, cache, **kw):
        raise RuntimeError("vision exploded")

    monkeypatch.setattr(bs, "analyze_and_cache", boom)
    orch = _orchestrator()
    assert orch._run_vision_brain([_cand("a.jpg")], _Cache()) == {}


def test_run_vision_brain_empty_paths():
    orch = _orchestrator()
    assert orch._run_vision_brain([], _Cache()) == {}
