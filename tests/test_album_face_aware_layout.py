"""
Face-aware layout: don't crop through faces, and don't bury a crowd in a
thumbnail.

Two distinct failures are covered here, because they happen in different parts
of the pipeline:

- The **designed-template renderer** crops photos to fill shaped slots, so a
  badly-shaped slot slices faces. Guarded by ``face_crop_loss`` feeding the
  slot matcher.
- The **count-based engine** letterboxes photos into collage cells, so nothing
  is cropped — but a fifteen-person group in the smallest cell is unreadable.
  Guarded by the crowding term in the engine's frame assignment.
"""

import pytest

from core.album.facecrop import face_crop_loss, face_safe_cover_crop
from core.album.layout import AlbumLayoutEngine, AlbumSpec, Frame, PhotoItem
from core.album.slot_matcher import SlotProfile, compatibility_score
from core.content_analyzer import analyze


def _spec():
    return AlbumSpec(page_width_in=12, page_height_in=12, dpi=100, gutter_in=0.5)


# --------------------------------------------------------------------------- #
# face_crop_loss
# --------------------------------------------------------------------------- #
def test_no_faces_means_no_loss():
    assert face_crop_loss(1.5, 0.75, ()) == 0.0


def test_centred_face_survives_a_matching_slot():
    face = (0.42, 0.30, 0.16, 0.20)
    assert face_crop_loss(1.5, 1.5, [face]) == pytest.approx(0.0, abs=1e-9)


def test_single_face_survives_even_a_badly_shaped_slot():
    """
    One face can always be saved by shifting the crop window, so the loss stays
    zero however wrong the slot's shape is. This is what ``face_safe_cover_crop``
    is for, and the penalty must not fire when it succeeds.
    """
    face = (0.05, 0.30, 0.12, 0.16)
    assert face_crop_loss(2.0, 0.5, [face]) == pytest.approx(0.0, abs=1e-9)


def test_wide_row_of_guests_is_cut_by_a_tall_slot():
    """
    The case shifting cannot rescue: faces spanning the full width simply do not
    fit a narrow slot, so somebody gets trimmed. This is the signal the slot
    matcher needs, and the one an aspect-ratio comparison alone never sees.
    """
    guests = tuple((x, 0.40, 0.08, 0.10) for x in (0.02, 0.25, 0.48, 0.71, 0.88))
    loss = face_crop_loss(1.6, 0.5, guests)
    assert loss > 0.2, f"expected visible face loss, got {loss}"


def test_loss_is_bounded():
    guests = tuple((x, 0.40, 0.08, 0.10) for x in (0.02, 0.45, 0.88))
    for frame_ar in (0.25, 0.5, 1.0, 1.6, 3.0):
        assert 0.0 <= face_crop_loss(1.6, frame_ar, guests) <= 1.0


def test_loss_agrees_with_the_crop_it_describes():
    """
    ``face_crop_loss`` must measure the crop the renderer will actually use, not
    an idealised one — otherwise the penalty describes a layout nobody produces.
    """
    guests = tuple((x, 0.40, 0.08, 0.10) for x in (0.02, 0.45, 0.88))
    crop_x, _, crop_w, _ = face_safe_cover_crop(1.6, 0.5, guests)
    fully_inside = [
        g for g in guests if g[0] >= crop_x and g[0] + g[2] <= crop_x + crop_w
    ]
    # Some guests fall outside that window, which is exactly what the loss reports.
    assert len(fully_inside) < len(guests)
    assert face_crop_loss(1.6, 0.5, guests) > 0.0


# --------------------------------------------------------------------------- #
# Slot matching penalises face-slicing slots
# --------------------------------------------------------------------------- #
def test_slot_matcher_prefers_a_slot_that_keeps_faces_whole():
    """
    A wide group photo scored against a wide slot and a tall one. Both are
    plausible on composition; only the wide slot keeps everyone's face.
    """
    guests = tuple((x, 0.40, 0.08, 0.10) for x in (0.02, 0.25, 0.48, 0.71, 0.88))
    content = analyze(1.6, guests)

    wide = SlotProfile("wide", 1.6, ("group", "large_group"), (2, 8))
    tall = SlotProfile("tall", 0.5, ("group", "large_group"), (2, 8))

    assert compatibility_score(content, wide) > compatibility_score(content, tall)


def test_face_safety_can_overrule_a_composition_match():
    """
    The penalty has to be strong enough to matter. A slot advertising the exact
    composition type must still lose to a mismatched slot that keeps faces intact
    — a spread that halves a guest's face is wrong regardless of its label.
    """
    guests = tuple((x, 0.40, 0.08, 0.10) for x in (0.02, 0.25, 0.48, 0.71, 0.88))
    content = analyze(1.6, guests)

    slicing_but_labelled = SlotProfile("tall", 0.42, ("large_group",), (2, 8))
    safe_but_mislabelled = SlotProfile("wide", 1.6, ("detail",), (0, 1))

    assert compatibility_score(content, safe_but_mislabelled) > compatibility_score(
        content, slicing_but_labelled
    )


def test_photos_without_face_boxes_score_as_before():
    """
    Face safety must be inert for descriptions carrying no boxes, so nothing
    regresses for callers that never supplied them.
    """
    from core.content_analyzer import PhotoContent

    bare = PhotoContent(
        face_count=3,
        dominant_face_frac=0.05,
        face_centroids=((0.2, 0.4), (0.5, 0.4), (0.8, 0.4)),
        composition_type="group",
        orientation="landscape",
        aspect_ratio=1.6,
    )
    tall = SlotProfile("tall", 0.5, ("group",), (2, 8))
    # No boxes -> no penalty -> the score is the sum of the positive sub-scores.
    assert compatibility_score(bare, tall) > 0


def test_analyze_carries_face_boxes_through():
    boxes = ((0.1, 0.2, 0.1, 0.1), (0.6, 0.2, 0.1, 0.1))
    assert analyze(1.5, boxes).face_boxes == boxes


# --------------------------------------------------------------------------- #
# The engine puts crowded photos in bigger cells
# --------------------------------------------------------------------------- #
def _faces(n):
    """``n`` small faces in a row, as relative boxes."""
    return tuple((0.05 + i * (0.9 / max(n, 1)), 0.4, 0.06, 0.08) for i in range(n))


def test_crowded_photo_gets_the_larger_cell():
    """
    Two same-shaped photos, one a crowd and one a single portrait, into a
    template with a big cell and a small one. The crowd should take the big cell
    — otherwise fifteen faces render at thumbnail size.
    """
    spec = _spec()
    engine = AlbumLayoutEngine(max_per_spread=4)
    frames = (
        Frame(0.0, 0.0, 0.65, 1.0),   # large
        Frame(0.67, 0.0, 0.33, 1.0),  # small, same-ish orientation
    )
    crowd = PhotoItem(path="crowd.jpg", aspect_ratio=0.9, face_boxes=_faces(8))
    single = PhotoItem(path="single.jpg", aspect_ratio=0.9, face_boxes=_faces(1))

    pairs = engine._assign_to_frames([crowd, single], frames, spec)
    by_frame = {frame: item.path for item, frame in pairs}
    assert by_frame[frames[0]] == "crowd.jpg"
    assert by_frame[frames[1]] == "single.jpg"


def test_orientation_still_wins_over_crowding():
    """
    Crowding is a nudge, not a veto. A portrait photo must not be dragged into a
    wide cell just because it has more faces — the wrong-shaped cell is visible
    on every spread, the smaller group photo merely a missed opportunity.
    """
    spec = _spec()
    engine = AlbumLayoutEngine(max_per_spread=4)
    frames = (
        Frame(0.0, 0.0, 0.7, 1.0),    # wide and large
        Frame(0.72, 0.0, 0.28, 1.0),  # tall and small
    )
    crowded_portrait = PhotoItem("tall.jpg", aspect_ratio=0.5, face_boxes=_faces(9))
    empty_landscape = PhotoItem("wide.jpg", aspect_ratio=2.0, face_boxes=())

    pairs = engine._assign_to_frames(
        [crowded_portrait, empty_landscape], frames, spec
    )
    by_frame = {frame: item.path for item, frame in pairs}
    assert by_frame[frames[0]] == "wide.jpg", "landscape belongs in the wide cell"
    assert by_frame[frames[1]] == "tall.jpg"


def test_assignment_covers_every_frame_exactly_once():
    spec = _spec()
    engine = AlbumLayoutEngine(max_per_spread=6)
    frames = tuple(Frame(i * 0.24, 0.0, 0.23, 1.0) for i in range(4))
    items = [
        PhotoItem(f"p{i}.jpg", aspect_ratio=0.6 + i * 0.4, face_boxes=_faces(i))
        for i in range(4)
    ]
    pairs = engine._assign_to_frames(items, frames, spec)

    assert len(pairs) == 4
    assert {id(f) for _, f in pairs} == {id(f) for f in frames}
    assert {it.path for it, _ in pairs} == {it.path for it in items}


def test_assignment_falls_back_cleanly_on_large_spreads():
    """
    Above the exhaustive-search ceiling the heuristic takes over; it must still
    place every photo exactly once.
    """
    spec = _spec()
    engine = AlbumLayoutEngine(max_per_spread=12)
    n = 10
    frames = tuple(Frame(i * (1.0 / n), 0.0, 1.0 / n - 0.001, 1.0) for i in range(n))
    items = [
        PhotoItem(f"p{i}.jpg", aspect_ratio=0.5 + i * 0.2, face_boxes=_faces(i % 4))
        for i in range(n)
    ]
    pairs = engine._assign_to_frames(items, frames, spec)
    assert len(pairs) == n
    assert {it.path for it, _ in pairs} == {it.path for it in items}


def test_assignment_is_deterministic_with_identical_photos():
    """Ties must not resolve differently between runs."""
    spec = _spec()
    engine = AlbumLayoutEngine(max_per_spread=4)
    frames = tuple(Frame(i * 0.25, 0.0, 0.24, 1.0) for i in range(4))
    items = [PhotoItem(f"p{i}.jpg", aspect_ratio=1.0) for i in range(4)]
    first = engine._assign_to_frames(items, frames, spec)
    second = engine._assign_to_frames(items, frames, spec)
    assert [it.path for it, _ in first] == [it.path for it, _ in second]
