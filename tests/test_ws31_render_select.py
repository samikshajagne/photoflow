"""
WS 3.1 integration tests: the *rendered* spread and the layout selector both
honour faces (previously the renderer centre-cropped and dropped them).
"""

from __future__ import annotations

from PIL import Image

from core.album.layout import AlbumSpec
from core.album.layout_select import LayoutSelector
from core.album.project import AlbumProject, SectionRecord
from core.album.template import (
    Background,
    SpreadTemplate,
    TemplateSlot,
    _fit,
    render_spread,
)

_RED = (255, 0, 0)
_BLUE = (0, 0, 255)


def _tall_with_red_top(w=100, h=300, red_frac=0.2) -> Image.Image:
    img = Image.new("RGB", (w, h), _BLUE)
    for y in range(int(h * red_frac)):
        for x in range(w):
            img.putpixel((x, y), _RED)
    return img


def _has_color(img: Image.Image, color, tol=40) -> bool:
    px = img.convert("RGB").load()
    w, h = img.size
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = px[x, y]
            if abs(r - color[0]) + abs(g - color[1]) + abs(b - color[2]) <= tol:
                return True
    return False


def test_fit_cover_keeps_top_face_drops_it_when_centered():
    img = _tall_with_red_top()
    face = [(0.4, 0.0, 0.2, 0.08)]  # a face in the red top strip
    out_face = _fit(img, 100, 100, "cover", face)
    out_centered = _fit(img, 100, 100, "cover", ())
    # Face-safe keeps the red (face) zone; centered crop of a tall photo drops it.
    assert _has_color(out_face, _RED)
    assert not _has_color(out_centered, _RED)


def test_render_spread_uses_face_boxes():
    spec = AlbumSpec(page_width_in=6, page_height_in=6, dpi=100, margin_in=0.0)
    template = SpreadTemplate(
        name="one",
        theme="classic",
        slots=(TemplateSlot(rect=(0.0, 0.0, 1.0, 1.0)),),
        background=Background(type="solid", color="#FFFFFF"),
    )
    img = _tall_with_red_top()
    loader = lambda _p: img  # noqa: E731 - tiny test stub

    with_faces = render_spread(
        template, ["x.jpg"], spec, loader=loader,
        face_boxes_by_index=[[(0.4, 0.0, 0.2, 0.08)]],
    )
    without = render_spread(template, ["x.jpg"], spec, loader=loader)

    assert _has_color(with_faces, _RED)      # face kept in the render
    assert not _has_color(without, _RED)     # old behaviour centre-cropped it out


def _img(path, size=(400, 200)) -> str:
    Image.new("RGB", size, (200, 180, 160)).save(path)
    return str(path)


def test_layout_selector_stamps_and_shifts_for_faces(tmp_path):
    p = _img(tmp_path / "a.jpg", size=(200, 400))  # tall photo
    project = AlbumProject.new(str(tmp_path))
    project.sections = [SectionRecord("Portraits", "portraits", [p])]
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=100)

    face = (0.4, 0.02, 0.2, 0.1)
    spreads = LayoutSelector().select(project, spec, faces_by_path={p: (face,)})
    pl = spreads[0].placements[0]

    # Face boxes are persisted on the placement for the renderer to reuse.
    assert pl["face_boxes"] == [[0.4, 0.02, 0.2, 0.1]]
    # And the stored crop actually contains the (padded) face rather than centering.
    cx, cy, cw, ch = pl["crop"]
    assert cy <= 0.02 + 1e-6  # window pulled up to keep the near-top face


def test_layout_selector_no_faces_is_backward_compatible(tmp_path):
    p = _img(tmp_path / "b.jpg", size=(200, 400))
    project = AlbumProject.new(str(tmp_path))
    project.sections = [SectionRecord("Portraits", "portraits", [p])]
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=100)

    spreads = LayoutSelector().select(project, spec)  # no faces_by_path
    pl = spreads[0].placements[0]
    assert pl["face_boxes"] == []
