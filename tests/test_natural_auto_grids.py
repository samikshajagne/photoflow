import pytest
from core.album.template import auto_grid_template, SHAPE_RECT, Background

def test_natural_auto_grid_1_photo():
    tmpl = auto_grid_template(1, theme="natural")
    assert tmpl.name == "natural-auto-1"
    assert len(tmpl.slots) == 1
    assert tmpl.slots[0].rect == (0.0, 0.0, 1.0, 1.0)
    assert tmpl.slots[0].z_index == 0

def test_natural_auto_grid_2_photos():
    tmpl = auto_grid_template(2, theme="natural")
    assert tmpl.name == "natural-auto-2"
    assert len(tmpl.slots) == 2
    assert tmpl.slots[0].z_index == 0
    assert tmpl.slots[1].z_index == 1
    assert tmpl.slots[1].rect == (0.68, 0.62, 0.25, 0.30)

def test_natural_auto_grid_3_photos_left_hero():
    tmpl = auto_grid_template(3, theme="natural", variant=0)
    assert "hero-grid" in tmpl.name
    assert len(tmpl.slots) == 3
    # First slot is the hero
    assert tmpl.slots[0].rect == (0.0, 0.0, 0.49, 1.0)
    assert tmpl.slots[0].use_cutout is True
    # Other slots are on the right side
    assert tmpl.slots[1].rect[0] >= 0.51
    assert tmpl.slots[2].rect[0] >= 0.51

def test_natural_auto_grid_6_photos_pano_strip():
    tmpl = auto_grid_template(6, theme="natural", variant=1)
    assert "pano-strip" in tmpl.name
    assert len(tmpl.slots) == 6
    # Slot 0 is full bleed background
    assert tmpl.slots[0].rect == (0.0, 0.0, 1.0, 1.0)
    assert tmpl.slots[0].z_index == 0
    # Other slots are overlays along the bottom (y=0.65)
    for s in tmpl.slots[1:]:
        assert s.z_index == 1
        assert s.rect[1] == 0.65
        assert s.rect[3] == 0.28
