"""
Unit tests for core.album.project — the canonical AlbumProject document.
"""

from core.album.project import (
    AlbumProject,
    PhotoRecord,
    SectionRecord,
    quality_tier,
)
from core.organizer import FOLDER_BEST_SHOTS, FOLDER_BLURRY, FOLDER_DUPLICATES, FOLDER_REVIEW


def _rec(path, category=FOLDER_REVIEW, **kw):
    return PhotoRecord(source_path=path, category=category, **kw)


def test_quality_tier_boundaries():
    assert quality_tier(None) is None
    assert quality_tier(90.0) == "Hero"
    assert quality_tier(75.0) == "BestShots"
    assert quality_tier(74.9) == "Review"
    assert quality_tier(59.9) == "Low"


def test_add_photo_updates_count():
    project = AlbumProject.new("/shoot")
    project.add_photo(_rec("/shoot/a.jpg"))
    project.add_photo(_rec("/shoot/b.jpg"))
    assert project.meta.photo_count == 2


def test_candidate_pool_excludes_duplicates_and_blurry():
    project = AlbumProject.new("/shoot")
    project.add_photo(_rec("/shoot/best.jpg", FOLDER_BEST_SHOTS))
    project.add_photo(_rec("/shoot/keep.jpg", FOLDER_REVIEW))
    project.add_photo(_rec("/shoot/dup.jpg", FOLDER_DUPLICATES))
    project.add_photo(_rec("/shoot/blur.jpg", FOLDER_BLURRY))

    pool = {r.source_path for r in project.candidate_pool()}
    assert pool == {"/shoot/best.jpg", "/shoot/keep.jpg"}


def test_to_from_dict_round_trips():
    project = AlbumProject.new("/shoot", album_spec={"page_width_in": 12})
    project.add_photo(
        _rec("/shoot/a.jpg", FOLDER_BEST_SHOTS, quality_score=92.0, tier="Hero",
             face_count=2, faces_detected=True, edit_recipe={"exposure": 1.1})
    )
    project.sections = [SectionRecord("Cover", "cover", ["/shoot/a.jpg"])]
    project.overrides = {"/shoot/a.jpg": FOLDER_BEST_SHOTS}

    restored = AlbumProject.from_dict(project.to_dict())
    assert restored == project


def test_save_and_load(tmp_path):
    project = AlbumProject.new(str(tmp_path))
    project.add_photo(_rec(str(tmp_path / "a.jpg"), FOLDER_REVIEW, quality_score=80.0))

    out = project.save(tmp_path)
    assert out.name == "album_manifest.json"
    assert out.is_file()
    assert project.export.manifest_path == str(out)

    reloaded = AlbumProject.load(tmp_path)
    assert reloaded.meta.source_folder == project.meta.source_folder
    assert len(reloaded.photos) == 1


def test_get_matches_by_normalized_path(tmp_path):
    project = AlbumProject.new(str(tmp_path))
    p = tmp_path / "a.jpg"
    project.add_photo(_rec(str(p), FOLDER_REVIEW))
    # A differently-spelled but equivalent path still resolves.
    assert project.get(str(tmp_path / "." / "a.jpg")) is not None
