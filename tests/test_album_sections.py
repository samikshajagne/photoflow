"""
Unit tests for core.album.sections.

Pure selection logic over synthetic identity / quality / timeline data.
"""

from datetime import datetime

import pytest

from core.album.sections import (
    AlbumProject,
    KIND_COUPLE,
    KIND_COVER,
    KIND_EVENT,
    KIND_GROUP,
    KIND_SOLO,
    Section,
    SectionContext,
    SectionError,
    SectionSpec,
    build_section,
)
from core.timeline import EventSegment


def _ctx(eligible=None, events=()):
    persons = {
        "/p/1.jpg": {"bride"},
        "/p/2.jpg": {"bride"},
        "/p/3.jpg": {"bride", "groom"},
        "/p/4.jpg": {"groom"},
    }
    quality = {"/p/1.jpg": 80.0, "/p/2.jpg": 90.0, "/p/3.jpg": 95.0, "/p/4.jpg": 70.0}
    return SectionContext(
        persons_present=persons,
        quality_by_path=quality,
        events=tuple(events),
        eligible=frozenset(eligible) if eligible is not None else None,
    )


def test_couple_section():
    spec = SectionSpec(name="Couple", kind=KIND_COUPLE)  # default bride+groom
    assert build_section(spec, _ctx()) == ("/p/3.jpg",)


def test_solo_section_ranks_by_quality_and_excludes_couple():
    spec = SectionSpec(name="Bride", kind=KIND_SOLO, label="bride")
    # bride-only photos are p1(80) and p2(90); ranked best-first.
    assert build_section(spec, _ctx()) == ("/p/2.jpg", "/p/1.jpg")


def test_group_section_includes_all_with_label():
    spec = SectionSpec(name="With bride", kind=KIND_GROUP, label="bride")
    assert build_section(spec, _ctx()) == ("/p/3.jpg", "/p/2.jpg", "/p/1.jpg")


def test_cover_picks_single_top_couple_shot():
    spec = SectionSpec(name="Cover", kind=KIND_COVER)
    assert build_section(spec, _ctx()) == ("/p/3.jpg",)


def test_eligible_pool_restricts_quality_sections():
    spec = SectionSpec(name="Bride", kind=KIND_SOLO, label="bride")
    # Exclude p2 from the BestShots pool -> only p1 remains for the bride sheet.
    assert build_section(spec, _ctx(eligible={"/p/1.jpg", "/p/3.jpg", "/p/4.jpg"})) == (
        "/p/1.jpg",
    )


def test_event_section_is_chronological_and_ignores_eligible():
    events = [
        EventSegment(
            index=0,
            photos=("/c/a.jpg", "/c/b.jpg", "/c/c.jpg"),
            start=datetime(2026, 6, 1, 9, 0),
            end=datetime(2026, 6, 1, 9, 30),
        )
    ]
    spec = SectionSpec(name="Ceremony", kind=KIND_EVENT, event_index=0)
    # None of these are in `eligible`, yet the whole ceremony is included in order.
    out = build_section(spec, _ctx(eligible=set(), events=events))
    assert out == ("/c/a.jpg", "/c/b.jpg", "/c/c.jpg")


def test_limit_caps_section():
    spec = SectionSpec(name="With bride", kind=KIND_GROUP, label="bride", limit=2)
    assert build_section(spec, _ctx()) == ("/p/3.jpg", "/p/2.jpg")


# --------------------------------------------------------------------------- #
# Spec validation
# --------------------------------------------------------------------------- #
def test_spec_validation():
    with pytest.raises(SectionError):
        SectionSpec(name="x", kind="bogus")
    with pytest.raises(SectionError):
        SectionSpec(name="x", kind=KIND_SOLO)  # missing label
    with pytest.raises(SectionError):
        SectionSpec(name="x", kind=KIND_EVENT)  # missing event_index


# --------------------------------------------------------------------------- #
# Sticky overrides
# --------------------------------------------------------------------------- #
def test_section_remove_is_sticky():
    section = Section.build(SectionSpec(name="Bride", kind=KIND_SOLO, label="bride"), _ctx())
    assert section.resolved() == ("/p/2.jpg", "/p/1.jpg")
    section = section.remove("/p/1.jpg")
    assert section.resolved() == ("/p/2.jpg",)


def test_section_add_and_reorder():
    section = Section.build(SectionSpec(name="Bride", kind=KIND_SOLO, label="bride"), _ctx())
    section = section.add("/extra/x.jpg")
    assert "/extra/x.jpg" in section.resolved()
    section = section.reorder(["/extra/x.jpg", "/p/1.jpg", "/p/2.jpg"])
    assert section.resolved()[0] == "/extra/x.jpg"


def test_album_project_builds_and_lists_in_order():
    specs = [
        SectionSpec(name="Cover", kind=KIND_COVER),
        SectionSpec(name="Bride", kind=KIND_SOLO, label="bride"),
    ]
    project = AlbumProject.build(specs, _ctx())
    resolved = project.resolved()
    assert [name for name, _ in resolved] == ["Cover", "Bride"]
    assert resolved[0][1] == ("/p/3.jpg",)
    assert project.all_photos()[0] == "/p/3.jpg"
