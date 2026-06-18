"""
Unit tests for core.scanner.

The scanner only inspects paths/extensions, so these tests use plain
placeholder files rather than real images.
"""

from pathlib import Path

import pytest

from core.scanner import ImageScanner, ScanError
from utils.config import load_config


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_finds_supported_images_recursively(tmp_path: Path):
    a = _touch(tmp_path / "a.jpg")
    b = _touch(tmp_path / "nested" / "b.png")
    _touch(tmp_path / "notes.txt")  # ignored

    found = ImageScanner().scan(tmp_path)

    assert found == sorted([a, b])


def test_results_are_sorted(tmp_path: Path):
    _touch(tmp_path / "z.jpg")
    _touch(tmp_path / "a.jpg")
    _touch(tmp_path / "m.jpg")

    found = ImageScanner().scan(tmp_path)

    assert found == sorted(found)


def test_extension_match_is_case_insensitive(tmp_path: Path):
    upper = _touch(tmp_path / "PHOTO.JPG")

    found = ImageScanner().scan(tmp_path)

    assert found == [upper]


def test_unsupported_extensions_ignored(tmp_path: Path):
    _touch(tmp_path / "a.gif")
    _touch(tmp_path / "b.txt")

    assert ImageScanner().scan(tmp_path) == []


def test_empty_folder_returns_empty_list(tmp_path: Path):
    assert ImageScanner().scan(tmp_path) == []


def test_nonexistent_folder_raises(tmp_path: Path):
    with pytest.raises(ScanError):
        ImageScanner().scan(tmp_path / "missing")


def test_file_path_raises(tmp_path: Path):
    f = _touch(tmp_path / "a.jpg")
    with pytest.raises(ScanError):
        ImageScanner().scan(f)


def test_empty_extensions_raises():
    with pytest.raises(ScanError):
        ImageScanner(supported_extensions=())


def test_extension_without_dot_raises():
    with pytest.raises(ScanError):
        ImageScanner(supported_extensions=("jpg",))


def test_from_config_uses_configured_extensions(tmp_path: Path):
    config = load_config()
    # Default config supports .png but not .gif.
    png = _touch(tmp_path / "a.png")
    _touch(tmp_path / "b.gif")

    found = ImageScanner.from_config(config).scan(tmp_path)

    assert found == [png]
