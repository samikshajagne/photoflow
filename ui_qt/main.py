"""
Entry point for the PhotoFlow desktop application.

Run with:  python -m ui_qt.main   (or)   python ui_qt/main.py
"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from utils.config import ConfigError, load_config
from utils.logger import get_logger, setup_logging
from ui_qt.theme import apply_dark_theme
from ui_qt.views.main_window import MainWindow

logger = get_logger("ui_qt.main")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    try:
        setup_logging(load_config().logging)
    except ConfigError:
        # Logging setup is best-effort; the app can still run with defaults.
        pass

    app = QApplication(argv)
    app.setApplicationName("PhotoFlow")
    apply_dark_theme(app)

    window = MainWindow()
    window.show()
    logger.info("PhotoFlow desktop UI started.")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
