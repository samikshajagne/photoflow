"""Tests for core/face_beautify.py (skin smoothing, auto-correct, background
whitening, teeth/eye whitening) and the core.auto_edit array-based refactor
it depends on."""

import cv2
import numpy as np
import pytest
from PIL import Image

from core.auto_edit import AutoEditor
from core.face_beautify import (
    CANONICAL_FACE_BOX,
    BeautifyOptions,
    _auto_correct,
    _smooth_skin,
    _whiten_background,
    _whiten_teeth_eyes,
    beautify,
)

_W, _H = 240, 280  # small synthetic "cropped passport photo"


def _face_roi_bounds():
    x, y, fw, fh = CANONICAL_FACE_BOX
    return int(x * _W), int(y * _H), int((x + fw) * _W), int((y + fh) * _H)


def _skin_photo(rng_seed: int = 0, noisy: bool = False) -> Image.Image:
    """A synthetic 'portrait': light-gray background + a skin-toned face box."""
    arr = np.full((_H, _W, 3), (225, 228, 232), dtype=np.uint8)  # light gray bg
    x0, y0, x1, y1 = _face_roi_bounds()
    skin = np.array([210, 170, 140], dtype=np.uint8)  # roughly skin-toned RGB
    if noisy:
        rng = np.random.default_rng(rng_seed)
        noise = rng.integers(-40, 40, size=(y1 - y0, x1 - x0, 3))
        patch = np.clip(skin.astype(int) + noise, 0, 255).astype(np.uint8)
    else:
        patch = np.tile(skin, (y1 - y0, x1 - x0, 1))
    arr[y0:y1, x0:x1] = patch
    return Image.fromarray(arr, "RGB")


# --------------------------------------------------------------------------- #
# BeautifyOptions
# --------------------------------------------------------------------------- #
def test_default_on_has_sensible_nonzero_intensities():
    opts = BeautifyOptions.default_on()
    assert opts.enabled is True
    assert 0 < opts.skin_smooth <= 1
    assert 0 < opts.auto_correct <= 1
    assert 0 < opts.background_whiten <= 1
    assert 0 < opts.teeth_eye_whiten <= 1


def test_options_clamp_out_of_range_intensities():
    opts = BeautifyOptions(enabled=True, skin_smooth=5.0, auto_correct=-2.0)
    assert opts.skin_smooth == 1.0
    assert opts.auto_correct == 0.0


def test_disabled_beautify_is_a_pure_noop():
    photo = _skin_photo()
    out = beautify(photo, BeautifyOptions(enabled=False, skin_smooth=1.0))
    assert out is photo  # short-circuits, doesn't even copy


# --------------------------------------------------------------------------- #
# Skin smoothing
# --------------------------------------------------------------------------- #
def test_skin_smooth_reduces_noise_variance_in_face_region():
    noisy = _skin_photo(noisy=True)
    smoothed = _smooth_skin(noisy, strength=1.0)

    x0, y0, x1, y1 = _face_roi_bounds()
    before = np.asarray(noisy)[y0:y1, x0:x1].astype(np.float32)
    after = np.asarray(smoothed)[y0:y1, x0:x1].astype(np.float32)
    assert after.std() < before.std()


def test_skin_smooth_leaves_far_corner_unchanged():
    noisy = _skin_photo(noisy=True)
    smoothed = _smooth_skin(noisy, strength=1.0)
    before = np.asarray(noisy)
    after = np.asarray(smoothed)
    # Top-left corner is well outside even the expanded face ROI.
    assert np.array_equal(before[:5, :5], after[:5, :5])


def test_skin_smooth_zero_strength_is_near_identity():
    noisy = _skin_photo(noisy=True)
    out = _smooth_skin(noisy, strength=0.0)
    before = np.asarray(noisy).astype(np.int16)
    after = np.asarray(out).astype(np.int16)
    assert np.abs(before - after).max() == 0


# --------------------------------------------------------------------------- #
# Brightness / color auto-correct
# --------------------------------------------------------------------------- #
def test_auto_correct_nudges_a_moderate_color_cast_toward_neutral():
    # Moderate cast (mirrors core/auto_edit.py's own "yellow" gentle-clamp
    # test): AutoEditor is deliberately gentle/clamped (see its module
    # docstring) so it won't fully neutralize an extreme cast, but a
    # moderate one should measurably shrink the channel spread.
    arr = np.zeros((_H, _W, 3), dtype=np.uint8)
    arr[:, :, 0] = 150  # R
    arr[:, :, 1] = 160  # G
    arr[:, :, 2] = 190  # B
    cast = Image.fromarray(arr, "RGB")

    corrected = _auto_correct(cast, strength=1.0)
    means_before = np.asarray(cast, dtype=np.float32).reshape(-1, 3).mean(axis=0)
    means_after = np.asarray(corrected, dtype=np.float32).reshape(-1, 3).mean(axis=0)

    spread_before = means_before.max() - means_before.min()
    spread_after = means_after.max() - means_after.min()
    assert spread_after < spread_before


def test_auto_correct_matches_manual_auto_editor_recipe():
    """`_auto_correct` should be equivalent to AutoEditor.analyze_array/apply_array
    with straighten/crop zeroed out -- not a separate, drifting implementation."""
    import dataclasses

    photo = _skin_photo()
    rgb = np.asarray(photo, dtype=np.uint8)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    editor = AutoEditor(strength=0.7)
    recipe = editor.analyze_array(bgr)
    gentle = dataclasses.replace(recipe, straighten_deg=0.0, crop=None)
    expected_bgr = editor.apply_array(bgr, gentle)
    expected_rgb = cv2.cvtColor(expected_bgr, cv2.COLOR_BGR2RGB)

    actual = np.asarray(_auto_correct(photo, strength=0.7))
    assert np.array_equal(actual, expected_rgb)


# --------------------------------------------------------------------------- #
# Teeth / eye whitening
# --------------------------------------------------------------------------- #
def _mouth_region_bounds():
    x, y, fw, fh = CANONICAL_FACE_BOX
    rx0, ry0, rx1, ry1 = (0.28, 0.62, 0.72, 0.85)
    x0 = int((x + rx0 * fw) * _W)
    y0 = int((y + ry0 * fh) * _H)
    x1 = int((x + rx1 * fw) * _W)
    y1 = int((y + ry1 * fh) * _H)
    return x0, y0, x1, y1


def _photo_with_dull_mouth() -> Image.Image:
    photo = _skin_photo()
    arr = np.asarray(photo).copy()
    x0, y0, x1, y1 = _mouth_region_bounds()
    # A believably dull/yellowish "teeth" patch: bright-ish but not neutral.
    arr[y0:y1, x0:x1] = (200, 190, 150)
    return Image.fromarray(arr, "RGB")


def test_whiten_teeth_eyes_brightens_and_desaturates_mouth_region():
    photo = _photo_with_dull_mouth()
    out = _whiten_teeth_eyes(photo, strength=1.0)

    x0, y0, x1, y1 = _mouth_region_bounds()
    before_hsv = cv2.cvtColor(np.asarray(photo)[y0:y1, x0:x1], cv2.COLOR_RGB2HSV).astype(np.float32)
    after_hsv = cv2.cvtColor(np.asarray(out)[y0:y1, x0:x1], cv2.COLOR_RGB2HSV).astype(np.float32)

    assert after_hsv[..., 2].mean() > before_hsv[..., 2].mean()  # brighter
    assert after_hsv[..., 1].mean() < before_hsv[..., 1].mean()  # less saturated


def test_whiten_teeth_eyes_zero_strength_is_identity():
    photo = _photo_with_dull_mouth()
    out = _whiten_teeth_eyes(photo, strength=0.0)
    assert np.array_equal(np.asarray(photo), np.asarray(out))


# --------------------------------------------------------------------------- #
# Background whitening (exercises the ellipse fallback -- rembg isn't
# installed in this environment, matching most real deployments until a
# studio opts into `pip install rembg`).
# --------------------------------------------------------------------------- #
def test_whiten_background_lightens_far_corner():
    photo = _skin_photo()
    out = _whiten_background(photo, strength=1.0)
    before = np.asarray(photo, dtype=np.float32)
    after = np.asarray(out, dtype=np.float32)
    # Corner is background under the ellipse fallback -> should brighten
    # toward the whitening color.
    assert after[:5, :5].mean() > before[:5, :5].mean()


def test_whiten_background_does_not_call_rembg_by_default(monkeypatch):
    """rembg/BiRefNet inference is far too slow for an interactive path (it
    previously hung the app hard enough to crash the machine), so the default
    must be the fast color-similarity mask."""
    import core.album.face_segmenter as seg

    called = {"n": 0}

    def _boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("subject_cutout must not be called by default")

    monkeypatch.setattr(seg, "subject_cutout", _boom)
    out = _whiten_background(_skin_photo(), strength=1.0)
    assert called["n"] == 0
    assert out.size == (_W, _H)


def test_whiten_background_uses_rembg_only_when_explicitly_opted_in(monkeypatch):
    import core.album.face_segmenter as seg

    called = {"n": 0}

    def _fake_cutout(image, **kwargs):
        called["n"] += 1
        return None  # force the color-similarity fallback, but prove it was asked

    monkeypatch.setattr(seg, "subject_cutout", _fake_cutout)
    _whiten_background(_skin_photo(), strength=1.0, use_rembg=True)
    assert called["n"] == 1


def test_beautify_options_default_to_rembg_off():
    assert BeautifyOptions().use_rembg is False
    assert BeautifyOptions.default_on().use_rembg is False


def test_whiten_background_zero_strength_is_identity():
    photo = _skin_photo()
    out = _whiten_background(photo, strength=0.0)
    assert np.array_equal(np.asarray(photo), np.asarray(out))


def test_whiten_background_partial_strength_is_between_none_and_full():
    photo = _skin_photo()
    none = np.asarray(_whiten_background(photo, strength=0.0), dtype=np.float32)
    half = np.asarray(_whiten_background(photo, strength=0.5), dtype=np.float32)
    full = np.asarray(_whiten_background(photo, strength=1.0), dtype=np.float32)
    corner_none, corner_half, corner_full = (
        none[:5, :5].mean(), half[:5, :5].mean(), full[:5, :5].mean()
    )
    assert corner_none <= corner_half <= corner_full


# --------------------------------------------------------------------------- #
# Combined pipeline
# --------------------------------------------------------------------------- #
def test_beautify_default_on_runs_all_effects_without_crashing():
    photo = _skin_photo(noisy=True)
    out = beautify(photo, BeautifyOptions.default_on())
    assert out.size == photo.size
    assert out.mode == "RGB"
    assert not np.array_equal(np.asarray(photo), np.asarray(out))


@pytest.mark.parametrize(
    "field",
    ["skin_smooth", "auto_correct", "background_whiten", "teeth_eye_whiten"],
)
def test_beautify_each_effect_alone_runs_without_crashing(field):
    photo = _skin_photo(noisy=True)
    opts = BeautifyOptions(enabled=True, **{field: 1.0})
    out = beautify(photo, opts)
    assert out.size == photo.size
