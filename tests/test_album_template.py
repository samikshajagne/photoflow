"""
Tests for the declarative spread-template engine (Phase 3, B1).

Pure Pillow/NumPy — no detection backends — so these run everywhere. Covers
schema validation, JSON round-tripping, the built-in library + selection, and
that the programmatic renderer actually composites shaped slots onto a spread.
"""

import numpy as np
import pytest
from PIL import Image

from core.album.layout import AlbumSpec
from core.album.template import (
    SHAPE_CIRCLE,
    SHAPE_DIAMOND,
    Background,
    SpreadTemplate,
    TemplateError,
    TemplateSlot,
    auto_grid_template,
    default_templates,
    load_templates,
    render_spread,
    select_template,
)


def _solid_loader(color):
    def loader(_path):
        return Image.new("RGB", (600, 400), color)

    return loader


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def test_slot_validation():
    with pytest.raises(TemplateError):
        TemplateSlot(rect=(0.0, 0.0, 0.0, 1.0))  # zero width
    with pytest.raises(TemplateError):
        TemplateSlot(rect=(0.0, 0.0, 1.0, 1.0), shape="star")  # unknown shape
    with pytest.raises(TemplateError):
        TemplateSlot(rect=(0.0, 0.0, 1.0, 1.0), fit="stretch")  # unknown fit
    TemplateSlot(rect=(0.0, 0.0, 1.0, 1.0))  # valid


def test_background_validation():
    with pytest.raises(TemplateError):
        Background(type="solid")  # solid needs a color
    with pytest.raises(TemplateError):
        Background(type="rainbow")
    Background(type="solid", color="#102030")  # valid


def test_slot_roundtrip():
    slot = TemplateSlot(rect=(0.1, 0.2, 0.3, 0.4), shape=SHAPE_CIRCLE, border=0.01, shadow=True)
    assert TemplateSlot.from_dict(slot.to_dict()) == slot


def test_template_roundtrip_json(tmp_path):
    template = default_templates()[3]
    path = template.to_json(tmp_path / "t.json")
    assert SpreadTemplate.from_json(path) == template


def test_empty_template_rejected():
    with pytest.raises(TemplateError):
        SpreadTemplate(name="x", theme="classic", slots=())


# --------------------------------------------------------------------------- #
# Library + selection
# --------------------------------------------------------------------------- #
def test_default_templates_counts_and_shapes():
    tpls = default_templates()
    counts = [t.photo_count for t in tpls]
    assert counts[:6] == [1, 2, 3, 4, 5, 6]          # base layouts for 1-6 photos
    assert counts.count(3) >= 2 and counts.count(4) >= 2  # plus variety variants
    shapes = {s.shape for s in tpls[3].slots}
    assert {SHAPE_CIRCLE, SHAPE_DIAMOND} <= shapes  # showcases shaped slots


def test_select_exact_match():
    assert select_template(default_templates(), 3, "classic").photo_count == 3


def test_select_falls_back_to_auto_grid():
    # No 8-slot classic template exists -> auto grid with exactly 8 slots.
    t8 = select_template(default_templates(), 8, "classic")
    assert t8.photo_count == 8
    assert t8.name.startswith("grid-")


def test_auto_grid_counts():
    for n in (1, 2, 5, 7, 9):
        assert auto_grid_template(n).photo_count == n


def test_load_templates_from_disk(tmp_path):
    templates = default_templates()
    for t in templates:
        t.to_json(tmp_path / "classic" / f"{t.name}.json")
    assert len(load_templates(tmp_path)) == len(templates)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_render_produces_spread_of_spec_size():
    spec = AlbumSpec(12, 12, 72)  # small + fast: 1728 x 864
    template = select_template(default_templates(), 4, "classic")
    img = render_spread(template, ["a", "b", "c", "d"], spec, loader=_solid_loader((200, 40, 40)))

    assert img.mode == "RGB"
    assert img.size == (spec.spread_width_px, spec.spread_height_px)
    # The red photo fills should be clearly present (slots were drawn).
    arr = np.asarray(img)
    reddish = ((arr[:, :, 0] > 150) & (arr[:, :, 1] < 100) & (arr[:, :, 2] < 100)).sum()
    assert reddish > 1000


def test_brush_shape_renders():
    # A brush (torn-edge) slot renders without error and fills the canvas.
    from core.album.template import SHAPE_BRUSH, Background, SpreadTemplate, TemplateSlot

    spec = AlbumSpec(10, 10, 72)
    template = SpreadTemplate(
        name="brush-1",
        theme="classic",
        slots=(TemplateSlot(rect=(0.1, 0.1, 0.8, 0.8), shape=SHAPE_BRUSH, shadow=True),),
        background=Background(type="solid", color="#EEE8DC"),
    )
    img = render_spread(
        template, ["x"], spec, loader=lambda _p: Image.new("RGB", (400, 400), (200, 50, 50))
    )
    assert img.size == (spec.spread_width_px, spec.spread_height_px)
    arr = np.asarray(img)
    reddish = ((arr[:, :, 0] > 150) & (arr[:, :, 2] < 100)).sum()
    assert reddish > 1000  # the photo shows through the torn edge


def test_render_partial_fill_is_ok():
    spec = AlbumSpec(12, 12, 72)
    template = select_template(default_templates(), 4, "classic")
    # Fewer photos than slots must still render (extra slots simply unfilled).
    img = render_spread(template, ["only-one"], spec, loader=_solid_loader((10, 180, 10)))
    assert img.size == (spec.spread_width_px, spec.spread_height_px)


def test_render_missing_photo_becomes_placeholder():
    spec = AlbumSpec(8, 8, 72)
    template = select_template(default_templates(), 2, "classic")
    # Default loader on a non-existent path yields a grey placeholder, no raise.
    img = render_spread(template, ["/does/not/exist.jpg", "/nope.png"], spec)
    assert img.size == (spec.spread_width_px, spec.spread_height_px)
