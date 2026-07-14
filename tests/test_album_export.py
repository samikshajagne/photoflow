"""
Unit tests for core.album.export.

Builds a small project manifest from real AlbumSpec/Spread/Placement objects and
an EditRecipe, then exercises the JSON write/read and the retouch round-trip.
"""

import pytest

from core.album.export import (
    AlbumExportError,
    JsonProjectExporter,
    MANIFEST_FILENAME,
    RETOUCH_DONE,
    RETOUCH_NEEDED,
    RETOUCH_NONE,
    build_manifest,
    export_album,
    load_manifest,
    pending_retouch,
    relink,
    update_retouch_status,
)
from core.album.layout import AlbumSpec, Placement, Spread
from core.auto_edit import EditRecipe


def _spec() -> AlbumSpec:
    return AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)


def _spreads() -> list[Spread]:
    placements = (
        Placement(path="/p/a.jpg", frame_px=(0, 0, 100, 100), crop=(0.0, 0.0, 1.0, 1.0)),
        Placement(path="/p/b.jpg", frame_px=(100, 0, 100, 100), crop=(0.1, 0.0, 0.8, 1.0)),
    )
    return [Spread(index=0, width_px=7200, height_px=3600, placements=placements)]


def _sections():
    return [("Cover", ["/p/a.jpg"]), ("Couple", ["/p/a.jpg", "/p/b.jpg"])]


def test_build_manifest_structure():
    manifest = build_manifest(
        _spec(),
        _sections(),
        _spreads(),
        recipes_by_path={"/p/a.jpg": EditRecipe.identity()},
        retouch_needed={"/p/b.jpg"},
    )

    assert manifest["version"] == 1
    assert manifest["spec"]["page_width_in"] == 12
    assert manifest["spec"]["spread_width_px"] == round(24 * 300)
    assert [s["name"] for s in manifest["sections"]] == ["Cover", "Couple"]
    assert manifest["spreads"][0]["placements"][0]["path"] == "/p/a.jpg"

    # Assets are the union of referenced photos, de-duplicated.
    paths = {a["path"] for a in manifest["assets"]}
    assert paths == {"/p/a.jpg", "/p/b.jpg"}
    by_path = {a["path"]: a for a in manifest["assets"]}
    assert by_path["/p/a.jpg"]["edit_recipe"] is not None
    assert by_path["/p/a.jpg"]["retouch_status"] == RETOUCH_NONE
    assert by_path["/p/b.jpg"]["retouch_status"] == RETOUCH_NEEDED


def test_export_and_load(tmp_path):
    out = export_album(tmp_path, _spec(), _sections(), _spreads())
    assert out.name == MANIFEST_FILENAME
    assert out.is_file()

    # load_manifest accepts the directory or the file.
    from_dir = load_manifest(tmp_path)
    from_file = load_manifest(out)
    assert from_dir == from_file
    assert len(from_dir["spreads"]) == 1


def test_load_missing_manifest_raises(tmp_path):
    with pytest.raises(AlbumExportError):
        load_manifest(tmp_path / "empty_dir")


def test_retouch_round_trip(tmp_path):
    manifest = build_manifest(
        _spec(), _sections(), _spreads(), retouch_needed={"/p/a.jpg", "/p/b.jpg"}
    )
    assert set(pending_retouch(manifest)) == {"/p/a.jpg", "/p/b.jpg"}

    relink(manifest, "/p/a.jpg", "/retouched/a_final.jpg")
    update_retouch_status(manifest, "/p/a.jpg", RETOUCH_DONE)

    by_path = {a["path"]: a for a in manifest["assets"]}
    assert by_path["/p/a.jpg"]["linked_path"] == "/retouched/a_final.jpg"
    assert by_path["/p/a.jpg"]["retouch_status"] == RETOUCH_DONE
    # Only b.jpg is still awaiting retouch.
    assert pending_retouch(manifest) == ["/p/b.jpg"]


def test_update_status_validates():
    manifest = build_manifest(_spec(), _sections(), _spreads())
    with pytest.raises(AlbumExportError):
        update_retouch_status(manifest, "/p/a.jpg", "bogus")
    with pytest.raises(AlbumExportError):
        update_retouch_status(manifest, "/does/not/exist.jpg", RETOUCH_DONE)


def test_relink_unknown_asset_raises():
    manifest = build_manifest(_spec(), _sections(), _spreads())
    with pytest.raises(AlbumExportError):
        relink(manifest, "/nope.jpg", "/x.jpg")


def test_json_exporter_is_a_layout_exporter():
    manifest = build_manifest(_spec(), _sections(), _spreads())
    exporter = JsonProjectExporter()
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        target = exporter.export(manifest, d)
        assert target.is_file()
