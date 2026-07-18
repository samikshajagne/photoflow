"""WS 4.4 integration tests: designed cover wired into the render path."""

from __future__ import annotations

import types

from PIL import Image

import core.album.raster as rm


def _project(designed=False, title="Aisha & Rohan", date="2025"):
    meta = types.SimpleNamespace(
        album_spec={"cover_title": title, "cover_date": date, "designed_cover": designed}
    )
    return types.SimpleNamespace(meta=meta)


def test_designed_cover_flag_default_false():
    assert rm._designed_cover_flag(_project()) is False


def test_designed_cover_flag_enabled():
    assert rm._designed_cover_flag(_project(designed=True)) is True


def test_designed_cover_flag_bad_meta():
    assert rm._designed_cover_flag(types.SimpleNamespace(meta=None)) is False


def test_designed_cover_composes_image(tmp_path, monkeypatch):
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (600, 400), (0, 150, 0)).save(hero)
    monkeypatch.setattr(rm, "_resolve_source", lambda proj, p: (hero, None))

    cover = rm._designed_cover(
        _project(designed=True), [str(hero)], {}, (180, 50, 50), 800, 600, False
    )
    assert cover is not None
    assert cover.size == (800, 600)
    assert cover.mode == "RGB"


def test_designed_cover_missing_source_returns_none(tmp_path, monkeypatch):
    missing = tmp_path / "nope.jpg"
    monkeypatch.setattr(rm, "_resolve_source", lambda proj, p: (missing, None))
    cover = rm._designed_cover(_project(designed=True), ["nope.jpg"], {}, None, 800, 600, False)
    assert cover is None  # graceful fallback, not a crash
