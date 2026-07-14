"""
Unit tests for core.album.story (Phase 1 StoryBuilder).
"""

from core.album.project import AlbumProject, PhotoRecord
from core.album.story import StoryBuilder
from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)


def _photo(path, category, q, faces, fcount, when):
    return PhotoRecord(
        source_path=path,
        category=category,
        quality_score=q,
        faces_detected=faces,
        face_count=fcount,
        capture_time=when,
        is_best_shot=(category == FOLDER_BEST_SHOTS),
    )


def _project():
    project = AlbumProject.new("/shoot")
    project.add_photo(_photo("/p1.jpg", FOLDER_REVIEW, 80.0, True, 1, "2026-06-01T09:00:00"))
    project.add_photo(_photo("/p2.jpg", FOLDER_BEST_SHOTS, 92.0, True, 2, "2026-06-01T09:05:00"))
    project.add_photo(_photo("/p3.jpg", FOLDER_BEST_SHOTS, 88.0, True, 4, "2026-06-01T10:00:00"))
    project.add_photo(_photo("/p4.jpg", FOLDER_REVIEW, 70.0, False, 0, "2026-06-01T11:00:00"))
    project.add_photo(_photo("/p5.jpg", FOLDER_BEST_SHOTS, 95.0, True, 1, "2026-06-01T12:00:00"))
    # These must never reach the album.
    project.add_photo(_photo("/dup.jpg", FOLDER_DUPLICATES, 99.0, True, 2, "2026-06-01T09:01:00"))
    project.add_photo(_photo("/blur.jpg", FOLDER_BLURRY, 10.0, False, 0, "2026-06-01T09:02:00"))
    return project


def _by_name(sections):
    return {s.name: s for s in sections}


def test_sections_order_and_membership():
    sections = StoryBuilder().build(_project())
    names = [s.name for s in sections]
    assert names == ["Cover", "Highlights", "Ceremony", "Family", "Portraits", "Closing"]

    s = _by_name(sections)
    # Cover prefers a high-quality shot with people: p5 (q95, faces).
    assert s["Cover"].photos == ["/p5.jpg"]
    # Highlights = BestShots by quality, excluding the cover: p2(92), p3(88).
    assert s["Highlights"].photos == ["/p2.jpg", "/p3.jpg"]
    # Ceremony = all candidates chronologically (dup/blur excluded).
    assert s["Ceremony"].photos == ["/p1.jpg", "/p2.jpg", "/p3.jpg", "/p4.jpg", "/p5.jpg"]
    # Family = face_count >= 3.
    assert s["Family"].photos == ["/p3.jpg"]
    # Portraits = single subject, best-first.
    assert s["Portraits"].photos == ["/p5.jpg", "/p1.jpg"]


def test_duplicates_and_blurry_never_appear():
    sections = StoryBuilder().build(_project())
    all_photos = {p for s in sections for p in s.photos}
    assert "/dup.jpg" not in all_photos
    assert "/blur.jpg" not in all_photos


def test_empty_candidate_pool_yields_no_sections():
    project = AlbumProject.new("/shoot")
    project.add_photo(_photo("/dup.jpg", FOLDER_DUPLICATES, 99.0, True, 2, "t"))
    assert StoryBuilder().build(project) == []


def test_degrades_without_faces():
    # No faces -> no Portraits/Family, but Cover/Highlights/Ceremony still build.
    project = AlbumProject.new("/shoot")
    project.add_photo(_photo("/a.jpg", FOLDER_BEST_SHOTS, 90.0, False, 0, "2026-06-01T09:00:00"))
    project.add_photo(_photo("/b.jpg", FOLDER_REVIEW, 70.0, False, 0, "2026-06-01T09:30:00"))
    names = [s.name for s in StoryBuilder().build(project)]
    assert "Cover" in names
    assert "Ceremony" in names
    assert "Family" not in names
    assert "Portraits" not in names


def test_degrades_without_timestamps():
    # No capture times -> chronological falls back to path order (still builds).
    project = AlbumProject.new("/shoot")
    project.add_photo(_photo("/b.jpg", FOLDER_REVIEW, 70.0, False, 0, None))
    project.add_photo(_photo("/a.jpg", FOLDER_REVIEW, 75.0, False, 0, None))
    s = _by_name(StoryBuilder().build(project))
    assert s["Ceremony"].photos == ["/a.jpg", "/b.jpg"]
