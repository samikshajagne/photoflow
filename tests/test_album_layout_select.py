"""
Unit tests for core.album.layout_select (section -> spread policy).
"""

import cv2
import numpy as np

from core.album.layout import AlbumSpec
from core.album.layout_select import LayoutSelector
from core.album.project import AlbumProject, SectionRecord


def _img(path, w=200, h=100):
    cv2.imwrite(str(path), np.full((h, w, 3), 127, np.uint8))
    return str(path)


def test_section_policy_maps_to_spreads(tmp_path):
    paths = [_img(tmp_path / f"{i}.jpg") for i in range(4)]
    project = AlbumProject.new(str(tmp_path))
    project.sections = [
        SectionRecord("Cover", "cover", [paths[0]]),
        SectionRecord("Portraits", "portraits", paths[:2]),
        SectionRecord("Family", "family", paths[:4]),
        SectionRecord("Ceremony", "ceremony", paths[:3]),
    ]
    spec = AlbumSpec(page_width_in=12, page_height_in=12, dpi=300)

    spreads = LayoutSelector().select(project, spec)
    by_section: dict[str, list] = {}
    for s in spreads:
        by_section.setdefault(s.section, []).append(s)

    # cover -> 1 photo per spread (full)
    assert len(by_section["Cover"]) == 1
    assert len(by_section["Cover"][0].placements) == 1
    # portraits -> 2 per spread (side-by-side)
    assert len(by_section["Portraits"]) == 1
    assert len(by_section["Portraits"][0].placements) == 2
    # family -> 4 per spread (grid)
    assert len(by_section["Family"]) == 1
    assert len(by_section["Family"][0].placements) == 4
    # ceremony -> 3 per spread (collage)
    assert len(by_section["Ceremony"]) == 1
    assert len(by_section["Ceremony"][0].placements) == 3

    # Global spread indices are unique and sequential.
    assert [s.index for s in spreads] == list(range(len(spreads)))
    # Spread pixel size comes from the album spec.
    assert spreads[0].width_px == spec.spread_width_px


def test_placement_has_path_frame_and_crop(tmp_path):
    p = _img(tmp_path / "a.jpg")
    project = AlbumProject.new(str(tmp_path))
    project.sections = [SectionRecord("Cover", "cover", [p])]
    spreads = LayoutSelector().select(project, AlbumSpec(10, 10, 300))
    placement = spreads[0].placements[0]
    assert placement["path"] == p
    assert len(placement["frame_px"]) == 4
    assert len(placement["crop"]) == 4


def test_unreadable_photo_falls_back_to_square(tmp_path):
    project = AlbumProject.new(str(tmp_path))
    project.sections = [SectionRecord("X", "ceremony", ["/no/such/file.jpg"])]
    spreads = LayoutSelector().select(project, AlbumSpec(12, 12, 300))
    assert len(spreads) == 1
    assert len(spreads[0].placements) == 1


# --------------------------------------------------------------------------- #
# Density control
# --------------------------------------------------------------------------- #
def _ceremony_project(tmp_path, n=12):
    # Fake paths are fine: unreadable -> square aspect fallback.
    paths = [str(tmp_path / f"c{i}.jpg") for i in range(n)]
    project = AlbumProject.new(str(tmp_path))
    project.sections = [SectionRecord("Ceremony", "ceremony", paths)]
    return project


def test_density_changes_spread_count(tmp_path):
    project = _ceremony_project(tmp_path, n=12)
    spec = AlbumSpec(12, 12, 300)

    spacious = LayoutSelector(density="spacious").select(project, spec)
    balanced = LayoutSelector(density="balanced").select(project, spec)
    dense = LayoutSelector(density="dense").select(project, spec)

    # Fewer photos per spread (spacious) => more spreads; dense => fewer.
    assert len(spacious) > len(balanced) > len(dense)


def test_dense_packs_more_per_spread(tmp_path):
    project = _ceremony_project(tmp_path, n=12)
    spec = AlbumSpec(12, 12, 300)
    dense = LayoutSelector(density="dense").select(project, spec)
    # Dense ceremony lands above the balanced 3-per-spread.
    assert max(len(s.placements) for s in dense) > 3


def test_hero_stays_single_regardless_of_density(tmp_path):
    project = AlbumProject.new(str(tmp_path))
    project.sections = [SectionRecord("Cover", "cover", [str(tmp_path / "c.jpg")])]
    for density in ("spacious", "balanced", "dense"):
        spreads = LayoutSelector(density=density).select(project, AlbumSpec(12, 12, 300))
        assert len(spreads) == 1
        assert len(spreads[0].placements) == 1


def test_unknown_density_falls_back_to_balanced(tmp_path):
    sel = LayoutSelector(density="nonsense")
    assert sel.density == "balanced"
