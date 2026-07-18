"""WS 3.4.2 tests: album config flags round-trip through orchestrator -> manifest."""
from __future__ import annotations
import types

from core.album.raster import _album_flags


def _project(smart=True, cutouts=False):
    meta = types.SimpleNamespace(album_spec={"smart_slot_ordering": smart, "use_cutouts": cutouts})
    return types.SimpleNamespace(meta=meta)


def test_flags_default_smart_true_cutouts_false():
    smart, cutouts = _album_flags(_project())
    assert (smart, cutouts) == (True, False)


def test_flags_disable_smart_ordering():
    smart, _ = _album_flags(_project(smart=False))
    assert smart is False


def test_flags_enable_cutouts():
    _, cutouts = _album_flags(_project(cutouts=True))
    assert cutouts is True


def test_flags_both_on():
    smart, cutouts = _album_flags(_project(smart=True, cutouts=True))
    assert smart is True and cutouts is True


def test_flags_missing_album_spec_key_uses_defaults():
    """If the key is absent from album_spec, fall back to the coded defaults."""
    meta = types.SimpleNamespace(album_spec={})
    proj = types.SimpleNamespace(meta=meta)
    smart, cutouts = _album_flags(proj)
    assert smart is True    # default on
    assert cutouts is False  # default off


def test_flags_none_meta_graceful():
    proj = types.SimpleNamespace(meta=None)
    smart, cutouts = _album_flags(proj)
    assert smart is True and cutouts is False


def test_orchestrator_writes_flags_into_album_meta(tmp_path, monkeypatch):
    """
    AlbumOrchestrator writes smart_slot_ordering + use_cutouts into the
    album_spec dict so they round-trip through the manifest.
    """
    import core.album.orchestrator as orch_mod
    from core.album.orchestrator import AlbumOrchestrator
    from core.album.project import PhotoRecord

    # Create a tiny source folder with one stub photo.
    src = tmp_path / "photos"
    src.mkdir()
    img_path = str(src / "img.jpg")
    (src / "img.jpg").write_bytes(b"")

    class _FakeScanner:
        def scan(self, folder):
            return [src / "img.jpg"]

    def _fake_records(result):
        return [PhotoRecord(source_path=img_path, category="best_shots", is_best_shot=True)]

    monkeypatch.setattr(orch_mod, "records_from_result", _fake_records)

    class _FakePipeline:
        def run(self, *a, **kw):
            return {}

    orch = AlbumOrchestrator(
        scanner=_FakeScanner(),
        pipeline=_FakePipeline(),
        enable_identity=False,
        smart_slot_ordering=False,
        use_cutouts=True,
    )

    project, out_dir, cache, _ = orch._prepare(src, tmp_path / "out", overrides={}, reanalyze=False)

    raw = dict(getattr(project.meta, "album_spec", {}) or {})
    assert raw.get("smart_slot_ordering") is False, f"Expected False, got {raw.get('smart_slot_ordering')}"
    assert raw.get("use_cutouts") is True, f"Expected True, got {raw.get('use_cutouts')}"
