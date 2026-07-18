"""
Unit tests for core.album.raster.

Builds a small album project with real spread geometry and two synthetic source
photos, then verifies the direct (no-Photoshop) renderer: spread geometry/placement
is correct, tonal edits are applied, missing assets are skipped, and all four
output formats (PNG / JPG / PDF / layered PSD) produce valid, openable files.
"""

import numpy as np
import pytest
from PIL import Image

from core.album.project import AlbumProject, PhotoRecord, SpreadRecord
from core.album.raster import (
    SUPPORTED_FORMATS,
    AlbumRenderError,
    export_jpg,
    export_pdf,
    export_png,
    export_psd,
    export_renders,
    render_spread,
    render_spread_template,
)


def _make_photo(path, color):
    """Write a solid-color 200x200 JPEG and return its path."""
    Image.new("RGB", (200, 200), color).save(path, "JPEG", quality=95)
    return str(path)


@pytest.fixture
def project(tmp_path):
    """A 400x200 single spread with two side-by-side 200x200 frames."""
    red = _make_photo(tmp_path / "a.jpg", (220, 20, 20))
    blue = _make_photo(tmp_path / "b.jpg", (20, 20, 220))

    proj = AlbumProject.new(str(tmp_path), album_spec={"dpi": 300})
    proj.add_photo(PhotoRecord(source_path=red))
    proj.add_photo(PhotoRecord(source_path=blue))
    proj.spreads = [
        SpreadRecord(
            index=0,
            section="Cover",
            width_px=400,
            height_px=200,
            placements=[
                {"path": red, "frame_px": [0, 0, 200, 200], "crop": [0.0, 0.0, 1.0, 1.0]},
                {"path": blue, "frame_px": [200, 0, 200, 200], "crop": [0.0, 0.0, 1.0, 1.0]},
            ],
        )
    ]
    return proj


def _close(actual, expected, tol=6):
    """JPEG round-trips colors approximately; compare within a tolerance."""
    return all(abs(a - e) <= tol for a, e in zip(actual, expected))


def test_render_spread_places_photos_in_frames(project):
    img = render_spread(project, project.spreads[0], apply_edits=False)
    assert img.size == (400, 200)
    assert _close(img.getpixel((100, 100)), (220, 20, 20))
    assert _close(img.getpixel((300, 100)), (20, 20, 220))


def test_render_spread_template_size_and_content(project):
    # The template renderer produces a spread of the same canvas size, with
    # both photos composited into shaped slots (so saturated red + blue pixels
    # are present) over a lighter sampled background.
    img = render_spread_template(project, project.spreads[0], apply_edits=False)
    assert img.size == (400, 200)
    assert img.mode == "RGB"
    arr = np.asarray(img)
    reddish = ((arr[:, :, 0] > 150) & (arr[:, :, 2] < 90)).sum()
    bluish = ((arr[:, :, 2] > 150) & (arr[:, :, 0] < 90)).sum()
    assert reddish > 50 and bluish > 50


def test_render_spread_template_skips_missing(project):
    project.spreads[0].placements.append(
        {"path": "/nope/missing.jpg", "frame_px": [0, 0, 50, 50], "crop": [0, 0, 1, 1]}
    )
    skipped: list = []
    img = render_spread_template(project, project.spreads[0], apply_edits=False, skipped=skipped)
    assert img.size == (400, 200)
    assert "/nope/missing.jpg" in skipped


def test_is_section_opener_flags_first_spread(tmp_path):
    from core.album.raster import _is_section_opener

    proj = AlbumProject.new(str(tmp_path), album_spec={"dpi": 100})
    s0 = SpreadRecord(index=0, section="Haldi", width_px=100, height_px=100, placements=[])
    s1 = SpreadRecord(index=1, section="Haldi", width_px=100, height_px=100, placements=[])
    s2 = SpreadRecord(index=2, section="Reception", width_px=100, height_px=100, placements=[])
    proj.spreads = [s0, s1, s2]
    assert _is_section_opener(proj, s0) is True    # first Haldi spread
    assert _is_section_opener(proj, s1) is False   # second Haldi spread
    assert _is_section_opener(proj, s2) is True    # first Reception spread


def test_section_theme_is_consistent_across_spreads(tmp_path):
    # Two spreads in one section share the same (photo-tinted) background; a
    # spread in a different section gets a different background.
    yellow = _make_photo(tmp_path / "y1.jpg", (230, 200, 30))
    yellow2 = _make_photo(tmp_path / "y2.jpg", (235, 205, 35))
    blue = _make_photo(tmp_path / "b1.jpg", (30, 60, 200))
    proj = AlbumProject.new(str(tmp_path), album_spec={"dpi": 100})
    for p in (yellow, yellow2, blue):
        proj.add_photo(PhotoRecord(source_path=p))

    def _spread(i, section, paths):
        return SpreadRecord(
            index=i, section=section, width_px=800, height_px=400,
            placements=[{"path": p, "frame_px": [0, 0, 400, 400], "crop": [0, 0, 1, 1]} for p in paths],
        )

    proj.spreads = [
        _spread(0, "Haldi", [yellow]),
        _spread(1, "Haldi", [yellow2]),
        _spread(2, "Reception", [blue]),
    ]
    bg = [
        render_spread_template(proj, s, apply_edits=False).getpixel((2, 2))
        for s in proj.spreads
    ]
    assert bg[0] == bg[1]     # same section -> identical background
    assert bg[0] != bg[2]     # different section -> different background
    assert bg[0][0] >= bg[2][0]  # warm Haldi tint has >= red than the blue one


def test_crop_maps_to_source_region(project, tmp_path):
    arr = np.zeros((100, 100, 3), dtype=np.uint8)
    arr[:, :50] = (255, 0, 0)
    arr[:, 50:] = (0, 255, 0)
    half = str(tmp_path / "half.jpg")
    Image.fromarray(arr, "RGB").save(half, "JPEG", quality=100)
    project.add_photo(PhotoRecord(source_path=half))
    project.spreads[0].placements = [
        {"path": half, "frame_px": [0, 0, 400, 200], "crop": [0.5, 0.0, 0.5, 1.0]},
    ]
    img = render_spread(project, project.spreads[0], apply_edits=False)
    r, g, b = img.getpixel((200, 100))
    assert g > 200 and r < 60


def test_tonal_edit_applied(project):
    red_path = project.spreads[0].placements[0]["path"]
    project.get(red_path).edit_recipe = {
        "white_balance_gains": [1.0, 1.0, 1.0],
        "exposure": 2.0,
        "contrast": 1.0,
        "straighten_deg": 0.0,
        "crop": None,
    }
    plain = render_spread(project, project.spreads[0], apply_edits=False)
    edited = render_spread(project, project.spreads[0], apply_edits=True)
    assert edited.getpixel((100, 100))[0] > plain.getpixel((100, 100))[0]


def test_missing_source_is_skipped(project):
    project.spreads[0].placements.append(
        {"path": "/does/not/exist.jpg", "frame_px": [0, 0, 50, 50], "crop": [0, 0, 1, 1]}
    )
    img = render_spread(project, project.spreads[0], apply_edits=False)
    assert img.size == (400, 200)


def test_export_png(project, tmp_path):
    paths = export_png(tmp_path / "renders", project)
    assert len(paths) == 1 and paths[0].exists()
    with Image.open(paths[0]) as im:
        assert im.format == "PNG" and im.size == (400, 200)


def test_export_jpg(project, tmp_path):
    paths = export_jpg(tmp_path / "renders", project)
    assert len(paths) == 1 and paths[0].suffix == ".jpg"
    with Image.open(paths[0]) as im:
        assert im.format == "JPEG"


def test_export_pdf_single_multipage_file(project, tmp_path):
    project.spreads.append(
        SpreadRecord(
            index=1,
            section="Closing",
            width_px=400,
            height_px=200,
            placements=list(project.spreads[0].placements),
        )
    )
    pdf = export_pdf(tmp_path / "renders", project)
    assert pdf.exists() and pdf.suffix == ".pdf"
    assert pdf.read_bytes()[:4] == b"%PDF"


def test_export_psd_is_layered_and_openable(project, tmp_path):
    psd_tools = pytest.importorskip("psd_tools")
    paths = export_psd(tmp_path / "renders", project)
    assert len(paths) == 1 and paths[0].exists()
    psd = psd_tools.PSDImage.open(paths[0])
    names = [layer.name for layer in psd]
    assert "background" in names
    assert len(names) == 3
    assert psd.composite().size == (400, 200)


def test_export_renders_dispatch_and_validation(project, tmp_path):
    results = export_renders(tmp_path / "out", project, ["png", "PDF"])
    assert set(results) == {"png", "pdf"}
    with pytest.raises(AlbumRenderError):
        export_renders(tmp_path / "out", project, ["png", "tiff"])


def test_no_spreads_raises(tmp_path):
    proj = AlbumProject.new(str(tmp_path), album_spec={"dpi": 300})
    with pytest.raises(AlbumRenderError):
        export_png(tmp_path / "out", proj)


def test_supported_formats_constant():
    assert set(SUPPORTED_FORMATS) == {"png", "jpg", "pdf", "psd"}


def test_linked_path_is_rendered_instead_of_original(project, tmp_path):
    # The left frame's photo is red; point its linked_path at a green file.
    green = str(tmp_path / "retouched.jpg")
    Image.new("RGB", (200, 200), (20, 220, 20)).save(green, "JPEG", quality=95)
    red_path = project.spreads[0].placements[0]["path"]
    project.get(red_path).linked_path = green
    img = render_spread(project, project.spreads[0], apply_edits=False)
    r, g, b = img.getpixel((100, 100))
    assert g > 200 and r < 60  # the retouched (green) file was used


def test_skipped_list_records_missing_sources(project):
    project.spreads[0].placements.append(
        {"path": "/does/not/exist.jpg", "frame_px": [0, 0, 50, 50], "crop": [0, 0, 1, 1]}
    )
    skipped: list = []
    render_spread(project, project.spreads[0], apply_edits=False, skipped=skipped)
    assert skipped == ["/does/not/exist.jpg"]


def test_export_reports_per_spread_progress(project, tmp_path):
    # Two spreads, one format -> progress should advance per spread and end full.
    project.spreads.append(
        SpreadRecord(
            index=1,
            section="Closing",
            width_px=400,
            height_px=200,
            placements=list(project.spreads[0].placements),
        )
    )
    calls: list = []
    export_renders(
        tmp_path / "out", project, ["pdf"],
        progress_cb=lambda done, total, msg: calls.append((done, total)),
    )
    assert calls, "progress callback was never called"
    # Total reflects spreads (2), not just formats (1); ends at 100%.
    assert max(t for _, t in calls) == 2
    assert calls[-1][0] == calls[-1][1]


def test_contain_letterboxes_without_cropping(project, tmp_path):
    # A wide (200x100) photo in a tall 200x200 frame, fit=contain, should be
    # scaled to 200x100 and centered: white background above/below, photo in
    # the middle band -- nothing cropped.
    import numpy as np
    from PIL import Image as PILImage

    wide = str(tmp_path / "wide.jpg")
    arr = np.zeros((100, 200, 3), dtype=np.uint8)
    arr[:, :] = (0, 200, 0)
    PILImage.fromarray(arr, "RGB").save(wide, "JPEG", quality=100)
    project.add_photo(PhotoRecord(source_path=wide))
    project.spreads[0].placements = [
        {"path": wide, "frame_px": [0, 0, 200, 200], "crop": [0, 0, 1, 1], "fit": "contain"},
    ]
    img = render_spread(project, project.spreads[0], apply_edits=False)
    # Top band is background (white); vertical center is the green photo.
    assert _close(img.getpixel((100, 5)), (255, 255, 255))
    assert img.getpixel((100, 100))[1] > 180  # green present in the middle


def test_export_renders_threads_skipped(project, tmp_path):
    project.spreads[0].placements.append(
        {"path": "/missing/x.jpg", "frame_px": [0, 0, 10, 10], "crop": [0, 0, 1, 1]}
    )
    skipped: list = []
    export_renders(tmp_path / "out", project, ["png"], skipped=skipped)
    assert "/missing/x.jpg" in skipped
