"""
Tests for Phase 2 analysis wiring (separate-process run + Qt controller).

- The process-side runner is tested in-process (deterministic, no timing).
- The AnalysisController is tested against a real child process, spinning the
  Qt event loop until the result arrives.
- MainWindow's result handling is tested by calling its slot with a synthetic
  PipelineResult (no subprocess).

Skipped wholesale where PyQt6 / its native libs can't load, so the core suite
stays green.
"""

import multiprocessing as mp
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication  # noqa: E402

    from ui_qt.views.main_window import MainWindow  # noqa: E402
    from ui_qt.workers.analysis_worker import AnalysisController  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment without Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.organizer import (  # noqa: E402
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)
from core.pipeline import PipelineResult  # noqa: E402
from ui_qt.workers.analysis_process import run_pipeline_to_queue  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_photos(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    n, sq = 128, 8
    rows = [
        np.repeat([(0 if (r + c) % 2 else 255) for c in range(n // sq)], sq)
        for r in range(n // sq)
    ]
    checker = np.repeat(np.array(rows, np.uint8), sq, 0)[:n, :n]
    cv2.imwrite(str(folder / "a.png"), checker)
    (folder / "b_copy.png").write_bytes((folder / "a.png").read_bytes())
    cv2.imwrite(str(folder / "z_blurry.png"), np.tile(np.linspace(0, 255, n, dtype=np.uint8), (n, 1)))


def _drain(queue):
    msgs = []
    while True:
        kind, payload = queue.get(timeout=30)
        msgs.append((kind, payload))
        if kind == "done":
            break
    return msgs


# --------------------------------------------------------------------------- #
# Process-side runner (in-process, deterministic)
# --------------------------------------------------------------------------- #
def test_process_runner_streams_logs_and_result(tmp_path: Path):
    photos = tmp_path / "photos"
    _make_photos(photos)

    queue: mp.Queue = mp.Queue()
    run_pipeline_to_queue(str(photos), str(tmp_path / "out"), queue)
    msgs = _drain(queue)

    kinds = [k for k, _ in msgs]
    assert "log" in kinds            # backend log lines were forwarded
    assert kinds[-1] == "done"       # sentinel last
    results = [p for k, p in msgs if k == "result"]
    assert len(results) == 1
    assert isinstance(results[0], PipelineResult)
    assert results[0].scanned_count == 3


def test_process_runner_reports_error_for_missing_folder(tmp_path: Path):
    queue: mp.Queue = mp.Queue()
    run_pipeline_to_queue(str(tmp_path / "missing"), str(tmp_path / "out"), queue)
    msgs = _drain(queue)
    assert any(k == "error" for k, _ in msgs)
    assert all(k != "result" for k, _ in msgs)


# --------------------------------------------------------------------------- #
# AnalysisController (real subprocess + event loop)
# --------------------------------------------------------------------------- #
def _spin_until(qapp, predicate, timeout_s: float = 120.0) -> bool:
    deadline = time.time() + timeout_s
    while not predicate() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    return predicate()


def test_controller_runs_in_subprocess_and_emits_result(qapp, tmp_path: Path):
    photos = tmp_path / "photos"
    _make_photos(photos)

    controller = AnalysisController()
    captured = {}
    progress = []
    stopped_emitted = []
    controller.finished.connect(lambda r: captured.setdefault("result", r))
    controller.failed.connect(lambda m: captured.setdefault("error", m))
    controller.progress.connect(progress.append)
    controller.stopped.connect(lambda: stopped_emitted.append(True))

    controller.start(str(photos), str(tmp_path / "out"))
    ok = _spin_until(qapp, lambda: "result" in captured or "error" in captured)

    assert ok, "analysis did not finish in time"
    assert "error" not in captured, captured.get("error")
    result = captured["result"]
    assert isinstance(result, PipelineResult)
    assert result.scanned_count == 3
    assert result.category_counts[FOLDER_DUPLICATES] >= 1
    # Live status was streamed from the backend logs.
    assert len(progress) > 0
    # Let the waiter thread fully wind down.
    _spin_until(qapp, lambda: not controller.is_running(), timeout_s=10)
    assert len(stopped_emitted) == 1


def test_cancel_when_idle_is_safe(qapp):
    controller = AnalysisController()
    controller.cancel()  # must not raise
    assert controller.is_running() is False


# --------------------------------------------------------------------------- #
# MainWindow wiring (no subprocess)
# --------------------------------------------------------------------------- #
def _fake_result(counts: dict) -> PipelineResult:
    return PipelineResult(
        input_folder="/x",
        scanned_count=sum(counts.values()),
        duplicate_group_count=1,
        duplicate_count=counts.get(FOLDER_DUPLICATES, 0),
        blurry_count=counts.get(FOLDER_BLURRY, 0),
        faces_detected_count=0,
        dry_run=False,
        output_root="/x/PhotoFlow_Output",
        category_counts=counts,
        organization=None,
        blur_failures=(),
        face_failures=(),
        quality_results=(),
        best_shot_candidates=(),
    )


def test_main_window_has_cancel_action(qapp):
    win = MainWindow()
    assert win.action_cancel.text() == "Cancel"
    assert win.action_cancel.isEnabled() is False  # idle


def test_apply_result_updates_sidebar(qapp):
    win = MainWindow()
    counts = {FOLDER_BEST_SHOTS: 3, FOLDER_DUPLICATES: 2, FOLDER_BLURRY: 1, FOLDER_REVIEW: 4}
    win.apply_result(_fake_result(counts))

    texts = [win.sidebar._list.item(i).text() for i in range(4)]
    assert any("Best Shots" in t and "3" in t for t in texts)
    assert any("Review" in t and "4" in t for t in texts)
    assert "complete" in win.statusBar().currentMessage().lower()


def test_analyzing_state_toggles_actions(qapp, tmp_path: Path):
    _make_photos(tmp_path / "photos")
    win = MainWindow()
    win.load_folder(tmp_path / "photos")

    win._set_analyzing(True)
    assert win.action_cancel.isEnabled() is True
    assert win.action_analyze.isEnabled() is False
    assert win.action_open.isEnabled() is False

    win._set_analyzing(False)
    assert win.action_cancel.isEnabled() is False
    assert win.action_analyze.isEnabled() is True
    assert win.action_open.isEnabled() is True
