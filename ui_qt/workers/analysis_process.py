"""
Process-side analysis runner.

This module runs **in a separate process** (see
``ui_qt.workers.analysis_worker``). It deliberately imports **no PyQt6** so
the child process stays lightweight and works even where the GUI's native Qt
libraries aren't loadable.

It reuses the existing pipeline unchanged: it builds
``core.pipeline.PhotoFlowPipeline`` from config and calls ``run()``. While
the pipeline executes, the backend's own ``photoflow`` log records are
forwarded to the parent over a queue (so the UI can show live status), and
the final ``PipelineResult`` (or an error) is sent back the same way.

Message protocol (tuples placed on the queue):
    ("log",    str)              # a status line
    ("result", PipelineResult)   # success
    ("error",  str)              # failure (message + traceback)
    ("done",   None)             # always last; signals end of stream
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any

# Load .env so OPENAI_API_KEY reaches the subprocess (it runs in a fresh
# Python interpreter that doesn't inherit the parent process's env-var load).
try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on shell-level env vars.

# The logger hierarchy the backend writes to.
_PHOTOFLOW_LOGGER = "photoflow"


class _QueueLogHandler(logging.Handler):
    """A logging handler that forwards formatted records onto a queue."""

    def __init__(self, queue: Any) -> None:
        super().__init__()
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put(("log", record.getMessage()))
        except Exception:  # pragma: no cover - never let logging crash analysis
            pass


def run_pipeline_to_queue(input_folder: str, output_folder: str, queue: Any) -> None:
    """
    Run the full pipeline and stream log lines + the result onto ``queue``.

    Intended as a ``multiprocessing.Process`` target. Never raises: failures
    are reported as an ``("error", ...)`` message. Always finishes by putting
    a ``("done", None)`` sentinel.
    """
    handler = _QueueLogHandler(queue)
    handler.setLevel(logging.INFO)
    root = logging.getLogger(_PHOTOFLOW_LOGGER)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)

    try:
        # Imported lazily so importing this module stays cheap.
        from core.pipeline import PhotoFlowPipeline
        from utils.config import load_config
        from persistence.analysis_cache import AnalysisCache
        from core.album.analysis_records import album_cache_path, records_from_result

        # A shared analysis cache keyed to the source folder. Populating it here
        # means a later "Generate Album" reuses this pass (quality records +
        # face detections) instead of re-running the whole pipeline.
        cache = AnalysisCache(album_cache_path(input_folder))

        pipeline = PhotoFlowPipeline.from_config(load_config())
        result = pipeline.run(
            input_folder=input_folder,
            destination_root=output_folder,
            dry_run=False,
            cache=cache,
        )

        # Persist the classified inventory so album generation can skip
        # re-analysis. Non-fatal: a cache-write failure must not fail analysis.
        try:
            for rec in records_from_result(result):
                cache.put("quality", rec.source_path, rec.to_dict())
            cache.save()
        except Exception as cache_exc:  # noqa: BLE001
            logging.getLogger(_PHOTOFLOW_LOGGER).warning(
                "Analysis cache write failed (album will re-analyze): %s", cache_exc
            )

        queue.put(("result", result))
    except Exception as exc:  # noqa: BLE001 - report everything to the parent
        queue.put(("error", f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"))
    finally:
        root.removeHandler(handler)
        queue.put(("done", None))
