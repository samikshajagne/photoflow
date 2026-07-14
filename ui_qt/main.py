"""
Entry point for the PhotoFlow desktop application.

Run with:  python -m ui_qt.main   (or)   python ui_qt/main.py
"""

from __future__ import annotations

import sys
import traceback
from types import TracebackType
from typing import Optional, Type

from PyQt6.QtWidgets import QApplication, QMessageBox

from utils.config import ConfigError, load_config
from utils.logger import get_logger, setup_logging
from ui_qt.theme import apply_dark_theme
from ui_qt.views.main_window import MainWindow

logger = get_logger("ui_qt.main")


def _install_excepthook() -> None:
    """
    Route otherwise-uncaught exceptions to the log and a dialog.

    Without this, an exception escaping a Qt slot prints a traceback to a
    console the user never sees and the window simply vanishes. We log the full
    traceback (so it lands in logs/photoflow.log for support) and show a concise
    dialog instead of crashing silently.
    """
    previous = sys.excepthook

    def _hook(
        exc_type: Type[BaseException],
        exc: BaseException,
        tb: Optional[TracebackType],
    ) -> None:
        # Let Ctrl-C behave normally.
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        logger.critical(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        try:
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "PhotoFlow - unexpected error",
                    f"{exc_type.__name__}: {exc}\n\n"
                    "The details were saved to the log file (logs/photoflow.log).",
                )
        except Exception:  # noqa: BLE001 - never let the handler itself crash
            pass

    sys.excepthook = _hook


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    try:
        setup_logging(load_config().logging)
    except ConfigError as exc:
        # Logging setup is best-effort; the app can still run with defaults, but
        # the user should know their config was not applied.
        logger.warning("Could not load configuration; using defaults: %s", exc)

    _install_excepthook()

    app = QApplication(argv)
    app.setApplicationName("PhotoFlow")
    apply_dark_theme(app)

    window = MainWindow()
    window.show()
    logger.info("PhotoFlow desktop UI started.")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
