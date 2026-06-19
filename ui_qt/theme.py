"""
Dark theme for the PhotoFlow desktop UI.

Applies the cross-platform Fusion style with a graphite dark palette, then
layers a QSS stylesheet for finer details (toolbar, sidebar, panels). Keeping
the theme in one place means widgets carry no inline styling.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from utils.logger import get_logger

logger = get_logger("ui_qt.theme")

_QSS_PATH = Path(__file__).resolve().parent / "theme" / "dark.qss"

# Core palette colors (Lightroom/Photoshop-like graphite).
_BG = QColor(30, 31, 34)
_BG_ALT = QColor(37, 38, 42)
_BASE = QColor(24, 25, 28)
_TEXT = QColor(220, 221, 224)
_TEXT_DIM = QColor(150, 152, 158)
_ACCENT = QColor(58, 130, 246)
_DISABLED = QColor(90, 92, 98)


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, _BG)
    p.setColor(QPalette.ColorRole.WindowText, _TEXT)
    p.setColor(QPalette.ColorRole.Base, _BASE)
    p.setColor(QPalette.ColorRole.AlternateBase, _BG_ALT)
    p.setColor(QPalette.ColorRole.Text, _TEXT)
    p.setColor(QPalette.ColorRole.Button, _BG_ALT)
    p.setColor(QPalette.ColorRole.ButtonText, _TEXT)
    p.setColor(QPalette.ColorRole.ToolTipBase, _BG_ALT)
    p.setColor(QPalette.ColorRole.ToolTipText, _TEXT)
    p.setColor(QPalette.ColorRole.Highlight, _ACCENT)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.PlaceholderText, _TEXT_DIM)
    for group in (QPalette.ColorGroup.Disabled,):
        p.setColor(group, QPalette.ColorRole.Text, _DISABLED)
        p.setColor(group, QPalette.ColorRole.ButtonText, _DISABLED)
        p.setColor(group, QPalette.ColorRole.WindowText, _DISABLED)
    return p


def apply_dark_theme(app: QApplication) -> None:
    """Apply the Fusion style, dark palette, and QSS stylesheet to ``app``."""
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    try:
        app.setStyleSheet(_QSS_PATH.read_text(encoding="utf-8"))
    except OSError as exc:  # pragma: no cover - stylesheet is optional polish
        logger.warning("Could not load stylesheet '%s': %s", _QSS_PATH, exc)
    logger.info("Applied dark theme (Fusion + QSS).")
