"""
Unit tests for persistence.analysis_cache.
"""

import os
import time

from persistence.analysis_cache import AnalysisCache


def _touch(path, content=b"x"):
    path.write_bytes(content)
    return path


def test_put_get_round_trip(tmp_path):
    img = _touch(tmp_path / "a.jpg")
    cache = AnalysisCache(tmp_path / "cache.json")
    cache.put("quality", img, {"score": 91.0})
    assert cache.get("quality", img) == {"score": 91.0}
    assert cache.valid("quality", img)


def test_miss_for_unknown_file(tmp_path):
    cache = AnalysisCache(tmp_path / "cache.json")
    assert cache.get("quality", tmp_path / "nope.jpg") is None
    assert cache.valid("quality", tmp_path / "nope.jpg") is False


def test_invalidates_when_file_changes(tmp_path):
    img = _touch(tmp_path / "a.jpg", b"one")
    cache = AnalysisCache(tmp_path / "cache.json")
    cache.put("quality", img, {"score": 1})
    assert cache.valid("quality", img)

    # Change size + mtime -> stale.
    time.sleep(0.01)
    img.write_bytes(b"different-content")
    os.utime(img, None)
    assert cache.valid("quality", img) is False
    assert cache.get("quality", img) is None


def test_namespaces_are_independent(tmp_path):
    img = _touch(tmp_path / "a.jpg")
    cache = AnalysisCache(tmp_path / "cache.json")
    cache.put("quality", img, {"q": 1})
    assert cache.get("edit", img) is None  # different namespace
    cache.put("edit", img, {"exposure": 1.2})
    assert cache.get("edit", img) == {"exposure": 1.2}


def test_persists_across_instances(tmp_path):
    img = _touch(tmp_path / "a.jpg")
    path = tmp_path / "cache.json"
    c1 = AnalysisCache(path)
    c1.put("quality", img, {"score": 88})
    c1.save()

    c2 = AnalysisCache(path)
    assert c2.get("quality", img) == {"score": 88}


def test_all_valid(tmp_path):
    a = _touch(tmp_path / "a.jpg")
    b = _touch(tmp_path / "b.jpg")
    cache = AnalysisCache(tmp_path / "cache.json")
    cache.put("quality", a, {"score": 1})
    assert cache.all_valid("quality", [a, b]) is False
    cache.put("quality", b, {"score": 2})
    assert cache.all_valid("quality", [a, b]) is True


def test_corrupt_cache_is_ignored(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{ not valid json", encoding="utf-8")
    cache = AnalysisCache(path)  # must not raise
    img = _touch(tmp_path / "a.jpg")
    assert cache.get("quality", img) is None
