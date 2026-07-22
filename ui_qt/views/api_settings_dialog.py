"""
OpenAI API key settings dialog (Implementation Plan — Component 5).

Lets users enter and persist their OpenAI API key without editing a file.
The key is saved to the project-root ``.env`` file so it survives restarts and
is picked up by both the main process and the analysis subprocess.

Features
--------
- Masked key input (shown as ••••) with a "Show" toggle.
- "Test connection" button — sends a tiny (~10-token) GPT-4o Chat request to
  verify the key is valid **before** the user runs a full analysis.
- "Clear key" button to remove a saved key.
- Non-blocking: the test runs in a ``QThread`` so the UI stays responsive.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# Project root = three levels up from this file (ui_qt/views/api_settings_dialog.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

# The env-var name this dialog manages.
_KEY_NAME = "OPENAI_API_KEY"


# ──────────────────────────────────────────────────────────────────────────── #
# Background test worker
# ──────────────────────────────────────────────────────────────────────────── #


class _TestWorker(QThread):
    """Sends a minimal API request in a background thread to verify the key."""

    finished = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, api_key: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._api_key = api_key

    def run(self) -> None:  # noqa: D102
        try:
            import openai  # noqa: PLC0415

            client = openai.OpenAI(api_key=self._api_key)
            # Cheapest possible call: 1-token completion.
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                timeout=15,
            )
            self.finished.emit(True, "✓ Connection successful — key is valid.")
        except ImportError:
            self.finished.emit(
                False,
                "The 'openai' package is not installed.\n"
                "Run: pip install openai",
            )
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(False, f"✗ Connection failed:\n{exc}")


# ──────────────────────────────────────────────────────────────────────────── #
# Dialog
# ──────────────────────────────────────────────────────────────────────────── #


class ApiSettingsDialog(QDialog):
    """
    Modal dialog for entering / testing / saving the OpenAI API key.

    Usage::

        dlg = ApiSettingsDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Key has been saved to .env; restart analysis if needed.
            pass
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OpenAI API Key Settings")
        self.setMinimumWidth(520)
        self.setModal(True)

        self._test_worker: Optional[_TestWorker] = None
        self._build_ui()
        self._load_current_key()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # ── Header ──────────────────────────────────────────────────────
        title = QLabel("OpenAI API Key")
        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        info = QLabel(
            "PhotoFlow uses GPT-4o Vision to classify wedding events (Haldi,\n"
            "Mehndi, Ceremony, Baraat, Reception, Portraits) from your photos.\n\n"
            "Get your key at: <a href='https://platform.openai.com/api-keys'>"
            "platform.openai.com/api-keys</a>"
        )
        info.setOpenExternalLinks(True)
        info.setWordWrap(True)
        layout.addWidget(info)

        # ── Key input row ────────────────────────────────────────────────
        key_row = QHBoxLayout()
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("sk-…")
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        key_row.addWidget(self._key_edit)

        self._show_btn = QPushButton("Show")
        self._show_btn.setFixedWidth(56)
        self._show_btn.setCheckable(True)
        self._show_btn.toggled.connect(self._toggle_visibility)
        key_row.addWidget(self._show_btn)
        layout.addLayout(key_row)

        # ── Action buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._test_btn = QPushButton("Test connection")
        self._test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(self._test_btn)

        self._clear_btn = QPushButton("Clear key")
        self._clear_btn.clicked.connect(self._clear_key)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Status label ─────────────────────────────────────────────────
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # ── Dialog buttons ───────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def _toggle_visibility(self, checked: bool) -> None:
        self._key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self._show_btn.setText("Hide" if checked else "Show")

    def _test_connection(self) -> None:
        key = self._key_edit.text().strip()
        if not key:
            self._set_status("Enter an API key first.", error=True)
            return
        self._set_status("Testing connection…")
        self._test_btn.setEnabled(False)
        self._test_worker = _TestWorker(key, parent=self)
        self._test_worker.finished.connect(self._on_test_done)
        self._test_worker.start()

    def _on_test_done(self, success: bool, message: str) -> None:
        self._set_status(message, error=not success)
        self._test_btn.setEnabled(True)
        self._test_worker = None

    def _clear_key(self) -> None:
        self._key_edit.clear()
        _write_env_key("")
        os.environ.pop(_KEY_NAME, None)
        self._set_status("API key cleared.")

    def _save_and_accept(self) -> None:
        key = self._key_edit.text().strip()
        _write_env_key(key)
        if key:
            os.environ[_KEY_NAME] = key
        else:
            os.environ.pop(_KEY_NAME, None)
        self.accept()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _load_current_key(self) -> None:
        """Pre-fill the field with whatever key is already saved."""
        key = _read_env_key()
        if key:
            self._key_edit.setText(key)

    def _set_status(self, msg: str, *, error: bool = False) -> None:
        color = "#c0392b" if error else "#27ae60"
        self._status_label.setText(f"<span style='color:{color}'>{msg}</span>")


# ──────────────────────────────────────────────────────────────────────────── #
# .env file helpers
# ──────────────────────────────────────────────────────────────────────────── #


def _read_env_key() -> str:
    """Read the current key from the .env file (or from os.environ as fallback)."""
    # Prefer .env file over environment so we show the persisted value.
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(rf"^{re.escape(_KEY_NAME)}\s*=\s*(.+)$", line.strip())
            if m:
                val = m.group(1).strip().strip('"').strip("'")
                if val and val != "sk-your-openai-key-here":
                    return val
    return os.environ.get(_KEY_NAME, "")


def _write_env_key(key: str) -> None:
    """
    Upsert the ``OPENAI_API_KEY`` line in the project-root ``.env`` file.

    Preserves all other lines so existing settings are not overwritten.
    Creates the file if it doesn't exist.
    """
    lines: list[str] = []
    if _ENV_FILE.exists():
        lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()

    pattern = re.compile(rf"^{re.escape(_KEY_NAME)}\s*=")
    updated = False
    result: list[str] = []
    for line in lines:
        if pattern.match(line.strip()):
            if not updated:
                if key:
                    result.append(f"{_KEY_NAME}={key}")
                # If key is empty, drop the line (i.e., clear it).
                updated = True
        else:
            result.append(line)

    if not updated and key:
        result.append(f"{_KEY_NAME}={key}")

    _ENV_FILE.write_text("\n".join(result) + "\n", encoding="utf-8")


__all__ = ["ApiSettingsDialog"]
