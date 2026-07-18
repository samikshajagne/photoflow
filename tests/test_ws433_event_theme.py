"""WS 4.3.3 tests: event-themed background palettes."""

from __future__ import annotations

from core.album.event_theme import themed_background, themed_background_hex


def _is_light(rgb):
    return sum(rgb) / 3 > 150  # a page tint should be light


def test_haldi_yellow_background_is_warm_light():
    pair = themed_background((230, 200, 40))
    assert pair is not None
    bg, accent = pair
    assert _is_light(bg)
    assert bg[0] > bg[2] and bg[1] > bg[2]  # warm: red/green > blue
    # accent is deeper (darker) than the background tint.
    assert sum(accent) < sum(bg)


def test_mehndi_green_background():
    pair = themed_background((60, 170, 70))
    assert pair is not None
    bg, _ = pair
    assert bg[1] >= bg[0] and bg[1] >= bg[2]  # green channel dominant


def test_baraat_red_background():
    pair = themed_background((200, 40, 40))
    assert pair is not None
    bg, _ = pair
    assert bg[0] > bg[1] and bg[0] > bg[2]  # red dominant


def test_neutral_returns_none():
    # A desaturated grey classifies as Portraits -> no recolour.
    assert themed_background((190, 188, 185)) is None


def test_odd_hue_ceremony_returns_none():
    assert themed_background((60, 60, 200)) is None  # blue -> Ceremony fallback -> None


def test_low_confidence_returns_none():
    # Force a high confidence bar so even a themed colour is suppressed.
    assert themed_background((230, 200, 40), min_confidence=0.99) is None


def test_hex_helper():
    hx = themed_background_hex((230, 200, 40))
    assert hx is not None
    bg_hex, accent_hex = hx
    assert bg_hex.startswith("#") and len(bg_hex) == 7
    assert accent_hex.startswith("#") and len(accent_hex) == 7
