"""
Shared analysis → PhotoRecord conversion.

Both the album orchestrator (``core.album.orchestrator``) and the desktop
UI's analysis pass (``ui_qt.workers.analysis_process``) need to turn a
:class:`~core.pipeline.PipelineResult` into the canonical
:class:`~core.album.project.PhotoRecord` inventory, using the *same*
classification (BestShots > Duplicates > Blurry > Review).

Factoring it here means the UI's "Analyze Folder" pass can persist those
records to the shared :class:`~persistence.analysis_cache.AnalysisCache`, so a
subsequent "Generate Album" reuses them instead of re-running the whole
pipeline. There is exactly one definition of the classification, so the two
entry points can never drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Union

from core.album.project import PhotoRecord, normalize_path, quality_tier
from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)
from core.timeline import read_capture_time

if TYPE_CHECKING:
    from core.pipeline import PipelineResult

PathLike = Union[str, Path]

# Album output layout — the folder and cache filename the orchestrator writes
# and reads. Defined here (a light module with no heavy imports) so the UI's
# analysis child process can locate the same cache without importing the whole
# orchestrator (and its layout/cluster dependencies).
DEFAULT_ALBUM_DIR = "PhotoFlow_Album"
CACHE_FILENAME = ".photoflow_cache.json"


def album_cache_path(source_folder: PathLike) -> Path:
    """Path to the shared analysis cache for a given source photo folder."""
    return Path(source_folder) / DEFAULT_ALBUM_DIR / CACHE_FILENAME


def classify(path: str, usable: bool, best: set[str], dups: set[str]) -> str:
    """Organizer precedence: BestShots > Duplicates > Blurry > Review."""
    norm = normalize_path(path)
    if norm in best:
        return FOLDER_BEST_SHOTS
    if norm in dups:
        return FOLDER_DUPLICATES
    if not usable:
        return FOLDER_BLURRY
    return FOLDER_REVIEW


def records_from_result(result: "PipelineResult") -> list[PhotoRecord]:
    """
    Build the :class:`PhotoRecord` inventory from a pipeline result.

    Works off ``quality_results``, ``best_shot_candidates`` and
    ``duplicate_paths`` — all computed identically whether the pipeline ran in
    ``dry_run`` mode (album) or organizing mode (UI analyze) — so the records
    are the same regardless of which entry point produced ``result``.
    """
    best = {normalize_path(p) for p in result.best_shot_candidates}
    dups = set(result.duplicate_paths)  # already normalized by the pipeline

    records: list[PhotoRecord] = []
    for q in result.quality_results:
        usable = bool(getattr(q, "usable", True))
        category = classify(q.image_path, usable, best, dups)
        records.append(
            PhotoRecord(
                source_path=q.image_path,
                category=category,
                capture_time=read_capture_time(q.image_path).isoformat(),
                quality_score=q.quality_score,
                tier=quality_tier(q.quality_score),
                blur_score=q.blur_score,
                sharpness=getattr(q, "sharpness", None),
                brightness=q.brightness,
                contrast=q.contrast,
                faces_detected=q.faces_detected,
                face_count=q.face_count,
                usable=usable,
                is_best_shot=category == FOLDER_BEST_SHOTS,
                is_duplicate=category == FOLDER_DUPLICATES,
            )
        )
    return records
