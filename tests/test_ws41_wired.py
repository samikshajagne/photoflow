"""WS 4.1 integration tests: flexible layouts wired into the render path."""

from __future__ import annotations

import types

from core.album.raster import _flexible_flag
from core.album.flexible_render import flexible_template_for
from core.album.template import SpreadTemplate
from core.album.spread_layout_calculator import rects_within_bounds


def _project(flexible=False):
    meta = types.SimpleNamespace(album_spec={"flexible_layout": flexible})
    return types.SimpleNamespace(meta=meta)


# --------------------------------------------------------------------------- #
# flag reader
# --------------------------------------------------------------------------- #
def test_flexible_flag_default_false():
    assert _flexible_flag(_project()) is False


def test_flexible_flag_enabled():
    assert _flexible_flag(_project(flexible=True)) is True


def test_flexible_flag_bad_meta():
    assert _flexible_flag(types.SimpleNamespace(meta=None)) is False


# --------------------------------------------------------------------------- #
# flexible_template_for
# --------------------------------------------------------------------------- #
def _aspects(mapping):
    return lambda p: mapping.get(p, 1.0)


def test_builds_adapted_template_covering_all_photos():
    paths = ["portrait.jpg", "group.jpg", "detail.jpg"]
    faces = {
        "portrait.jpg": ((0.3, 0.15, 0.4, 0.5),),                       # 1 big face
        "group.jpg": tuple((0.1 * i, 0.4, 0.07, 0.09) for i in range(3)),  # 3 faces
        "detail.jpg": ((0.48, 0.48, 0.03, 0.03),),                      # tiny face
    }
    asp = _aspects({"portrait.jpg": 0.75, "group.jpg": 1.5, "detail.jpg": 1.0})

    tmpl = flexible_template_for(paths, faces, "classic", aspect_fn=asp)
    assert isinstance(tmpl, SpreadTemplate)
    assert tmpl.photo_count == 3
    assert rects_within_bounds([s.rect for s in tmpl.slots])
    # At most one hero cutout.
    assert sum(1 for s in tmpl.slots if s.use_cutout) <= 1


def test_empty_returns_none():
    assert flexible_template_for([], {}, "classic") is None


def test_falls_back_to_none_on_error():
    def boom(_p):
        raise RuntimeError("bad read")

    # An aspect function that raises must not propagate — caller falls back.
    assert flexible_template_for(["a.jpg"], {}, "classic", aspect_fn=boom) is None


def test_theme_is_propagated():
    tmpl = flexible_template_for(["a.jpg", "b.jpg"], {}, "haldi",
                                 aspect_fn=_aspects({"a.jpg": 0.75, "b.jpg": 1.5}))
    assert tmpl is not None
    assert tmpl.theme == "haldi"
