"""Component 3 tests: label-based event classification with colour fallback."""

from __future__ import annotations

from core.vision_brain import PhotoBrain
from core.event_classifier import (
    BARAAT,
    CEREMONY,
    HALDI,
    MEHNDI,
    RECEPTION,
    classify_event_group,
    classify_labels,
    classify_photo,
)


def test_labels_map_to_events():
    assert classify_labels(["Turmeric", "Marigold"])[0] == HALDI
    assert classify_labels(["Henna", "Hand"])[0] == MEHNDI
    assert classify_labels(["Horse", "Procession", "Dhol"])[0] == BARAAT
    assert classify_labels(["Dance floor", "Stage", "Cake"])[0] == RECEPTION
    assert classify_labels(["Mandap", "Priest", "Ritual"])[0] == CEREMONY


def test_confidence_weighting_picks_stronger_event():
    labels = ["yellow", "dance"]
    # dance (reception) with high confidence should beat a weak yellow (haldi).
    event, score = classify_labels(labels, [0.2, 0.95])
    assert event == RECEPTION


def test_no_match_returns_none():
    assert classify_labels(["building", "sky"]) == (None, 0.0)
    assert classify_labels([]) == (None, 0.0)


def test_classify_photo_prefers_labels():
    pb = PhotoBrain(path="a.jpg", scene_labels=["haldi", "turmeric"], scene_confidence=[0.9, 0.8])
    res = classify_photo(pb)
    assert res.event_type == HALDI
    assert res.source == "labels"


def test_classify_photo_falls_back_to_color():
    # No labels, but a turmeric-yellow dominant colour -> colour heuristic.
    pb = PhotoBrain(path="a.jpg", scene_labels=[], dominant_colors=[(230, 200, 40)])
    res = classify_photo(pb)
    assert res.event_type == HALDI
    assert res.source == "color"


def test_group_majority_vote():
    haldi = [PhotoBrain(path=f"{i}.jpg", scene_labels=["haldi"], scene_confidence=[0.9]) for i in range(3)]
    stray = [PhotoBrain(path="x.jpg", scene_labels=["dance"], scene_confidence=[0.5])]
    result = classify_event_group(haldi + stray)
    assert result.event_type == HALDI  # 3 haldi outweigh 1 stray
