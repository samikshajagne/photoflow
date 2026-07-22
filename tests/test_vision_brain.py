"""
Tests for the Vision Brain layer (Component 1).

The Google Vision API call (`VisionBrain._annotate`) is mocked with a canned
response, so parsing is verified with no network / no key. The local-fallback
path is exercised by forcing the API to fail.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from PIL import Image

from core.vision_brain import (
    SOURCE_GOOGLE,
    SOURCE_LOCAL,
    PhotoBrain,
    VisionBrain,
)


# A canned Google Vision annotateImage response for a 1000x800 image:
# one joyful face at pixel box (300,150)-(500,450), with 5 landmarks, plus
# labels and dominant colours.
def _canned_response():
    return {
        "faceAnnotations": [
            {
                "fdBoundingPoly": {
                    "vertices": [
                        {"x": 300, "y": 150},
                        {"x": 500, "y": 150},
                        {"x": 500, "y": 450},
                        {"x": 300, "y": 450},
                    ]
                },
                "landmarks": [
                    {"type": "LEFT_EYE", "position": {"x": 360, "y": 250}},
                    {"type": "RIGHT_EYE", "position": {"x": 440, "y": 250}},
                    {"type": "NOSE_TIP", "position": {"x": 400, "y": 300}},
                    {"type": "MOUTH_LEFT", "position": {"x": 370, "y": 360}},
                    {"type": "MOUTH_RIGHT", "position": {"x": 430, "y": 360}},
                ],
                "joyLikelihood": "VERY_LIKELY",
                "sorrowLikelihood": "VERY_UNLIKELY",
                "angerLikelihood": "VERY_UNLIKELY",
                "surpriseLikelihood": "UNLIKELY",
            }
        ],
        "labelAnnotations": [
            {"description": "Ceremony", "score": 0.95},
            {"description": "Yellow", "score": 0.88},
            {"description": "Tradition", "score": 0.80},
        ],
        "imagePropertiesAnnotation": {
            "dominantColors": {
                "colors": [
                    {"color": {"red": 240, "green": 210, "blue": 60}, "score": 0.6},
                    {"color": {"red": 30, "green": 120, "blue": 40}, "score": 0.3},
                ]
            }
        },
    }


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "img.jpg"
    Image.new("RGB", (1000, 800), (200, 180, 120)).save(p)
    return str(p)


def test_available_reflects_api_key():
    assert VisionBrain(api_key="abc").available() is True
    assert VisionBrain(api_key="").available() is False


def test_parses_google_response(monkeypatch, photo):
    brain = VisionBrain(api_key="fake-key")
    monkeypatch.setattr(brain, "_annotate", lambda path: _canned_response())

    pb = brain.analyze(photo)
    assert pb.source == SOURCE_GOOGLE
    assert pb.face_count == 1

    # Box normalised to [0,1]: x=300/1000, y=150/800, w=200/1000, h=300/800.
    x, y, w, h = pb.face_boxes[0]
    assert x == pytest.approx(0.30) and y == pytest.approx(0.1875)
    assert w == pytest.approx(0.20) and h == pytest.approx(0.375)

    # 5 landmarks, normalised.
    assert len(pb.face_landmarks[0]) == 5
    lx, ly = pb.face_landmarks[0][0]  # LEFT_EYE 360,250
    assert lx == pytest.approx(0.36) and ly == pytest.approx(0.3125)

    assert pb.face_emotions == ["joy"]
    assert "ceremony" in pb.scene_labels          # lower-cased
    assert pb.scene_confidence[0] == pytest.approx(0.95)
    assert pb.dominant_colors[0] == (240, 210, 60)  # highest score first


def test_emotion_neutral_when_weak(monkeypatch, photo):
    resp = _canned_response()
    resp["faceAnnotations"][0]["joyLikelihood"] = "POSSIBLE"  # not > POSSIBLE
    brain = VisionBrain(api_key="k")
    monkeypatch.setattr(brain, "_annotate", lambda path: resp)
    assert brain.analyze(photo).face_emotions == ["neutral"]


def test_falls_back_to_local_on_api_error(monkeypatch, photo):
    brain = VisionBrain(api_key="k", enable_fallback=True)

    def boom(path):
        raise RuntimeError("network down")

    monkeypatch.setattr(brain, "_annotate", boom)
    # Force a deterministic local detector so we don't depend on MediaPipe here.
    brain._detector = _StubDetector([(0.4, 0.3, 0.2, 0.2)])

    pb = brain.analyze(photo)
    assert pb.source == SOURCE_LOCAL
    assert pb.face_count == 1
    assert pb.face_boxes[0] == (0.4, 0.3, 0.2, 0.2)
    assert pb.scene_labels == []  # no scene understanding locally


def test_no_key_uses_local_directly(monkeypatch, photo):
    brain = VisionBrain(api_key="")  # no key -> never calls the API
    brain._detector = _StubDetector([])
    pb = brain.analyze(photo)
    assert pb.source == SOURCE_LOCAL
    assert pb.face_count == 0


def test_api_error_reraised_when_fallback_disabled(monkeypatch, photo):
    brain = VisionBrain(api_key="k", enable_fallback=False)
    monkeypatch.setattr(brain, "_annotate", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        brain.analyze(photo)


def test_photobrain_json_roundtrip():
    pb = PhotoBrain(
        path="a.jpg",
        face_count=1,
        face_boxes=[(0.1, 0.2, 0.3, 0.4)],
        face_landmarks=[[(0.1, 0.1), (0.2, 0.1)]],
        face_emotions=["joy"],
        scene_labels=["haldi"],
        scene_confidence=[0.9],
        dominant_colors=[(240, 210, 60)],
        capture_time=datetime(2025, 2, 24, 10, 30, 0),
        source=SOURCE_GOOGLE,
    )
    restored = PhotoBrain.from_dict(pb.to_dict())
    assert restored.face_boxes == pb.face_boxes
    assert restored.face_landmarks == pb.face_landmarks
    assert restored.scene_labels == pb.scene_labels
    assert restored.dominant_colors == pb.dominant_colors
    assert restored.capture_time == pb.capture_time
    assert restored.source == SOURCE_GOOGLE


class _StubDetector:
    """Minimal stand-in for core.face_detector.FaceDetector."""

    def __init__(self, regions):
        self._regions = regions

    def detect(self, path):
        class _R:
            pass

        r = _R()
        r.regions = tuple(self._regions)
        r.face_count = len(self._regions)
        return r
