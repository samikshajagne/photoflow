"""
Unit tests for core.duplicate_detector.

These synthesize small images on disk (via Pillow) rather than relying on
fixture files, so the suite is self-contained and deterministic. The image
generators are chosen so their perceptual-hash Hamming distances are stable
and well-separated:

- an exact byte-for-byte copy        -> distance 0
- a resized round-trip of the base   -> small distance (~4), a "near" dup
- a gradient with different params    -> large distance (~34), distinct

No blur/face/quality/UI/persistence behavior is exercised here — this module
only tests duplicate detection.
"""

from pathlib import Path

import pytest

from core.duplicate_detector import (
    DEFAULT_HASH_DISTANCE_MAX,
    DuplicateDetectionError,
    DuplicateDetector,
)
from utils.config import load_config

from PIL import Image


# --------------------------------------------------------------------------- #
# Image fixture helpers
# --------------------------------------------------------------------------- #
def _gradient_image(ax: int = 3, ay: int = 7, offset: int = 0, size: int = 128) -> Image.Image:
    """Build a structured RGB gradient whose perceptual hash is content-dependent."""
    img = Image.new("RGB", (size, size))
    pixels = []
    for y in range(size):
        for x in range(size):
            v = (x * ax + y * ay + offset) % 256
            pixels.append((v, (v * 2) % 256, (v * 5) % 256))
    img.putdata(pixels)
    return img


def _save(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def _save_base(path: Path) -> Path:
    """A reference image (the 'original')."""
    return _save(_gradient_image(ax=3, ay=7), path)


def _save_near_duplicate(path: Path) -> Path:
    """A lightly-degraded copy of the base image (small, nonzero hash distance)."""
    base = _gradient_image(ax=3, ay=7)
    near = base.resize((110, 110)).resize((128, 128))
    return _save(near, path)


def _save_distinct(path: Path) -> Path:
    """An image whose content is far from the base (large hash distance)."""
    return _save(_gradient_image(ax=11, ay=2, offset=40), path)


# --------------------------------------------------------------------------- #
# Detection behavior
# --------------------------------------------------------------------------- #
def test_no_duplicates_returns_empty_groups(tmp_path: Path):
    _save_base(tmp_path / "a.png")
    _save_distinct(tmp_path / "b.png")

    result = DuplicateDetector().detect(tmp_path)

    assert result == {"groups": []}


def test_detects_exact_duplicates(tmp_path: Path):
    base = _save_base(tmp_path / "original.png")
    # Byte-for-byte copy -> Hamming distance 0.
    copy = tmp_path / "copy.png"
    copy.write_bytes(base.read_bytes())

    result = DuplicateDetector().detect(tmp_path)

    assert len(result["groups"]) == 1
    group = result["groups"][0]
    members = {group["representative"], *group["duplicates"]}
    assert members == {str(base), str(copy)}
    assert len(group["duplicates"]) == 1


def test_detects_near_duplicates(tmp_path: Path):
    base = _save_base(tmp_path / "a.png")
    near = _save_near_duplicate(tmp_path / "b.png")

    result = DuplicateDetector(hash_distance_max=DEFAULT_HASH_DISTANCE_MAX).detect(tmp_path)

    assert len(result["groups"]) == 1
    members = {result["groups"][0]["representative"], *result["groups"][0]["duplicates"]}
    assert members == {str(base), str(near)}


def test_threshold_zero_excludes_near_duplicates(tmp_path: Path):
    _save_base(tmp_path / "a.png")
    _save_near_duplicate(tmp_path / "b.png")

    # With a strict threshold of 0, only visually identical images group.
    result = DuplicateDetector(hash_distance_max=0).detect(tmp_path)

    assert result == {"groups": []}


def test_threshold_zero_still_catches_exact_duplicates(tmp_path: Path):
    base = _save_base(tmp_path / "a.png")
    copy = tmp_path / "b.png"
    copy.write_bytes(base.read_bytes())

    result = DuplicateDetector(hash_distance_max=0).detect(tmp_path)

    assert len(result["groups"]) == 1


def test_higher_threshold_groups_more_aggressively(tmp_path: Path):
    _save_base(tmp_path / "a.png")
    _save_distinct(tmp_path / "b.png")

    # A deliberately large threshold collapses even the distinct image in.
    result = DuplicateDetector(hash_distance_max=40).detect(tmp_path)

    assert len(result["groups"]) == 1
    assert len(result["groups"][0]["duplicates"]) == 1


def test_scan_is_recursive(tmp_path: Path):
    base = _save_base(tmp_path / "a.png")
    nested = _save_base(tmp_path / "nested" / "deeper" / "b.png")

    result = DuplicateDetector().detect(tmp_path)

    assert len(result["groups"]) == 1
    members = {result["groups"][0]["representative"], *result["groups"][0]["duplicates"]}
    assert members == {str(base), str(nested)}


def test_representative_is_lexicographically_first(tmp_path: Path):
    first = _save_base(tmp_path / "aaa.png")
    base = _gradient_image(ax=3, ay=7)
    second = tmp_path / "zzz.png"
    second.write_bytes(first.read_bytes())

    result = DuplicateDetector().detect(tmp_path)

    assert result["groups"][0]["representative"] == str(first)
    assert result["groups"][0]["duplicates"] == [str(second)]


def test_unsupported_extensions_are_ignored(tmp_path: Path):
    base = _save_base(tmp_path / "a.png")
    # Same content, but an extension the detector isn't configured to scan.
    ignored = tmp_path / "a.gif"
    _gradient_image(ax=3, ay=7).save(ignored, format="GIF")

    result = DuplicateDetector().detect(tmp_path)

    assert result == {"groups": []}
    assert base.exists()  # detector never mutates inputs


def test_extension_match_is_case_insensitive(tmp_path: Path):
    base = _save_base(tmp_path / "a.png")
    upper = tmp_path / "B.PNG"
    upper.write_bytes(base.read_bytes())

    result = DuplicateDetector().detect(tmp_path)

    assert len(result["groups"]) == 1


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
def test_corrupt_image_is_skipped_not_fatal(tmp_path: Path):
    base = _save_base(tmp_path / "good_a.png")
    copy = tmp_path / "good_b.png"
    copy.write_bytes(base.read_bytes())
    # A file with an image extension but garbage content.
    (tmp_path / "broken.png").write_bytes(b"this is not a valid image")

    result = DuplicateDetector().detect(tmp_path)

    # The corrupt file is skipped; the real duplicates still group.
    assert len(result["groups"]) == 1
    members = {result["groups"][0]["representative"], *result["groups"][0]["duplicates"]}
    assert members == {str(base), str(copy)}


def test_nonexistent_folder_raises(tmp_path: Path):
    with pytest.raises(DuplicateDetectionError):
        DuplicateDetector().detect(tmp_path / "does_not_exist")


def test_path_that_is_a_file_raises(tmp_path: Path):
    file_path = _save_base(tmp_path / "a.png")
    with pytest.raises(DuplicateDetectionError):
        DuplicateDetector().detect(file_path)


def test_empty_folder_returns_empty_groups(tmp_path: Path):
    result = DuplicateDetector().detect(tmp_path)
    assert result == {"groups": []}


# --------------------------------------------------------------------------- #
# Construction / configuration
# --------------------------------------------------------------------------- #
def test_negative_threshold_raises():
    with pytest.raises(DuplicateDetectionError):
        DuplicateDetector(hash_distance_max=-1)


def test_empty_extensions_raises():
    with pytest.raises(DuplicateDetectionError):
        DuplicateDetector(supported_extensions=())


def test_extension_without_dot_raises():
    with pytest.raises(DuplicateDetectionError):
        DuplicateDetector(supported_extensions=("jpg",))


def test_too_small_hash_size_raises():
    with pytest.raises(DuplicateDetectionError):
        DuplicateDetector(hash_size=1)


def test_from_config_uses_configured_threshold():
    config = load_config()
    detector = DuplicateDetector.from_config(config)

    assert detector.hash_distance_max == config.thresholds.duplicate_hash_distance_max


def test_from_config_detector_works_end_to_end(tmp_path: Path):
    base = _save_base(tmp_path / "a.png")
    copy = tmp_path / "b.png"
    copy.write_bytes(base.read_bytes())

    detector = DuplicateDetector.from_config(load_config())
    result = detector.detect(tmp_path)

    assert len(result["groups"]) == 1


def test_result_is_json_serializable(tmp_path: Path):
    import json

    base = _save_base(tmp_path / "a.png")
    copy = tmp_path / "b.png"
    copy.write_bytes(base.read_bytes())

    result = DuplicateDetector().detect(tmp_path)

    # Round-trips cleanly -> structure contains only primitives.
    assert json.loads(json.dumps(result)) == result
