"""Component 4 tests: landmark-derived face box."""

from __future__ import annotations

from core.album.facecrop import face_box_from_landmarks

_EPS = 1e-6


def _contains(box, px, py):
    x, y, w, h = box
    return x - _EPS <= px <= x + w + _EPS and y - _EPS <= py <= y + h + _EPS


def test_box_contains_all_five_points():
    # Eyes at y=0.30, mouth at y=0.42, centered horizontally.
    lm = [(0.45, 0.30), (0.55, 0.30), (0.50, 0.36), (0.46, 0.42), (0.54, 0.42)]
    box = face_box_from_landmarks(lm)
    assert box is not None
    for px, py in lm:
        assert _contains(box, px, py), f"{(px, py)} not in {box}"


def test_box_extends_above_eyes_and_below_mouth():
    lm = [(0.45, 0.30), (0.55, 0.30), (0.50, 0.36), (0.46, 0.42), (0.54, 0.42)]
    x, y, w, h = face_box_from_landmarks(lm)
    # Crown is above the eye line (0.30) and chin below the mouth (0.42).
    assert y < 0.30
    assert y + h > 0.42


def test_stays_within_unit_square():
    # Face near the top-left corner; box must clamp to [0,1].
    lm = [(0.03, 0.03), (0.10, 0.03), (0.06, 0.06), (0.03, 0.09), (0.10, 0.09)]
    x, y, w, h = face_box_from_landmarks(lm)
    assert 0.0 <= x and 0.0 <= y
    assert x + w <= 1.0 + _EPS and y + h <= 1.0 + _EPS


def test_center_uses_eye_midpoint():
    lm = [(0.40, 0.30), (0.60, 0.30), (0.50, 0.36), (0.44, 0.42), (0.56, 0.42)]
    x, y, w, h = face_box_from_landmarks(lm)
    assert abs((x + w / 2.0) - 0.50) < 1e-6  # horizontally centered on eye midpoint


def test_none_on_bad_input():
    assert face_box_from_landmarks([]) is None
    assert face_box_from_landmarks([(0.5, 0.5)]) is None          # need >=2
    assert face_box_from_landmarks([(0.5, 0.5), (0.5, 0.5)]) is None  # zero eye distance
