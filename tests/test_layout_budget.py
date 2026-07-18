"""
Tests for the page-budget density and EXIF orientation fixes (Phase 6a).
"""

from PIL import Image

from core.album.layout_select import LayoutSelector
from core.album.project import AlbumProject, SectionRecord


def _project(counts):
    """Build a project whose sections have the given (kind, photo_count)."""
    proj = AlbumProject.new("/x")
    proj.sections = [
        SectionRecord(f"S{i}", kind, [f"p{i}_{j}.jpg" for j in range(n)])
        for i, (kind, n) in enumerate(counts)
    ]
    return proj


def _ceil(a, b):
    return -(-a // b)


def test_budget_auto_targets_20_30():
    sel = LayoutSelector()  # auto target = 25
    proj = _project([("cover", 1), ("highlights", 120), ("ceremony", 100)])
    per = sel._budget_per_spread(proj)
    assert per == _ceil(220, 24)  # ceil(non-cover total / (target-1))


def test_budget_explicit_target():
    sel = LayoutSelector(target_pages=10)
    proj = _project([("cover", 1), ("ceremony", 90)])
    assert sel._budget_per_spread(proj) == _ceil(90, 9)


def test_budget_minimum_two_per_spread():
    sel = LayoutSelector(target_pages=200)  # absurdly high -> tiny per, clamped
    proj = _project([("ceremony", 10)])
    assert sel._budget_per_spread(proj) >= 2


def test_aspect_honors_exif_orientation(tmp_path):
    # Landscape pixels (200x100) + orientation=6 => displayed portrait.
    img = Image.new("RGB", (200, 100), (10, 120, 200))
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 90° CW on display
    path = tmp_path / "rotated.jpg"
    img.save(path, exif=exif)
    assert LayoutSelector._aspect(str(path)) < 1.0  # reported as portrait


def test_aspect_plain_landscape():
    # (no EXIF) a wide image reads as landscape (>1).
    import tempfile
    import os

    d = tempfile.mkdtemp()
    p = os.path.join(d, "wide.jpg")
    Image.new("RGB", (300, 100), (0, 0, 0)).save(p)
    assert LayoutSelector._aspect(p) > 1.0
