"""WS 3.2 tests: composition classification + subject-aware slot matching."""

from __future__ import annotations

from core.content_analyzer import (
    DETAIL,
    FULL_BODY,
    GROUP,
    LANDSCAPE,
    LARGE_GROUP,
    PORTRAIT,
    analyze,
    classify_composition,
)
from core.album.slot_matcher import (
    DEFAULT_SLOT_PROFILES,
    SlotProfile,
    compatibility_score,
    match_photos_to_slots,
)


# --------------------------------------------------------------------------- #
# content_analyzer
# --------------------------------------------------------------------------- #
def test_classify_portrait():
    # One large face in a portrait frame.
    assert classify_composition([(0.3, 0.2, 0.4, 0.4)], 0.75) == PORTRAIT


def test_classify_group_and_large_group():
    three = [(0.1 * i, 0.4, 0.08, 0.1) for i in range(3)]
    assert classify_composition(three, 1.5) == GROUP
    six = [(0.1 * i, 0.4, 0.05, 0.06) for i in range(6)]
    assert classify_composition(six, 1.78) == LARGE_GROUP


def test_classify_landscape_and_detail():
    assert classify_composition([], 1.78) == LANDSCAPE
    # A tiny face -> detail close-up.
    assert classify_composition([(0.48, 0.48, 0.03, 0.03)], 1.0) == DETAIL


def test_classify_full_body():
    # Small face, tall frame -> full-body portrait.
    assert classify_composition([(0.45, 0.05, 0.05, 0.05)], 0.6) == FULL_BODY


def test_analyze_reports_fields():
    pc = analyze(0.75, [(0.3, 0.2, 0.4, 0.4)])
    assert pc.face_count == 1
    assert pc.composition_type == PORTRAIT
    assert pc.orientation == "portrait"
    assert abs(pc.dominant_face_frac - 0.16) < 1e-6


# --------------------------------------------------------------------------- #
# slot_matcher
# --------------------------------------------------------------------------- #
def test_compatibility_prefers_matching_slot():
    portrait = analyze(0.75, [(0.3, 0.2, 0.4, 0.45)])
    p_slot = SlotProfile("portrait_large", 0.75, ("portrait",), (1, 1))
    l_slot = SlotProfile("landscape_wide", 1.78, ("group", "landscape"), (2, 5))
    assert compatibility_score(portrait, p_slot) > compatibility_score(portrait, l_slot)


def test_matching_assigns_each_type_to_its_slot():
    # A portrait, a group, and a detail; three matching slots.
    portrait = analyze(0.75, [(0.3, 0.2, 0.4, 0.45)])
    group = analyze(1.5, [(0.1 * i, 0.4, 0.07, 0.09) for i in range(3)])
    detail = analyze(1.0, [(0.48, 0.48, 0.03, 0.03)])
    contents = [portrait, group, detail]

    slots = [
        SlotProfile("portrait_large", 0.75, ("portrait",), (1, 1)),
        SlotProfile("group_square", 1.0, ("group",), (2, 5)),
        SlotProfile("detail_square", 1.0, ("detail",), (0, 1)),
    ]
    assignment = match_photos_to_slots(contents, slots)
    # {slot_index: photo_index}
    assert assignment[0] == 0  # portrait slot -> portrait photo
    assert assignment[1] == 1  # group slot -> group photo
    assert assignment[2] == 2  # detail slot -> detail photo


def test_matching_covers_min_of_photos_and_slots():
    contents = [analyze(0.75, [(0.3, 0.2, 0.4, 0.45)])]
    assignment = match_photos_to_slots(contents, DEFAULT_SLOT_PROFILES)
    assert len(assignment) == 1
    # The single portrait should win the portrait slot, not a landscape one.
    slot_names = {DEFAULT_SLOT_PROFILES[si].name for si in assignment}
    assert "portrait_large" in slot_names or "portrait_small" in slot_names


def test_matching_is_deterministic():
    contents = [analyze(0.75, [(0.3, 0.2, 0.4, 0.45)]), analyze(1.78, [])]
    a = match_photos_to_slots(contents, DEFAULT_SLOT_PROFILES)
    b = match_photos_to_slots(contents, DEFAULT_SLOT_PROFILES)
    assert a == b
