"""
Qt-side controller for running analysis in a separate process.

``AnalysisController`` launches the pipeline in a child process (via the
``spawn`` start method, so it never inherits the GUI's Qt/thread state) and
bridges that process back to the Qt world:

- The heavy work runs in the child (CPU-bound image analysis), keeping the UI
  responsive and the run cancelable by terminating the process.
- A thin :class:`_ResultWaiter` ``QThread`` blocks on the inter-process queue
  (doing no real work, just IPC) and re-emits messages as Qt signals on the
  GUI thread via queued connections.

Signals:
    started()              analysis has begun
    progress(str)          a live status line (forwarded backend log record)
    finished(object)       success; carries the PipelineResult
    failed(str)            failure; carries an error message
    cancelled()            the run was cancelled by the user
"""

from __future__ import annotations

import multiprocessing as mp
import queue as pyqueue
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ui_qt.workers.analysis_process import run_pipeline_to_queue
from utils.logger import get_logger

logger = get_logger("ui_qt.analysis")

# How long the waiter blocks on each queue read before re-checking liveness.
_POLL_TIMEOUT_S = 0.1


class _ResultWaiter(QThread):
    """Drains the IPC queue and re-emits its messages as Qt signals."""

    logMessage = pyqtSignal(str)
    resultReady = pyqtSignal(object)
    errorRaised = pyqtSignal(str)
    finishedAll = pyqtSignal()

    def __init__(self, queue, process) -> None:
        super().__init__()
        self._queue = queue
        self._process = process
        self._stop = False

    def run(self) -> None:
        while not self._stop:
            try:
                kind, payload = self._queue.get(timeout=_POLL_TIMEOUT_S)
            except pyqueue.Empty:
                # If the child died without sending 'done', stop waiting.
                if not self._process.is_alive():
                    break
                continue
            if kind == "log":
                self.logMessage.emit(payload)
            elif kind == "result":
                self.resultReady.emit(payload)
            elif kind == "error":
                self.errorRaised.emit(payload)
            elif kind == "done":
                break
        self.finishedAll.emit()

    def stop(self) -> None:
        self._stop = True


class AnalysisController(QObject):
    """Runs the pipeline in a child process and reports progress via signals."""

    started = pyqtSignal()
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)  # PipelineResult
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # 'spawn' avoids inheriting the GUI process's Qt/thread state and is
        # the cross-platform-consistent choice.
        self._ctx = mp.get_context("spawn")
        self._process: Optional[mp.process.BaseProcess] = None
        self._waiter: Optional[_ResultWaiter] = None
        self._queue = None
        self._cancelled = False
        self._got_result = False

    def is_running(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def start(self, input_folder: str, output_folder: str) -> None:
        """Launch analysis. No-op if a run is already in progress."""
        if self.is_running():
            logger.warning("Analysis already running; ignoring start request.")
            return
        self._cancelled = False
        self._got_result = False
        self._queue = self._ctx.Queue()
        self._process = self._ctx.Process(
            target=run_pipeline_to_queue,
            args=(str(input_folder), str(output_folder), self._queue),
            daemon=True,
        )
        self._process.start()

        self._waiter = _ResultWaiter(self._queue, self._process)
        self._waiter.logMessage.connect(self.progress)
        self._waiter.resultReady.connect(self._on_result)
        self._waiter.errorRaised.connect(self.failed)
        self._waiter.finishedAll.connect(self._on_waiter_finished)
        self._waiter.start()

        logger.info("Analysis process started (pid=%s).", self._process.pid)
        self.started.emit()

    def cancel(self) -> None:
        """Terminate the running analysis process, if any."""
        if not self.is_running():
            return
        logger.info("Cancelling analysis (pid=%s).", self._process.pid)
        self._cancelled = True
        if self._process is not None:
            self._process.terminate()
        if self._waiter is not None:
            self._waiter.stop()

    # ----------------------------------------------------------------- #
    # Internal slots (run on the GUI thread)
    # ----------------------------------------------------------------- #
    def _on_result(self, result: object) -> None:
        self._got_result = True
        self.finished.emit(result)

    def _on_waiter_finished(self) -> None:
        if self._process is not None:
            self._process.join(timeout=2.0)
        if self._cancelled and not self._got_result:
            self.cancelled.emit()
        self._cleanup()

    def _cleanup(self) -> None:
        self._waiter = None
        self._process = None
        self._queue = None
