"""Component 6 tests: vision brain analysis stage + caching."""

from __future__ import annotations

from core.vision_brain import PhotoBrain, VisionBrain, SOURCE_GOOGLE
from core.brain_stage import (
    CACHE_NAMESPACE,
    analyze_and_cache,
    load_cached_brains,
    resolve_api_key,
)


class _FakeCache:
    """Minimal stand-in for AnalysisCache: namespace -> {path: data}."""

    def __init__(self):
        self._data = {}
        self.put_calls = 0

    def valid(self, ns, path):
        return path in self._data.get(ns, {})

    def get(self, ns, path):
        return self._data.get(ns, {}).get(path)

    def put(self, ns, path, data):
        self.put_calls += 1
        self._data.setdefault(ns, {})[path] = data


class _CountingBrain(VisionBrain):
    """A VisionBrain whose analyze() is counted and returns a canned PhotoBrain."""

    def __init__(self):
        super().__init__(api_key="fake-key")
        self.calls = 0

    def analyze(self, path):
        self.calls += 1
        return PhotoBrain(path=path, face_count=1, scene_labels=["haldi"], source=SOURCE_GOOGLE)


def test_resolve_api_key_prefers_explicit(monkeypatch):
    monkeypatch.setenv("GOOGLE_VISION_API_KEY", "env-key")
    assert resolve_api_key("explicit") == "explicit"
    assert resolve_api_key(None) == "env-key"
    monkeypatch.delenv("GOOGLE_VISION_API_KEY", raising=False)
    assert resolve_api_key(None) == ""


def test_analyzes_and_caches_each_photo():
    cache = _FakeCache()
    brain = _CountingBrain()
    paths = ["a.jpg", "b.jpg", "c.jpg"]
    out = analyze_and_cache(paths, cache, brain=brain)
    assert set(out) == set(paths)
    assert brain.calls == 3
    assert cache.put_calls == 3
    # Cached as serialisable dicts under the right namespace.
    assert cache.get(CACHE_NAMESPACE, "a.jpg")["scene_labels"] == ["haldi"]


def test_reuses_cache_on_second_run():
    cache = _FakeCache()
    brain = _CountingBrain()
    paths = ["a.jpg", "b.jpg"]
    analyze_and_cache(paths, cache, brain=brain)
    assert brain.calls == 2

    # Second run: everything cached -> no new analyze calls.
    brain2 = _CountingBrain()
    out = analyze_and_cache(paths, cache, brain=brain2)
    assert brain2.calls == 0
    assert all(isinstance(pb, PhotoBrain) for pb in out.values())
    assert out["a.jpg"].scene_labels == ["haldi"]


def test_partial_cache_only_analyzes_new():
    cache = _FakeCache()
    analyze_and_cache(["a.jpg"], cache, brain=_CountingBrain())
    brain = _CountingBrain()
    analyze_and_cache(["a.jpg", "b.jpg"], cache, brain=brain)  # a cached, b new
    assert brain.calls == 1  # only b.jpg


def test_load_cached_brains_roundtrip():
    cache = _FakeCache()
    analyze_and_cache(["a.jpg", "b.jpg"], cache, brain=_CountingBrain())
    loaded = load_cached_brains(cache, ["a.jpg", "b.jpg", "missing.jpg"])
    assert set(loaded) == {"a.jpg", "b.jpg"}
    assert loaded["a.jpg"].face_count == 1


def test_progress_callback_fires():
    cache = _FakeCache()
    seen = []
    analyze_and_cache(
        ["a.jpg", "b.jpg"], cache, brain=_CountingBrain(),
        progress_cb=lambda done, total: seen.append((done, total)),
    )
    assert (1, 2) in seen and (2, 2) in seen
