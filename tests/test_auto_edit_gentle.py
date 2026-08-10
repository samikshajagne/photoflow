"""Tests for the gentler auto-edit (Phase 6d): tight clamps, strength, dead-zones."""

import cv2
import numpy as np

from core.auto_edit import AutoEditor


def _save(tmp_path, name, arr):
    p = str(tmp_path / name)
    cv2.imwrite(p, arr)
    return p


def test_strength_zero_is_identity(tmp_path):
    rng = np.random.default_rng(1)
    dark = np.clip(rng.normal(60, 30, (200, 200, 3)), 0, 255).astype(np.uint8)
    r = AutoEditor(strength=0.0).analyze(_save(tmp_path, "d.jpg", dark))
    assert r.exposure == 1.0
    assert r.contrast == 1.0
    assert r.white_balance_gains == (1.0, 1.0, 1.0)
    assert r.straighten_deg == 0.0


def test_wellexposed_neutral_is_near_identity(tmp_path):
    """
    A genuinely neutral, normal-contrast frame should be left alone.

    The noise is generated once in *grayscale* and replicated across R/G/B
    rather than drawn independently per channel. That matters: contrast is
    measured on luma, and luma is a weighted sum of the three channels, so
    independent per-channel noise partially cancels -- std 0.215 per channel
    comes out as luma std 0.143, well under the 0.22 reference the engine
    normalizes toward. The engine would then (correctly) boost contrast to
    its cap, and this test would fail while testing nothing it claimed to.
    Replicating one channel keeps luma std at ~0.215, i.e. actually normal
    contrast. Saved as PNG so lossy compression can't reshape the noise
    either.
    """
    rng = np.random.default_rng(0)
    gray = np.clip(rng.normal(128, 56, (200, 200)), 0, 255).astype(np.uint8)
    neutral = np.dstack([gray, gray, gray])
    r = AutoEditor().analyze(_save(tmp_path, "n.png", neutral))
    assert abs(r.exposure - 1.0) < 0.08
    assert all(abs(g - 1.0) < 0.1 for g in r.white_balance_gains)
    assert abs(r.contrast - 1.0) < 0.12


def test_genuinely_flat_image_still_gets_a_contrast_boost(tmp_path):
    """Guards the flip side of the test above: the gentle engine should still
    lift a genuinely low-contrast frame (this is what the previous version of
    that test was unintentionally exercising)."""
    rng = np.random.default_rng(0)
    gray = np.clip(rng.normal(128, 20, (200, 200)), 0, 255).astype(np.uint8)
    flat = np.dstack([gray, gray, gray])
    r = AutoEditor().analyze(_save(tmp_path, "flat.png", flat))
    assert r.contrast > 1.0


def test_dark_exposure_is_gentle_not_extreme(tmp_path):
    dark = np.full((200, 200, 3), 20, np.uint8)
    r = AutoEditor().analyze(_save(tmp_path, "vd.jpg", dark))
    assert 1.0 < r.exposure <= 1.4  # brightens, but capped (was up to 2.5 before)


def test_strong_colour_cast_not_over_neutralized(tmp_path):
    # A saturated yellow frame (BGR: low blue, high green/red). Old gray-world
    # would push the blue gain toward 2.0; now it stays within the tight band,
    # preserving the colour mood.
    yellow = np.zeros((200, 200, 3), np.uint8)
    yellow[:, :, 0], yellow[:, :, 1], yellow[:, :, 2] = 30, 200, 220
    r = AutoEditor().analyze(_save(tmp_path, "y.jpg", yellow))
    assert all(0.85 <= g <= 1.18 for g in r.white_balance_gains)
