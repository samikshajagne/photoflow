"""
Tests for core.collage_auto (smart photo selection / per-person grouping) and
core.collage_presets (saved styles).

The auto-build tests deliberately exercise the *degraded* paths too -- MediaPipe
and a face-embedding backend are both optional, and these features must return
something usable rather than raising when they're missing, because they're
conveniences and must never block building a collage.
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest
from PIL import Image

from core.collage_auto import (
    CollageAutoError,
    ScoredPhoto,
    group_photos_by_person,
    score_folder,
    select_best_photos,
    to_collage_photos,
)
from core.collage_presets import (
    CollagePreset,
    PresetError,
    delete_preset,
    load_presets,
    save_presets,
    upsert_preset,
)


# --------------------------------------------------------------------------- #
# Fixtures: a folder of photos with genuinely different sharpness
# --------------------------------------------------------------------------- #
def _sharp(path, size=(400, 300)) -> None:
    """High-frequency detail => high variance-of-Laplacian => 'sharp'."""
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
    cv2.imwrite(str(path), arr)


def _blurry(path, size=(400, 300)) -> None:
    rng = np.random.default_rng(1)
    arr = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
    cv2.imwrite(str(path), cv2.GaussianBlur(arr, (0, 0), sigmaX=9))


def _photo_folder(tmp_path, sharp=3, blurry=2):
    folder = tmp_path / "shoot"
    folder.mkdir()
    for i in range(sharp):
        _sharp(folder / f"sharp_{i}.png")
    for i in range(blurry):
        _blurry(folder / f"blurry_{i}.png")
    return folder


# --------------------------------------------------------------------------- #
# score_folder
# --------------------------------------------------------------------------- #
def test_score_folder_scores_every_readable_image(tmp_path):
    folder = _photo_folder(tmp_path)
    scored = score_folder(folder)
    assert len(scored) == 5
    assert all(isinstance(s, ScoredPhoto) for s in scored)
    assert all(0 <= s.quality <= 100 for s in scored)


def test_score_folder_ranks_sharp_above_blurry(tmp_path):
    folder = _photo_folder(tmp_path)
    scored = score_folder(folder)
    sharp = [s.quality for s in scored if "sharp" in s.path.name]
    blurry = [s.quality for s in scored if "blurry" in s.path.name]
    assert min(sharp) > max(blurry)


def test_score_folder_respects_limit(tmp_path):
    folder = _photo_folder(tmp_path, sharp=5, blurry=5)
    assert len(score_folder(folder, limit=4)) <= 4


def test_score_folder_rejects_missing_folder(tmp_path):
    with pytest.raises(CollageAutoError, match="Not a folder"):
        score_folder(tmp_path / "nope")


def test_score_folder_rejects_empty_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CollageAutoError, match="No supported images"):
        score_folder(empty)


def test_score_folder_skips_unreadable_files(tmp_path):
    folder = _photo_folder(tmp_path, sharp=2, blurry=0)
    (folder / "broken.png").write_text("not an image")
    scored = score_folder(folder)
    assert len(scored) == 2  # the broken file was skipped, not fatal


def test_require_faces_is_ignored_when_detection_finds_nothing(tmp_path):
    """Without MediaPipe (or on face-free photos) this must not wipe out every
    candidate -- it should warn and carry on."""
    folder = _photo_folder(tmp_path, sharp=3, blurry=0)
    scored = score_folder(folder, require_faces=True)
    assert len(scored) == 3


# --------------------------------------------------------------------------- #
# select_best_photos
# --------------------------------------------------------------------------- #
def test_select_best_returns_requested_count(tmp_path):
    folder = _photo_folder(tmp_path, sharp=6, blurry=4)
    assert len(select_best_photos(folder, count=5)) == 5


def test_select_best_prefers_sharp_photos(tmp_path):
    folder = _photo_folder(tmp_path, sharp=4, blurry=6)
    chosen = select_best_photos(folder, count=4, spread=False)
    assert all("sharp" in p.path.name for p in chosen)


def test_select_best_returns_everything_when_asked_for_more_than_exists(tmp_path):
    folder = _photo_folder(tmp_path, sharp=2, blurry=1)
    chosen = select_best_photos(folder, count=50)
    assert len(chosen) == 3
    # Still ranked best-first.
    assert chosen == sorted(chosen, key=lambda s: -s.quality)


def test_select_best_never_returns_duplicates(tmp_path):
    folder = _photo_folder(tmp_path, sharp=6, blurry=6)
    chosen = select_best_photos(folder, count=8)
    assert len({p.path for p in chosen}) == len(chosen)


def test_spread_selection_covers_the_shoot(tmp_path):
    """Spreading exists so a collage isn't six frames from one burst."""
    folder = tmp_path / "timeline"
    folder.mkdir()
    for i in range(12):
        _sharp(folder / f"{i:02d}.png")
    chosen = select_best_photos(folder, count=4, spread=True)
    names = sorted(p.path.name for p in chosen)
    # Picks should not all be crowded into the first few filenames.
    assert int(names[-1].split(".")[0]) - int(names[0].split(".")[0]) >= 5


def test_select_best_rejects_bad_count(tmp_path):
    folder = _photo_folder(tmp_path)
    with pytest.raises(CollageAutoError, match="count must be"):
        select_best_photos(folder, count=0)


# --------------------------------------------------------------------------- #
# group_photos_by_person
# --------------------------------------------------------------------------- #
def test_grouping_falls_back_to_one_group_without_a_backend(tmp_path):
    """No embedding backend available => a single combined group, not a crash."""
    folder = _photo_folder(tmp_path, sharp=3, blurry=0)
    scored = score_folder(folder)
    groups = group_photos_by_person(scored, embed_backend=lambda crops: [])
    assert list(groups) == ["Everyone"]
    assert len(groups["Everyone"]) == 3


def test_grouping_handles_photos_without_faces(tmp_path):
    folder = _photo_folder(tmp_path, sharp=2, blurry=0)
    scored = score_folder(folder)  # synthetic noise => no faces
    groups = group_photos_by_person(scored)
    assert groups  # something usable came back
    assert all(isinstance(v, list) for v in groups.values())


def test_grouping_with_a_stub_backend_separates_two_people(tmp_path, monkeypatch):
    """With face boxes and a deterministic fake embedder, distinct people must
    land in distinct groups."""
    folder = _photo_folder(tmp_path, sharp=4, blurry=0)
    scored = score_folder(folder)
    # Give every photo one face box, alternating "identity" by filename order.
    for index, photo in enumerate(scored):
        photo.face_boxes = ((0.3, 0.3, 0.3, 0.3),)
        photo.face_count = 1
        photo._identity = index % 2  # type: ignore[attr-defined]

    order = {str(p.path): getattr(p, "_identity") for p in scored}

    class StubEmbedder:
        """Returns one of two well-separated unit vectors per photo."""

        def __init__(self, *a, **k):
            pass

        @classmethod
        def from_config(cls, *a, **k):
            return cls()

        def embed(self, path, regions):
            import dataclasses

            @dataclasses.dataclass
            class E:
                face_index: int
                vector: np.ndarray

            base = np.array([1.0, 0.0], np.float32) if order[str(path)] == 0 else np.array(
                [0.0, 1.0], np.float32
            )
            return [E(face_index=0, vector=base)]

    import core.face_embedder as fe

    monkeypatch.setattr(fe, "FaceEmbedder", StubEmbedder)
    groups = group_photos_by_person(scored, min_photos=1, embed_backend=lambda crops: [])
    assert len(groups) == 2
    assert sum(len(v) for v in groups.values()) == 4


def test_min_photos_filters_small_groups(tmp_path):
    folder = _photo_folder(tmp_path, sharp=2, blurry=0)
    scored = score_folder(folder)
    groups = group_photos_by_person(scored, min_photos=99)
    # Everything filtered out => still returns the usable fallback group.
    assert list(groups) == ["Everyone"]


# --------------------------------------------------------------------------- #
# to_collage_photos
# --------------------------------------------------------------------------- #
def test_to_collage_photos_loads_images(tmp_path):
    folder = _photo_folder(tmp_path, sharp=3, blurry=0)
    scored = score_folder(folder)
    photos = to_collage_photos(scored)
    assert len(photos) == 3
    assert all(p.image.mode == "RGB" for p in photos)


def test_to_collage_photos_downscales_when_asked(tmp_path):
    folder = tmp_path / "big"
    folder.mkdir()
    _sharp(folder / "big.png", size=(2000, 1500))
    scored = score_folder(folder)
    photos = to_collage_photos(scored, max_dim=300)
    assert max(photos[0].image.size) <= 300


def test_to_collage_photos_skips_files_that_vanished(tmp_path):
    folder = _photo_folder(tmp_path, sharp=2, blurry=0)
    scored = score_folder(folder)
    scored[0].path.unlink()
    assert len(to_collage_photos(scored)) == 1


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #
def _preset(name="House Style") -> CollagePreset:
    return CollagePreset(
        name=name, layout="mosaic", theme="Gallery Dark",
        size_preset="A4 Landscape @300dpi", spacing=20, border=6, corner=10,
        background_style="gradient", background_color=(10, 20, 30),
        background_color2=(200, 180, 160), shape="heart", title="Priya & Arjun",
        bleed_frac=0.03, trim_marks=True,
    )


def test_missing_preset_file_is_not_an_error(tmp_path):
    assert load_presets(tmp_path / "none.json") == []


def test_preset_round_trip(tmp_path):
    path = tmp_path / "p.json"
    original = _preset()
    save_presets([original], path)
    loaded = load_presets(path)
    assert len(loaded) == 1
    assert loaded[0] == original


def test_colours_survive_the_json_round_trip(tmp_path):
    path = tmp_path / "p.json"
    save_presets([_preset()], path)
    loaded = load_presets(path)[0]
    assert loaded.background_color == (10, 20, 30)
    assert isinstance(loaded.background_color, tuple)


def test_upsert_replaces_a_preset_with_the_same_name(tmp_path):
    path = tmp_path / "p.json"
    upsert_preset(_preset("Style"), path)
    upsert_preset(CollagePreset(name="Style", theme="Polaroid"), path)
    presets = load_presets(path)
    assert len(presets) == 1
    assert presets[0].theme == "Polaroid"


def test_upsert_keeps_presets_sorted(tmp_path):
    path = tmp_path / "p.json"
    upsert_preset(CollagePreset(name="Zebra"), path)
    upsert_preset(CollagePreset(name="apple"), path)
    names = [p.name for p in load_presets(path)]
    assert names == ["apple", "Zebra"]


def test_delete_preset(tmp_path):
    path = tmp_path / "p.json"
    upsert_preset(CollagePreset(name="A"), path)
    upsert_preset(CollagePreset(name="B"), path)
    remaining = delete_preset("A", path)
    assert [p.name for p in remaining] == ["B"]


def test_delete_missing_preset_is_a_noop(tmp_path):
    path = tmp_path / "p.json"
    upsert_preset(CollagePreset(name="A"), path)
    assert [p.name for p in delete_preset("nope", path)] == ["A"]


def test_malformed_entries_are_skipped_not_fatal(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "version": 1,
        "presets": [{"name": "Good", "theme": "Polaroid"}, {"no_name": True}, "junk"],
    }))
    loaded = load_presets(path)
    assert [p.name for p in loaded] == ["Good"]


def test_unknown_keys_are_ignored_for_forward_compatibility(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "presets": [{"name": "Future", "some_new_option": 42, "theme": "Polaroid"}]
    }))
    loaded = load_presets(path)
    assert loaded[0].name == "Future"
    assert loaded[0].theme == "Polaroid"


def test_missing_keys_fall_back_to_defaults(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"presets": [{"name": "Sparse"}]}))
    loaded = load_presets(path)[0]
    assert loaded.theme == CollagePreset(name="x").theme


def test_bad_colour_value_falls_back_instead_of_crashing(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "presets": [{"name": "Odd", "background_color": "not-a-colour"}]
    }))
    loaded = load_presets(path)[0]
    assert loaded.background_color == CollagePreset(name="x").background_color


def test_invalid_json_raises(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{definitely not json")
    with pytest.raises(PresetError, match="Could not read presets"):
        load_presets(path)


def test_unexpected_top_level_structure_raises(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps("just a string"))
    with pytest.raises(PresetError, match="Unexpected preset file structure"):
        load_presets(path)


def test_bare_list_is_tolerated(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps([{"name": "Legacy"}]))
    assert [p.name for p in load_presets(path)] == ["Legacy"]


def test_save_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "deeper" / "p.json"
    save_presets([CollagePreset(name="A")], path)
    assert path.exists()


def test_save_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "p.json"
    save_presets([CollagePreset(name="A")], path)
    assert list(tmp_path.glob("*.tmp")) == []
