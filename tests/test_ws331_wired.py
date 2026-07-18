"""WS 3.3.1 integration tests: use_cutout flag wired into _place_slot."""
from __future__ import annotations
import numpy as np
from PIL import Image
from core.album.template import (
    TemplateSlot, SpreadTemplate, Background, BG_SOLID, SHAPE_RECT, FIT_COVER,
    _place_slot,
)


def _canvas(w=400, h=400):
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def _solid_rgb(w=200, h=300, color=(120, 80, 60)):
    return Image.new("RGB", (w, h), color)


def _slot_with_cutout(use_cutout=False):
    return TemplateSlot(
        rect=(0.0, 0.0, 0.5, 1.0),
        shape=SHAPE_RECT,
        border=0.0,
        shadow=False,
        fit=FIT_COVER,
        use_cutout=use_cutout,
    )


# Face in the upper-center of the image; large enough for the segmenter to use.
_LARGE_FACE = ((0.25, 0.1, 0.5, 0.35),)
# Face box too small -> segmenter returns None -> falls back to shape clip.
_TINY_FACE = ((0.48, 0.48, 0.04, 0.04),)


def test_cutout_flag_off_produces_rgba_canvas():
    """use_cutout=False -> normal RECT mask applied; canvas stays RGBA."""
    canvas = _canvas()
    slot = _slot_with_cutout(use_cutout=False)
    _place_slot(canvas, slot, _solid_rgb(), (0, 0, 400, 400), 400, _LARGE_FACE, use_cutout=False)
    assert canvas.mode == "RGBA"


def test_cutout_flag_on_large_face_fewer_opaque_pixels_than_rect():
    """
    use_cutout=True with a large face -> the mask is an ellipse, so fewer
    pixels should be fully opaque than the RECT fallback (which fills the
    entire slot).  We compare total alpha on identical canvases.
    """
    def _render(use_cutout_flag):
        c = _canvas()
        _place_slot(c, _slot_with_cutout(use_cutout=True), _solid_rgb(),
                    (0, 0, 400, 400), 400, _LARGE_FACE, use_cutout=use_cutout_flag)
        return np.asarray(c)[:, :, 3].sum()

    alpha_with_cutout = _render(True)
    alpha_rect_mask = _render(False)   # RECT mask covers full slot area

    # A cutout is an irregular shape (head-and-shoulders ellipse) inside the
    # slot, so the total opaque pixels should be strictly fewer.
    assert alpha_with_cutout < alpha_rect_mask, (
        f"Cutout mask should have fewer opaque pixels than a full RECT mask. "
        f"cutout={alpha_with_cutout}, rect={alpha_rect_mask}"
    )


def test_cutout_flag_on_tiny_face_falls_back_gracefully():
    """use_cutout=True with too-small face falls back to shape mask (no crash)."""
    canvas = _canvas()
    slot = _slot_with_cutout(use_cutout=True)
    img = _solid_rgb()
    _place_slot(canvas, slot, img, (0, 0, 400, 400), 400, _TINY_FACE, use_cutout=True)
    # Should not crash. The slot should have some drawn pixels.
    arr = np.asarray(canvas)
    assert arr[:, :, 3].sum() > 0, "Expected slot area to have drawn pixels after fallback"


def test_cutout_flag_on_no_faces_falls_back_to_shape_clip():
    """use_cutout=True with no face boxes falls back to the normal shape mask."""
    canvas = _canvas()
    slot = _slot_with_cutout(use_cutout=True)
    _place_slot(canvas, slot, _solid_rgb(), (0, 0, 400, 400), 400, (), use_cutout=True)
    # No face boxes -> no cutout -> normal rect mask -> slot should be fully opaque.
    arr = np.asarray(canvas)
    # Slot is 200x400 = 80,000 px, all should be alpha=255 for a RECT mask.
    assert arr[:, :200, 3].sum() == 255 * 400 * 200, "Expected full RECT opacity with no faces"


def test_use_cutout_field_on_template_slot_defaults_false():
    """Default TemplateSlot has use_cutout=False (backward compatible)."""
    slot = TemplateSlot(rect=(0.0, 0.0, 1.0, 1.0))
    assert slot.use_cutout is False


def test_use_cutout_field_roundtrips_through_dict():
    """use_cutout survives to_dict / from_dict serialisation."""
    slot = TemplateSlot(rect=(0.0, 0.0, 0.5, 1.0), use_cutout=True)
    d = slot.to_dict()
    assert d["use_cutout"] is True
    restored = TemplateSlot.from_dict(d)
    assert restored.use_cutout is True


def test_hero_slots_in_default_templates_are_cutout_eligible():
    """classic-3/4/5 hero slots (index 0) must have use_cutout=True."""
    from core.album.template import default_templates
    templates = {t.name: t for t in default_templates()}
    for name in ("classic-3", "classic-4", "classic-5"):
        hero_slot = templates[name].slots[0]
        assert hero_slot.use_cutout is True, f"{name} slot[0] should have use_cutout=True"
