"""Tests for preview rendering support (Phase 7a): spec override + preview_spec."""

from PIL import Image

from core.album.layout import AlbumSpec
from core.album.project import AlbumProject, PhotoRecord, SpreadRecord
from core.album.raster import preview_spec, render_spread_template


def test_preview_spec_lowers_dpi_keeps_size():
    full = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)
    pv = preview_spec(full)
    assert pv.dpi < full.dpi
    assert pv.spread_width_in == full.spread_width_in   # same physical page size
    assert max(pv.spread_width_px, pv.spread_height_px) <= 1300  # small + fast


def test_render_with_spec_override_uses_spec_dims(tmp_path):
    photo = str(tmp_path / "a.jpg")
    Image.new("RGB", (400, 400), (200, 50, 50)).save(photo)
    proj = AlbumProject.new(str(tmp_path), album_spec={"dpi": 300})
    proj.add_photo(PhotoRecord(source_path=photo))
    # SpreadRecord says 7200x3600, but the spec override should win.
    proj.spreads = [
        SpreadRecord(
            index=0, section="Cover", width_px=7200, height_px=3600,
            placements=[{"path": photo, "frame_px": [0, 0, 10, 10], "crop": [0, 0, 1, 1]}],
        )
    ]
    small = AlbumSpec(page_width_in=12, page_height_in=12, dpi=50)
    img = render_spread_template(proj, proj.spreads[0], apply_edits=False, spec=small)
    assert img.size == (small.spread_width_px, small.spread_height_px)
    assert img.size != (7200, 3600)
