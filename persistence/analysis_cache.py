"""
Persistent analysis cache for PhotoFlow (Phase 1).

Re-analyzing a 900-3000 photo shoot on every "generate album" is unusable, so
expensive per-photo results are cached and reused while the source file is
unchanged. Entries are namespaced (e.g. ``"quality"``, ``"edit"``, later
``"embedding"``) and validated by a cheap **(size, mtime)** signature -- if a
file's size or modification time changes, its cached entries are treated as
stale and recomputed.

The cache is a single JSON file; it stores only JSON-serializable payloads
(the caller decides what to cache). It has no Qt and no heavy dependencies.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Union

from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]


class AnalysisCache:
    """
    A namespaced, file-validated JSON cache.

    Args:
        cache_path: Path to the JSON cache file. Created on first :meth:`save`.

    Typical use::

        cache = AnalysisCache(folder / ".photoflow_cache.json")
        cached = cache.get("quality", photo)
        if cached is None:
            cached = expensive_analysis(photo)
            cache.put("quality", photo, cached)
        ...
        cache.save()
    """

    def __init__(self, cache_path: PathLike) -> None:
        self.path = Path(cache_path)
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ----------------------------------------------------------------- #
    # Lookup / store
    # ----------------------------------------------------------------- #
    def valid(self, namespace: str, photo_path: PathLike) -> bool:
        """True if a fresh cached entry exists for this file (size+mtime match)."""
        entry = self._data.get(namespace, {}).get(self._key(photo_path))
        if entry is None:
            return False
        sig = self._signature(photo_path)
        return sig is not None and entry.get("sig") == sig

    def get(self, namespace: str, photo_path: PathLike) -> Optional[Any]:
        """Return the cached payload if fresh, else ``None``."""
        if not self.valid(namespace, photo_path):
            return None
        return self._data[namespace][self._key(photo_path)]["data"]

    def put(self, namespace: str, photo_path: PathLike, data: Any) -> None:
        """Store ``data`` for this file with its current signature."""
        sig = self._signature(photo_path)
        if sig is None:
            # File vanished between analysis and caching; skip rather than store
            # an entry that can never validate.
            return
        self._data.setdefault(namespace, {})[self._key(photo_path)] = {
            "sig": sig,
            "data": data,
        }

    def all_valid(self, namespace: str, photo_paths) -> bool:
        """True only if every given path has a fresh entry in ``namespace``."""
        return all(self.valid(namespace, p) for p in photo_paths)

    # ----------------------------------------------------------------- #
    # Persistence
    # ----------------------------------------------------------------- #
    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"version": 1, "namespaces": self._data}, indent=2),
            encoding="utf-8",
        )
        return self.path

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = dict(payload.get("namespaces", {}))
        except (OSError, ValueError) as exc:
            # A corrupt cache is never fatal: start empty and overwrite on save.
            logger.warning("Ignoring unreadable cache '%s': %s", self.path, exc)
            self._data = {}

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #
    @staticmethod
    def _key(photo_path: PathLike) -> str:
        return str(Path(photo_path).resolve(strict=False))

    @staticmethod
    def _signature(photo_path: PathLike) -> Optional[list]:
        """(size, int mtime) for the file, or ``None`` if it can't be stat'd."""
        try:
            st = os.stat(photo_path)
        except OSError:
            return None
        return [st.st_size, int(st.st_mtime)]
