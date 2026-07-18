"""
Tests for per-event colour theming (Phase 3, B3).

Pure Pillow/NumPy — no detection backends. Covers the mood-colour extraction,
the background tint, hex formatting, and the best-effort event classifier.
"""

from PIL import Image

from core.album.theming import (
    NEUTRAL,
    background_tint,
    classify_event_name,
    dominant_color,
    to_hex,
)


def _loader(mapping):
    return lambda path: mapping.get(path)


def test_dominant_color_picks_saturated_mood():
    imgs = {f"y{i}": Image.new("RGB", (20, 20), (230, 200, 30)) for i in range(3)}
    c = dominant_color(list(imgs), loader=_loader(imgs))
    assert c[0] > 150 and c[1] > 120 and c[2] < 100  # yellow-ish mood


def test_dominant_color_empty_is_neutral():
    assert dominant_color([]) == NEUTRAL


def test_dominant_color_skips_unreadable():
    assert dominant_color(["x", "y"], loader=lambda _p: None) == NEUTRAL


def test_background_tint_moves_toward_white():
    base = (200, 100, 50)
    assert background_tint(base, 0.0) == base
    assert background_tint(base, 1.0) == (255, 255, 255)
    mid = background_tint(base, 0.5)
    assert all(mid[i] > base[i] for i in range(3))


def test_to_hex():
    assert to_hex((255, 0, 16)) == "#FF0010"


def test_classify_haldi_from_turmeric_yellow():
    assert classify_event_name((235, 205, 40)) == "Haldi"


def test_classify_non_distinctive_is_none():
    assert classify_event_name((40, 60, 200)) is None    # blue
    assert classify_event_name((200, 200, 200)) is None   # neutral grey
