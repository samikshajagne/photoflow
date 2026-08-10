"""
Tests for the Vision Brain layer (OpenAI GPT-4o edition).

The OpenAI call (`VisionBrain._call_openai`) is mocked, so parsing/assembly is
verified with no network / no key. The local-fallback path is exercised by
forcing the API to fail. Face boxes come from the local detector, which is
injected as a stub here (MediaPipe isn't available in the sandbox).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from PIL import Image

from core.vision_brain import (
    SOURCE_LOCAL,
    SOURCE_OPENAI,
    PhotoBrain,
    VisionBrain,
)


class _StubDetector:
    """Stand-in for core.face_detector.FaceDetector.detect() -> result.regions."""

    def __init__(self, regions):
        self._regions = tuple(regions)

    def detect(self, path):
        class _R:
            pass

        r = _R()
        r.regions = self._regions
        r.face_count = len(self._regions)
        return r


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "img.jpg"
    Image.new("RGB", (1000, 800), (200, 180, 120)).save(p)
    return str(p)


_CANNED = (["ceremony", "mandap", "fire"], [0.95, 0.9, 0.8], [(240, 210, 60)], 4)


def test_available_reflects_api_key():
    assert VisionBrain(api_key="abc").available() is True
    assert VisionBrain(api_key="").available() is False


def test_openai_labels_and_colors(monkeypatch, photo):
    brain = VisionBrain(api_key="fake-key", detector=_StubDetector([]))
    monkeypatch.setattr(brain, "_call_openai", lambda path: _CANNED)

    pb = brain.analyze(photo)
    assert pb.source == SOURCE_OPENAI
    assert "ceremony" in pb.scene_labels
    assert pb.scene_confidence[0] == pytest.approx(0.95)
    assert pb.dominant_colors[0] == (240, 210, 60)
    # No local boxes -> face_count falls back to GPT's estimate.
    assert pb.face_count == 4


def test_face_boxes_come_from_local_detector(monkeypatch, photo):
    brain = VisionBrain(api_key="k", detector=_StubDetector([(0.4, 0.3, 0.2, 0.2)]))
    monkeypatch.setattr(brain, "_call_openai", lambda path: _CANNED)
    pb = brain.analyze(photo)
    assert pb.face_boxes == [(0.4, 0.3, 0.2, 0.2)]
    assert pb.face_count == 1  # local boxes take precedence over the GPT estimate


def test_falls_back_to_local_on_api_error(monkeypatch, photo):
    brain = VisionBrain(api_key="k", enable_fallback=True, detector=_StubDetector([]))
    monkeypatch.setattr(brain, "_call_openai", lambda path: (_ for _ in ()).throw(RuntimeError("down")))
    pb = brain.analyze(photo)
    assert pb.source == SOURCE_LOCAL
    assert pb.scene_labels == []          # no scene understanding without the API
    assert pb.dominant_colors            # local colour still computed


def test_api_error_reraised_when_fallback_disabled(monkeypatch, photo):
    brain = VisionBrain(api_key="k", enable_fallback=False, detector=_StubDetector([]))
    monkeypatch.setattr(brain, "_call_openai", lambda path: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        brain.analyze(photo)


def test_no_key_skips_api(monkeypatch, photo):
    brain = VisionBrain(api_key="", detector=_StubDetector([]))
    # _call_openai must never be reached without a key.
    monkeypatch.setattr(brain, "_call_openai", lambda p: (_ for _ in ()).throw(AssertionError("called")))
    pb = brain.analyze(photo)
    assert pb.source == SOURCE_LOCAL


def test_photobrain_json_roundtrip():
    pb = PhotoBrain(
        path="a.jpg",
        face_count=1,
        face_boxes=[(0.1, 0.2, 0.3, 0.4)],
        scene_labels=["haldi"],
        scene_confidence=[0.9],
        dominant_colors=[(240, 210, 60)],
        capture_time=datetime(2025, 2, 24, 10, 30, 0),
        source=SOURCE_OPENAI,
    )
    restored = PhotoBrain.from_dict(pb.to_dict())
    assert restored.face_boxes == pb.face_boxes
    assert restored.scene_labels == pb.scene_labels
    assert restored.dominant_colors == pb.dominant_colors
    assert restored.capture_time == pb.capture_time
    assert restored.source == SOURCE_OPENAI
