"""WS 3.2 integration tests: subject-aware slot ordering wired into render path."""
from __future__ import annotations
import types
import builtins

from core.album.raster import _album_flags, _order_by_content
from core.album.template import SHAPE_RECT, SpreadTemplate, TemplateSlot, Background, BG_SOLID


def _make_template(slot_rects):
    slots = tuple(TemplateSlot(rect=r, shape=SHAPE_RECT, border=0.0, shadow=False) for r in slot_rects)
    return SpreadTemplate(name="test", theme="classic", slots=slots, background=Background(type=BG_SOLID, color="#FFFFFF"))


def _project(smart=True, cutouts=False):
    meta = types.SimpleNamespace(album_spec={"smart_slot_ordering": smart, "use_cutouts": cutouts})
    return types.SimpleNamespace(meta=meta)


def test_album_flags_defaults():
    smart, cutouts = _album_flags(_project())
    assert smart is True and cutouts is False


def test_album_flags_disable_smart():
    smart, _ = _album_flags(_project(smart=False))
    assert smart is False


def test_album_flags_enable_cutouts():
    _, cutouts = _album_flags(_project(cutouts=True))
    assert cutouts is True


def test_album_flags_bad_meta():
    smart, cutouts = _album_flags(types.SimpleNamespace(meta=None))
    assert smart is True and cutouts is False


def test_order_by_content_portrait_to_tall_slot():
    """Portrait photo (face box, tall aspect) -> tall slot (index 0)."""
    portrait, landscape = "portrait.jpg", "landscape.jpg"
    # slot-0: rect (x, y, w=0.4, h=1.0) -> aspect 0.4 (tall/portrait)
    # slot-1: rect (x, y, w=0.58, h=0.4) -> aspect 1.45 (wide/landscape)
    template = _make_template([(0.0, 0.0, 0.4, 1.0), (0.42, 0.0, 0.58, 0.4)])
    faces_by_path = {portrait: ((0.3, 0.2, 0.4, 0.45),), landscape: ()}

    import core.album.raster as rm
    orig = rm._photo_aspect
    rm._photo_aspect = lambda p: 0.75 if p == portrait else 1.78
    try:
        result = _order_by_content([portrait, landscape], template, 1200, 800, faces_by_path)
    finally:
        rm._photo_aspect = orig

    assert result[0] == portrait, f"portrait should be in slot-0, got {result[0]}"
    assert result[1] == landscape, f"landscape should be in slot-1, got {result[1]}"


def test_order_by_content_single_unchanged():
    template = _make_template([(0.0, 0.0, 1.0, 1.0)])
    assert _order_by_content(["only.jpg"], template, 1200, 800, {}) == ["only.jpg"]


def test_order_by_content_deterministic():
    template = _make_template([(0.0, 0.0, 0.4, 1.0), (0.42, 0.0, 0.58, 0.4)])
    paths = ["a.jpg", "b.jpg"]
    faces = {"a.jpg": ((0.3, 0.2, 0.4, 0.45),), "b.jpg": ()}
    import core.album.raster as rm
    orig = rm._photo_aspect
    rm._photo_aspect = lambda p: 0.75 if p == "a.jpg" else 1.78
    try:
        r1 = _order_by_content(paths, template, 1200, 800, faces)
        r2 = _order_by_content(paths, template, 1200, 800, faces)
    finally:
        rm._photo_aspect = orig
    assert r1 == r2


def test_order_by_content_fallback_on_import_error():
    """If slot_matcher import fails, falls back gracefully."""
    real_import = builtins.__import__
    def broken(name, *a, **kw):
        if name == "core.album.slot_matcher":
            raise ImportError("simulated")
        return real_import(name, *a, **kw)
    builtins.__import__ = broken
    try:
        template = _make_template([(0.0, 0.0, 0.4, 1.0), (0.42, 0.0, 0.58, 0.4)])
        result = _order_by_content(["a.jpg", "b.jpg"], template, 1200, 800, {})
        assert set(result) == {"a.jpg", "b.jpg"}
    finally:
        builtins.__import__ = real_import
