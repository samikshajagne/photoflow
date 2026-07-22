"""
Vision Brain analysis stage + cache (Implementation Plan — Component 6).

Runs the :class:`~core.vision_brain.VisionBrain` over a set of photos **once**,
storing each :class:`~core.vision_brain.PhotoBrain` in the analysis cache under
the ``"vision_brain"`` namespace. Re-analysis only calls the API for new/changed
photos (the cache's per-file signature invalidates changed images), so the
OpenAI API cost stays "once per photo, ever".

The API key is read from the ``OPENAI_API_KEY`` environment variable (set it in
your ``.env`` file at the project root); when it's unset the brain uses its
local MediaPipe fallback, so the app still works without a key.

Downstream consumers (event classification, person clustering, landmark-based
cropping) read the cached brains via :func:`load_cached_brains` rather than
re-calling the API.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

from core.vision_brain import PhotoBrain, VisionBrain
from utils.logger import get_logger

logger = get_logger(__name__)

CACHE_NAMESPACE = "vision_brain"
ENV_KEY = "OPENAI_API_KEY"


def resolve_api_key(explicit: Optional[str] = None) -> str:
    """The OpenAI API key: explicit arg wins, else ``OPENAI_API_KEY`` env var."""
    if explicit:
        return explicit.strip()
    return os.environ.get(ENV_KEY, "").strip()


def analyze_and_cache(
    image_paths: Sequence[str],
    cache,
    *,
    api_key: Optional[str] = None,
    brain: Optional[VisionBrain] = None,
    progress_cb=None,
) -> Dict[str, PhotoBrain]:
    """
    Extract a :class:`PhotoBrain` for every path and cache it, reusing any valid
    cached brain so each photo hits the API at most once.

    Args:
        image_paths: Photos to analyze.
        cache: An :class:`~persistence.analysis_cache.AnalysisCache` (or anything
            with ``valid``/``get``/``put``).
        api_key: Overrides the ``OPENAI_API_KEY`` env var when given.
        brain: Inject a pre-built :class:`VisionBrain` (tests / shared instance).
        progress_cb: Optional ``callable(done, total)`` for UI progress.

    Returns:
        ``{path: PhotoBrain}`` for every input path (order not guaranteed).
    """
    key = resolve_api_key(api_key)
    vb = brain or VisionBrain(api_key=key)
    total = len(image_paths)
    if vb.available():
        logger.info("Vision Brain: analyzing %d photo(s) via OpenAI GPT-4o Vision…", total)
    else:
        logger.info(
            "Vision Brain: OPENAI_API_KEY not set; using local MediaPipe fallback "
            "for %d photo(s).",
            total,
        )

    out: Dict[str, PhotoBrain] = {}
    for done, path in enumerate(image_paths, start=1):
        cached = _get_cached(cache, path)
        if cached is not None:
            out[path] = cached
        else:
            pb = vb.analyze(path)
            out[path] = pb
            _put_cached(cache, path, pb)
        if progress_cb is not None and (done == 1 or done % 10 == 0 or done == total):
            progress_cb(done, total)
    return out


def load_cached_brains(cache, image_paths: Sequence[str]) -> Dict[str, PhotoBrain]:
    """Load already-cached :class:`PhotoBrain` objects (missing paths omitted)."""
    out: Dict[str, PhotoBrain] = {}
    for path in image_paths:
        pb = _get_cached(cache, path)
        if pb is not None:
            out[path] = pb
    return out


def _get_cached(cache, path: str) -> Optional[PhotoBrain]:
    try:
        if hasattr(cache, "valid") and not cache.valid(CACHE_NAMESPACE, path):
            return None
        data = cache.get(CACHE_NAMESPACE, path)
    except Exception:  # noqa: BLE001 - a cache miss must never break analysis
        return None
    if not data:
        return None
    try:
        return PhotoBrain.from_dict(data)
    except Exception:  # noqa: BLE001 - corrupt entry -> recompute
        return None


def _put_cached(cache, path: str, brain: PhotoBrain) -> None:
    try:
        cache.put(CACHE_NAMESPACE, path, brain.to_dict())
    except Exception as exc:  # noqa: BLE001 - caching is best-effort
        logger.debug("Could not cache vision brain for '%s': %s", path, exc)


__all__ = [
    "CACHE_NAMESPACE",
    "ENV_KEY",
    "resolve_api_key",
    "analyze_and_cache",
    "load_cached_brains",
]
