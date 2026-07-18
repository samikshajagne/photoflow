"""Tests for text overlays (Phase 4, B4). Pure Pillow — runs anywhere."""

import numpy as np
from PIL import Image

from core.album.textlayer import (
    QUOTES,
    draw_caption,
    draw_cover,
    pick_quote,
    resolve_font,
    title_for_section,
)


def test_resolve_font_returns_a_font():
    assert resolve_font("title", 40) is not None
    assert resolve_font("quote", 24) is not None


def test_resolve_script_font_falls_back_gracefully():
    # No Script.ttf is bundled, so this resolves to the elegant italic fallback
    # (never raises, never returns None).
    assert resolve_font("script", 48) is not None


def test_title_for_section_uppercases_and_trims():
    assert title_for_section("haldi") == "HALDI"
    assert title_for_section("  Reception ") == "RECEPTION"


def test_pick_quote_is_deterministic_and_in_library():
    assert pick_quote("Haldi") == pick_quote("Haldi")
    assert pick_quote("Haldi") in QUOTES
    assert pick_quote("Reception") in QUOTES


def test_draw_caption_keeps_size_and_draws_something():
    img = Image.new("RGB", (800, 500), (120, 120, 120))
    out = draw_caption(img, "HALDI", "A true love story never ends")
    assert out.size == (800, 500) and out.mode == "RGB"
    assert not np.array_equal(np.asarray(img), np.asarray(out))  # pixels changed


def test_draw_caption_title_only():
    img = Image.new("RGB", (600, 600), (80, 80, 80))
    out = draw_caption(img, "BRIDE")
    assert out.size == (600, 600)


def test_draw_cover_keeps_size_and_draws():
    img = Image.new("RGB", (1000, 600), (100, 100, 100))
    out = draw_cover(img, "Ruchika Weds Lukesh", "24 February 2024", "A Successful Love Story")
    assert out.size == (1000, 600) and out.mode == "RGB"
    assert not np.array_equal(np.asarray(img), np.asarray(out))
