"""WS 4.3.3 integration: theme_backgrounds flag reader."""

from __future__ import annotations

import types

import core.album.raster as rm


def _project(themed=False):
    return types.SimpleNamespace(
        meta=types.SimpleNamespace(album_spec={"theme_backgrounds": themed})
    )


def test_theme_backgrounds_flag_default_false():
    assert rm._theme_backgrounds_flag(_project()) is False


def test_theme_backgrounds_flag_enabled():
    assert rm._theme_backgrounds_flag(_project(themed=True)) is True


def test_theme_backgrounds_flag_bad_meta():
    assert rm._theme_backgrounds_flag(types.SimpleNamespace(meta=None)) is False
