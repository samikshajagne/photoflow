"""
tests/test_z_ordering.py
------------------------
Tests for the z_index overlapping layout feature added in Component 1.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from core.album.template import (
    Background,
    SpreadTemplate,
    TemplateSlot,
    default_templates,
    render_spread,
    SHAPE_RECT,
    BG_SOLID,
    DEFAULT_THEME,
)
from core.album.layout import AlbumSpec

# A minimal AlbumSpec for tests (10x5 inch at 72 dpi -> 720x360 px).
_SPEC = AlbumSpec(
    page_width_in=5.0,
    page_height_in=5.0,
    dpi=72,
    double_page_spread=True,
)


def _loader(path: str) -> Image.Image:
    if path == "red":
        return Image.new("RGB", (100, 100), (255, 0, 0))
    return Image.new("RGB", (100, 100), (0, 0, 255))


# ---------------------------------------------------------------------------
# 1. TemplateSlot stores z_index correctly
# ---------------------------------------------------------------------------

def test_default_z_index_is_zero():
    slot = TemplateSlot(rect=(0.0, 0.0, 1.0, 1.0))
    assert slot.z_index == 0


def test_z_index_stores_correctly():
    slot = TemplateSlot(rect=(0.5, 0.5, 0.3, 0.3), z_index=1)
    assert slot.z_index == 1


def test_to_dict_includes_z_index():
    slot = TemplateSlot(rect=(0.0, 0.0, 0.5, 0.5), z_index=2)
    d = slot.to_dict()
    assert "z_index" in d
    assert d["z_index"] == 2


def test_from_dict_restores_z_index():
    original = TemplateSlot(rect=(0.0, 0.0, 0.5, 0.5), z_index=3)
    restored = TemplateSlot.from_dict(original.to_dict())
    assert restored.z_index == 3


def test_from_dict_missing_z_index_defaults_zero():
    data = {"rect": [0.0, 0.0, 1.0, 1.0], "shape": "rect"}
    slot = TemplateSlot.from_dict(data)
    assert slot.z_index == 0


# ---------------------------------------------------------------------------
# 2. Render order: background (z=0) is drawn before overlay (z=1)
# ---------------------------------------------------------------------------

def _make_ordered_template(bg_z: int, overlay_z: int) -> SpreadTemplate:
    return SpreadTemplate(
        name=f"test-zorder",
        theme="test",
        slots=(
            TemplateSlot(rect=(0.0, 0.0, 1.0, 1.0), shape=SHAPE_RECT,
                         border=0.0, shadow=False, z_index=bg_z),
            TemplateSlot(rect=(0.70, 0.70, 0.25, 0.25), shape=SHAPE_RECT,
                         border=0.0, shadow=False, z_index=overlay_z),
        ),
        background=Background(type=BG_SOLID, color="#FFFFFF"),
    )


def test_overlay_z1_painted_on_top_of_z0_background():
    """The overlay (z=1, blue) must cover the background (z=0, red)."""
    template = _make_ordered_template(bg_z=0, overlay_z=1)
    spread = render_spread(
        template,
        image_paths=["red", "blue"],
        spec=_SPEC,
        loader=_loader,
    )
    # Sample pixel inside the overlay region (bottom-right quadrant)
    px = spread.getpixel((540, 280))
    r, g, b = px[:3]
    assert b > 200 and r < 50, f"Expected blue overlay on top of red bg, got {px}"


def test_z0_background_without_overlay_is_red():
    """With no overlay slot, the single background should be red."""
    template = SpreadTemplate(
        name="test-bg-only",
        theme="test",
        slots=(
            TemplateSlot(rect=(0.0, 0.0, 1.0, 1.0), shape=SHAPE_RECT,
                         border=0.0, shadow=False, z_index=0),
        ),
        background=Background(type=BG_SOLID, color="#FFFFFF"),
    )
    spread = render_spread(
        template,
        image_paths=["red"],
        spec=_SPEC,
        loader=_loader,
    )
    # Center of spread should be red (the single background photo)
    px = spread.getpixel((360, 180))
    r, g, b = px[:3]
    assert r > 200 and b < 50, f"Expected red background, got {px}"


# ---------------------------------------------------------------------------
# 3. New natural theme templates are discoverable
# ---------------------------------------------------------------------------

def test_natural_theme_templates_exist():
    templates = default_templates()
    natural_names = [t.name for t in templates if t.theme == "natural"]
    assert len(natural_names) >= 3, f"Expected >= 3 natural templates, got: {natural_names}"


def test_natural_panoramic_has_one_slot():
    templates = default_templates()
    panoramic = next((t for t in templates if t.name == "natural-1-panoramic"), None)
    assert panoramic is not None, "natural-1-panoramic template not found"
    assert len(panoramic.slots) == 1
    assert panoramic.slots[0].z_index == 0


def test_natural_duo_has_overlay_slot():
    templates = default_templates()
    duo = next((t for t in templates if t.name == "natural-2-duo"), None)
    assert duo is not None, "natural-2-duo template not found"
    assert len(duo.slots) == 2
    z_indexes = [s.z_index for s in duo.slots]
    assert 0 in z_indexes, "Duo must have a background slot (z_index=0)"
    assert 1 in z_indexes, "Duo must have an overlay slot (z_index=1)"


def test_natural_left_hero_3_has_correct_structure():
    templates = default_templates()
    hero = next((t for t in templates if t.name == "natural-3-left-hero"), None)
    assert hero is not None
    assert len(hero.slots) == 3
    # First slot should be the tall left hero covering < 55% width, >= 95% height
    assert hero.slots[0].rect[2] < 0.55
    assert hero.slots[0].rect[3] >= 0.95


# ---------------------------------------------------------------------------
# 4. Backward compatibility: classic templates all have z_index=0
# ---------------------------------------------------------------------------

def test_classic_templates_have_default_z_index_zero():
    templates = default_templates()
    classic = [t for t in templates if t.theme == DEFAULT_THEME]
    for tmpl in classic:
        for slot in tmpl.slots:
            assert slot.z_index == 0, (
                f"Classic template '{tmpl.name}' slot has non-zero z_index: {slot.z_index}"
            )


def test_round_trip_classic_template_preserves_z_index():
    """Classic templates round-trip through to_dict/from_dict without changing z_index."""
    templates = default_templates()
    classic_2 = next((t for t in templates if t.name == "classic-2"), None)
    assert classic_2 is not None
    d = classic_2.to_dict()
    restored = SpreadTemplate.from_dict(d)
    for orig, new in zip(classic_2.slots, restored.slots):
        assert orig.z_index == new.z_index == 0
