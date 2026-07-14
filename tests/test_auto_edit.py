"""
Unit tests for core.auto_edit.

Recipes are derived from synthesized OpenCV images (flat color fields,
gradients) so the white-balance, exposure, contrast, and crop logic is
exercised end to end, and rendered back via ``apply`` to confirm the
non-destructive corrections behave as advertised. Helper methods are pure and
unit-tested directly where useful.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.auto_edit import (
    AutoEditError,
    AutoEditor,
    EditRecipe,
)


# --------------------------------------------------------------------------- #
# Image synthesis helpers
# --------------------------------------------------------------------------- #
def _save_bgr(path: Path, bgr: np.ndarray) -> Path:
    cv2.imwrite(str(path), bgr)
    return path


def _flat(path: Path, b: int, g: int, r: int, size: int = 64) -> Path:
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[:, :] = (b, g, r)
    return _save_bgr(path, img)


# --------------------------------------------------------------------------- #
# Construction / config
# --------------------------------------------------------------------------- #
def test_invalid_target_brightness_raises():
    with pytest.raises(AutoEditError):
        AutoEditor(target_brightness=0.0)
    with pytest.raises(AutoEditError):
        AutoEditor(target_brightness=1.0)
    with pytest.raises(AutoEditError):
        AutoEditor(target_brightness=-0.2)


def test_invalid_max_straighten_raises():
    with pytest.raises(AutoEditError):
        AutoEditor(max_straighten_deg=-1.0)


def test_from_config_returns_editor():
    # from_config takes no relevant keys; a None config still yields a default.
    editor = AutoEditor.from_config(config=None)
    assert isinstance(editor, AutoEditor)


# --------------------------------------------------------------------------- #
# White balance (gray-world)
# --------------------------------------------------------------------------- #
def test_neutral_image_yields_unit_gains(tmp_path: Path):
    path = _flat(tmp_path / "gray.png", 128, 128, 128)
    recipe = AutoEditor().analyze(path)
    r, g, b = recipe.white_balance_gains
    assert r == pytest.approx(1.0, abs=0.1)
    assert g == pytest.approx(1.0, abs=0.1)
    assert b == pytest.approx(1.0, abs=0.1)


def test_blue_cast_image_boosts_red_relative_to_blue(tmp_path: Path):
    # Strong blue cast: B high, R/G low. Gray-world should scale blue down and
    # red up, so the blue gain ends up below the red gain.
    path = _flat(tmp_path / "blue.png", 200, 110, 60)
    recipe = AutoEditor().analyze(path)
    r, g, b = recipe.white_balance_gains
    assert b < r
    assert b < g


# --------------------------------------------------------------------------- #
# Exposure
# --------------------------------------------------------------------------- #
def test_dark_image_brightens(tmp_path: Path):
    path = _flat(tmp_path / "dark.png", 40, 40, 40)
    recipe = AutoEditor().analyze(path)
    assert recipe.exposure > 1.0


def test_bright_image_darkens(tmp_path: Path):
    path = _flat(tmp_path / "bright.png", 220, 220, 220)
    recipe = AutoEditor().analyze(path)
    assert recipe.exposure < 1.0


def test_mid_image_exposure_moves_toward_target(tmp_path: Path):
    # A mid-gray image (~0.5 luma) with target 0.5 should need ~no exposure.
    path = _flat(tmp_path / "mid.png", 128, 128, 128)
    recipe = AutoEditor(target_brightness=0.5).analyze(path)
    assert recipe.exposure == pytest.approx(1.0, abs=0.1)


# --------------------------------------------------------------------------- #
# Crop (face-aware)
# --------------------------------------------------------------------------- #
def _contains(crop, box) -> bool:
    cx, cy, cw, ch = crop
    bx, by, bw, bh = box
    return (
        cx <= bx + 1e-9
        and cy <= by + 1e-9
        and cx + cw >= bx + bw - 1e-9
        and cy + ch >= by + bh - 1e-9
    )


def test_no_faces_yields_no_crop(tmp_path: Path):
    path = _flat(tmp_path / "gray.png", 128, 128, 128)
    recipe = AutoEditor().analyze(path)
    assert recipe.crop is None


def test_crop_contains_every_face_box(tmp_path: Path):
    path = _flat(tmp_path / "gray.png", 128, 128, 128, size=128)
    faces = [
        (0.20, 0.25, 0.15, 0.20),
        (0.55, 0.30, 0.12, 0.18),
    ]
    recipe = AutoEditor().analyze(path, face_regions=faces)
    crop = recipe.crop
    assert crop is not None
    cx, cy, cw, ch = crop
    # Within [0, 1].
    assert 0.0 <= cx <= 1.0
    assert 0.0 <= cy <= 1.0
    assert 0.0 < cw <= 1.0
    assert 0.0 < ch <= 1.0
    assert cx + cw <= 1.0 + 1e-9
    assert cy + ch <= 1.0 + 1e-9
    # Every face box fully inside the crop.
    for box in faces:
        assert _contains(crop, box)


def test_crop_contains_single_face(tmp_path: Path):
    path = _flat(tmp_path / "gray.png", 128, 128, 128, size=128)
    face = (0.6, 0.6, 0.2, 0.2)
    recipe = AutoEditor().analyze(path, face_regions=[face])
    assert recipe.crop is not None
    assert _contains(recipe.crop, face)
    cx, cy, cw, ch = recipe.crop
    assert cx + cw <= 1.0 + 1e-9
    assert cy + ch <= 1.0 + 1e-9


# --------------------------------------------------------------------------- #
# apply()
# --------------------------------------------------------------------------- #
def test_apply_dark_image_increases_brightness(tmp_path: Path):
    path = _flat(tmp_path / "dark.png", 40, 40, 40)
    editor = AutoEditor()
    recipe = editor.analyze(path)
    assert recipe.exposure > 1.0

    original = cv2.imdecode(
        np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    edited = editor.apply(path, recipe)
    assert edited.mean() > original.mean()
    assert edited.dtype == np.uint8


def test_apply_crop_reduces_dimensions(tmp_path: Path):
    path = _flat(tmp_path / "gray.png", 128, 128, 128, size=100)
    editor = AutoEditor()
    recipe = EditRecipe(
        white_balance_gains=(1.0, 1.0, 1.0),
        exposure=1.0,
        contrast=1.0,
        straighten_deg=0.0,
        crop=(0.25, 0.25, 0.5, 0.5),
    )
    edited = editor.apply(path, recipe)
    h, w = edited.shape[:2]
    assert h == pytest.approx(50, abs=1)
    assert w == pytest.approx(50, abs=1)


def test_apply_identity_preserves_dimensions(tmp_path: Path):
    path = _flat(tmp_path / "gray.png", 130, 120, 140, size=80)
    editor = AutoEditor()
    original = cv2.imdecode(
        np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    edited = editor.apply(path, EditRecipe.identity())
    assert edited.shape == original.shape
    # Identity is a no-op: pixels are unchanged.
    assert np.array_equal(edited, original)


# --------------------------------------------------------------------------- #
# EditRecipe round-trip / identity
# --------------------------------------------------------------------------- #
def test_recipe_round_trips_through_dict():
    recipe = EditRecipe(
        white_balance_gains=(1.1, 0.95, 0.8),
        exposure=1.3,
        contrast=1.15,
        straighten_deg=-2.5,
        crop=(0.1, 0.2, 0.6, 0.7),
    )
    assert EditRecipe.from_dict(recipe.as_dict()) == recipe


def test_recipe_round_trips_with_none_crop():
    recipe = EditRecipe.identity()
    assert EditRecipe.from_dict(recipe.as_dict()) == recipe


def test_as_dict_is_json_serializable():
    import json

    recipe = EditRecipe(
        white_balance_gains=(1.0, 1.0, 1.0),
        exposure=1.0,
        contrast=1.0,
        straighten_deg=0.0,
        crop=(0.0, 0.0, 1.0, 1.0),
    )
    # Should not raise.
    restored = EditRecipe.from_dict(json.loads(json.dumps(recipe.as_dict())))
    assert restored == recipe


def test_identity_is_no_op_recipe():
    recipe = EditRecipe.identity()
    assert recipe.white_balance_gains == (1.0, 1.0, 1.0)
    assert recipe.exposure == 1.0
    assert recipe.contrast == 1.0
    assert recipe.straighten_deg == 0.0
    assert recipe.crop is None


def test_from_dict_malformed_raises():
    with pytest.raises(AutoEditError):
        EditRecipe.from_dict({"exposure": 1.0})  # missing keys
    with pytest.raises(AutoEditError):
        EditRecipe.from_dict(
            {
                "white_balance_gains": [1.0, 1.0],  # wrong length
                "exposure": 1.0,
                "contrast": 1.0,
                "straighten_deg": 0.0,
                "crop": None,
            }
        )


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
def test_analyze_missing_file_raises(tmp_path: Path):
    with pytest.raises(AutoEditError):
        AutoEditor().analyze(tmp_path / "nope.png")


def test_analyze_corrupt_file_raises(tmp_path: Path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(AutoEditError):
        AutoEditor().analyze(bad)


def test_analyze_empty_file_raises(tmp_path: Path):
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(AutoEditError):
        AutoEditor().analyze(empty)


def test_apply_missing_file_raises(tmp_path: Path):
    with pytest.raises(AutoEditError):
        AutoEditor().apply(tmp_path / "nope.png", EditRecipe.identity())


def test_apply_rejects_non_recipe(tmp_path: Path):
    path = _flat(tmp_path / "gray.png", 128, 128, 128)
    with pytest.raises(AutoEditError):
        AutoEditor().apply(path, {"not": "a recipe"})
