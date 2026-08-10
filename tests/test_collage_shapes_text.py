"""
Tests for core.collage_shapes (shape masks) and core.collage_text
(title text + studio watermark).
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from core.collage import (
    LAYOUT_GRID,
    THEMES,
    Background,
    CollagePhoto,
    CollageSpec,
    build_collage,
)
from core.collage_shapes import (
    SHAPE_CIRCLE,
    SHAPE_HEART,
    SHAPE_NONE,
    SHAPE_ROUNDED,
    SHAPE_STAR,
    SHAPE_TEXT,
    SHAPES,
    ShapeError,
    shape_coverage,
    shape_mask,
)
from core.collage_text import (
    POS_BOTTOM_CENTER,
    POS_BOTTOM_RIGHT,
    POS_CENTER,
    POS_TOP_LEFT,
    POSITIONS,
    CollageTextError,
    TextOverlay,
    Watermark,
    draw_text_overlays,
    draw_watermark,
)


def _photos(n=6, color=(0, 0, 255)) -> list[CollagePhoto]:
    return [CollagePhoto(image=Image.new("RGB", (400, 400), color)) for _ in range(n)]


def _spec(w=400, h=400) -> CollageSpec:
    return CollageSpec(width_px=w, height_px=h)


# --------------------------------------------------------------------------- #
# Shape masks
# --------------------------------------------------------------------------- #
def test_none_shape_keeps_the_whole_canvas():
    mask = shape_mask(SHAPE_NONE, (100, 80))
    assert mask.size == (100, 80)
    assert np.asarray(mask).min() == 255


@pytest.mark.parametrize("shape", [SHAPE_HEART, SHAPE_CIRCLE, SHAPE_ROUNDED, SHAPE_STAR])
def test_shapes_cover_a_sensible_fraction_of_the_canvas(shape):
    coverage = shape_coverage(shape_mask(shape, (400, 400)))
    # Big enough to hold recognisable photos, small enough to read as a shape.
    assert 0.2 < coverage < 0.97, f"{shape} covers {coverage:.2f}"


def test_heart_is_wider_at_the_top_than_the_bottom():
    """Sanity-check the heart is actually heart-shaped, not a blob."""
    arr = np.asarray(shape_mask(SHAPE_HEART, (400, 400))) > 127
    upper_width = arr[120].sum()
    lower_width = arr[380].sum()
    assert upper_width > lower_width * 3


def test_heart_top_middle_dips_between_two_lobes():
    arr = np.asarray(shape_mask(SHAPE_HEART, (400, 400))) > 127
    # Near the top, the centre column sits in the notch between the lobes,
    # while columns either side are inside a lobe.
    row = arr[40]
    assert not row[200], "expected a notch at the top centre of the heart"
    assert row[140] or row[260], "expected lobes either side of the notch"


def test_circle_is_centred_and_symmetric():
    arr = np.asarray(shape_mask(SHAPE_CIRCLE, (300, 300))) > 127
    assert arr[150, 150]
    assert not arr[5, 5]
    left, right = arr[150, :150].sum(), arr[150, 150:].sum()
    assert abs(int(left) - int(right)) <= 3


def test_circle_stays_circular_on_a_wide_canvas():
    arr = np.asarray(shape_mask(SHAPE_CIRCLE, (600, 200))) > 127
    height = arr[:, 300].sum()
    width = arr[100, :].sum()
    assert abs(int(height) - int(width)) <= 6  # not stretched into an ellipse


def test_text_shape_renders_the_glyphs():
    mask = shape_mask(SHAPE_TEXT, (600, 300), text="25")
    arr = np.asarray(mask) > 127
    assert arr.any()
    assert 0.05 < arr.mean() < 0.8
    # Glyphs should be roughly centred, not hugging one edge.
    columns = np.where(arr.any(axis=0))[0]
    assert columns.min() > 5 and columns.max() < 595


def test_text_shape_differs_between_different_strings():
    a = np.asarray(shape_mask(SHAPE_TEXT, (400, 200), text="25"))
    b = np.asarray(shape_mask(SHAPE_TEXT, (400, 200), text="50"))
    assert not np.array_equal(a, b)


def test_text_shape_requires_text():
    with pytest.raises(ShapeError, match="needs some text"):
        shape_mask(SHAPE_TEXT, (200, 200), text="   ")


def test_unknown_shape_raises():
    with pytest.raises(ShapeError, match="Unknown shape"):
        shape_mask("octagon", (100, 100))


def test_tiny_canvas_raises():
    with pytest.raises(ShapeError, match="too small"):
        shape_mask(SHAPE_HEART, (1, 1))


@pytest.mark.parametrize("shape", SHAPES)
def test_all_shapes_reachable_through_build_collage(shape):
    img = build_collage(
        _photos(6), _spec(300, 300), THEMES["Seamless"],
        layout=LAYOUT_GRID, shape=shape, shape_text="25",
    )
    assert img.size == (300, 300)


@pytest.mark.parametrize(
    "shape,text", [(SHAPE_HEART, ""), (SHAPE_TEXT, "25"), (SHAPE_STAR, ""), (SHAPE_CIRCLE, "")]
)
def test_photos_land_inside_the_shape_and_background_outside(shape, text):
    """The whole point of a shape collage: photos fill the shape, and the
    background (not photos) shows around it."""
    spec = _spec(400, 400)
    img = build_collage(
        _photos(6, color=(0, 0, 255)), spec, THEMES["Seamless"],
        layout=LAYOUT_GRID, shape=shape, shape_text=text,
        background=Background(color=(255, 0, 0)),
    )
    arr = np.asarray(img)
    mask = np.asarray(shape_mask(shape, (400, 400), text=text)) > 127
    is_photo = arr[..., 2] > 150

    assert is_photo[mask].mean() > 0.9, "photos should fill the shape"
    assert is_photo[~mask].mean() < 0.1, "outside the shape should be background"


def test_shape_does_not_punch_a_hole_in_the_background():
    """Masking must clip the photos only -- the backdrop still covers the canvas."""
    img = build_collage(
        _photos(4), _spec(200, 200), THEMES["Seamless"], layout=LAYOUT_GRID,
        shape=SHAPE_CIRCLE, background=Background(color=(255, 0, 0)),
    )
    corner = np.asarray(img)[0, 0]
    assert corner.tolist() == [255, 0, 0]


# --------------------------------------------------------------------------- #
# Text overlays
# --------------------------------------------------------------------------- #
def _blank(w=400, h=300, color=(255, 255, 255)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def test_text_overlay_marks_the_canvas():
    before = _blank()
    after = draw_text_overlays(
        before, [TextOverlay(text="Priya & Arjun", color=(0, 0, 0), shadow=False)], _spec(400, 300)
    )
    assert not np.array_equal(np.asarray(before), np.asarray(after))


def test_empty_overlay_list_is_a_passthrough():
    img = _blank()
    assert draw_text_overlays(img, [], _spec(400, 300)) is img


def test_blank_text_is_skipped():
    img = _blank()
    out = draw_text_overlays(img, [TextOverlay(text="   ")], _spec(400, 300))
    assert np.array_equal(np.asarray(img), np.asarray(out))


@pytest.mark.parametrize("position", POSITIONS)
def test_every_text_position_is_accepted(position):
    out = draw_text_overlays(
        _blank(), [TextOverlay(text="Hi", position=position, color=(0, 0, 0))], _spec(400, 300)
    )
    assert out.size == (400, 300)


def test_text_position_actually_moves_the_text():
    spec = _spec(400, 300)
    top = np.asarray(
        draw_text_overlays(
            _blank(), [TextOverlay(text="ABC", position=POS_TOP_LEFT, color=(0, 0, 0))], spec
        )
    )
    bottom = np.asarray(
        draw_text_overlays(
            _blank(), [TextOverlay(text="ABC", position=POS_BOTTOM_CENTER, color=(0, 0, 0))], spec
        )
    )
    # Dark pixels should sit high in one and low in the other.
    top_rows = np.where((top < 100).any(axis=(1, 2)))[0]
    bottom_rows = np.where((bottom < 100).any(axis=(1, 2)))[0]
    assert top_rows.mean() < bottom_rows.mean()


def test_larger_size_frac_draws_more_ink():
    spec = _spec(400, 300)
    small = np.asarray(
        draw_text_overlays(_blank(), [TextOverlay(text="AB", size_frac=0.05, color=(0, 0, 0),
                                                 shadow=False)], spec)
    )
    large = np.asarray(
        draw_text_overlays(_blank(), [TextOverlay(text="AB", size_frac=0.18, color=(0, 0, 0),
                                                 shadow=False)], spec)
    )
    assert (large < 100).sum() > (small < 100).sum()


def test_unknown_position_raises():
    with pytest.raises(CollageTextError, match="Unknown position"):
        draw_text_overlays(_blank(), [TextOverlay(text="x", position="middle-ish")], _spec(400, 300))


def test_multiline_text_is_supported():
    out = draw_text_overlays(
        _blank(), [TextOverlay(text="Priya & Arjun\n12 Feb 2026", color=(0, 0, 0))],
        _spec(400, 300),
    )
    assert (np.asarray(out) < 100).any()


def test_missing_font_file_falls_back_instead_of_failing():
    out = draw_text_overlays(
        _blank(),
        [TextOverlay(text="Hello", font_path="/nope/not-a-font.ttf", color=(0, 0, 0))],
        _spec(400, 300),
    )
    assert (np.asarray(out) < 100).any()  # still drew something


def test_shadow_adds_dark_pixels_for_light_text():
    spec = _spec(400, 300)
    plain = np.asarray(
        draw_text_overlays(_blank(color=(200, 200, 200)),
                           [TextOverlay(text="WED", color=(255, 255, 255), shadow=False)], spec)
    )
    shadowed = np.asarray(
        draw_text_overlays(_blank(color=(200, 200, 200)),
                           [TextOverlay(text="WED", color=(255, 255, 255), shadow=True)], spec)
    )
    assert shadowed.min() < plain.min()


# --------------------------------------------------------------------------- #
# Watermark
# --------------------------------------------------------------------------- #
def _logo(tmp_path, w=200, h=80):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (w, h), (255, 0, 0, 255)).save(path)
    return path


def test_watermark_is_composited(tmp_path):
    out = draw_watermark(
        _blank(), Watermark(image_path=_logo(tmp_path)), _spec(400, 300)
    )
    arr = np.asarray(out)
    assert (arr[..., 0] > 150).any() and (arr[..., 1] < 120).any()  # some red logo


def test_watermark_width_frac_controls_size(tmp_path):
    logo = _logo(tmp_path)
    spec = _spec(400, 300)
    small = np.asarray(draw_watermark(_blank(), Watermark(image_path=logo, width_frac=0.1), spec))
    large = np.asarray(draw_watermark(_blank(), Watermark(image_path=logo, width_frac=0.5), spec))
    reddish = lambda a: ((a[..., 0] > 150) & (a[..., 1] < 120)).sum()  # noqa: E731
    assert reddish(large) > reddish(small)


def test_watermark_opacity_fades_it(tmp_path):
    logo = _logo(tmp_path)
    spec = _spec(400, 300)
    solid = np.asarray(draw_watermark(_blank(), Watermark(image_path=logo, opacity=255), spec))
    faded = np.asarray(draw_watermark(_blank(), Watermark(image_path=logo, opacity=60), spec))
    # A faded red logo over white stays lighter overall.
    assert faded.mean() > solid.mean()


def test_watermark_position_moves_it(tmp_path):
    logo = _logo(tmp_path)
    spec = _spec(400, 300)

    def red_rows(position):
        arr = np.asarray(draw_watermark(_blank(), Watermark(image_path=logo, position=position), spec))
        mask = (arr[..., 0] > 150) & (arr[..., 1] < 120)
        return np.where(mask.any(axis=1))[0].mean()

    assert red_rows(POS_TOP_LEFT) < red_rows(POS_BOTTOM_RIGHT)


def test_missing_watermark_file_raises_clearly():
    with pytest.raises(CollageTextError, match="Could not open watermark"):
        draw_watermark(_blank(), Watermark(image_path="/nope/logo.png"), _spec(400, 300))


def test_text_and_watermark_reachable_through_build_collage(tmp_path):
    img = build_collage(
        _photos(4), _spec(400, 400), layout=LAYOUT_GRID,
        text_overlays=[TextOverlay(text="Priya & Arjun", position=POS_CENTER)],
        watermark=Watermark(image_path=_logo(tmp_path)),
    )
    assert img.size == (400, 400)
