"""
Licence and privacy dialog.

Two jobs in one window, because they're the two things a customer needs to
decide once and then forget: entering their licence key, and choosing whether to
share anonymous usage counts.

Deliberate UX choices
---------------------
* **It never traps the user.** Even with an expired trial the dialog can be
  closed; ``MainWindow`` decides what to do next. A modal box a photographer
  can't dismiss on a deadline is a support call and a refund request.
* **Offline is reported differently from wrong.** "Couldn't reach the server"
  and "that key isn't valid" need different wording, because the fix is
  different. :class:`~core.licensing.ActivationResult` carries the distinction.
* **The privacy section shows the actual payload.** Rather than describing what
  gets sent, it prints exactly what would be sent (from
  ``Telemetry.describe()``). It's more convincing than any policy paragraph, and
  it can't drift out of date because it's generated from the real data.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.licensing import (
    STATE_ACTIVE,
    STATE_EXPIRED,
    STATE_GRACE,
    STATE_TRIAL,
    STATE_TRIAL_EXPIRED,
    LicenseManager,
)
from core.telemetry import Telemetry
from utils.logger import get_logger
from utils.version import COMPANY_DOMAIN, SUPPORT_EMAIL, __version__

logger = get_logger("ui_qt.license_dialog")

_STATE_COLOURS = {
    STATE_ACTIVE: "#3fbf7f",
    STATE_TRIAL: "#4a7dff",
    STATE_GRACE: "#e0a03a",
    STATE_TRIAL_EXPIRED: "#ff7a59",
    STATE_EXPIRED: "#ff7a59",
}


class LicenseDialog(QDialog):
    """Enter a licence key and set the usage-sharing preference."""

    def __init__(
        self,
        manager: LicenseManager,
        telemetry: Optional[Telemetry] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._telemetry = telemetry
        self._preview_shown = False
        self.setWindowTitle("PhotoFlow — Licence")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        # -- status ---------------------------------------------------------
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self.status_label)

        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #9a9ca3;")
        layout.addWidget(self.detail_label)

        # -- key entry ------------------------------------------------------
        key_box = QGroupBox("Licence key")
        key_layout = QVBoxLayout(key_box)

        row = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("PHOTOFLOW-XXXX-XXXX-XXXX")
        self.key_input.returnPressed.connect(self._on_activate)
        self.btn_activate = QPushButton("Activate")
        self.btn_activate.setDefault(True)
        self.btn_activate.clicked.connect(self._on_activate)
        row.addWidget(self.key_input, 1)
        row.addWidget(self.btn_activate)
        key_layout.addLayout(row)

        self.activate_result = QLabel()
        self.activate_result.setWordWrap(True)
        key_layout.addWidget(self.activate_result)

        self.btn_deactivate = QPushButton("Remove licence from this computer")
        self.btn_deactivate.clicked.connect(self._on_deactivate)
        key_layout.addWidget(self.btn_deactivate)
        layout.addWidget(key_box)

        # -- privacy --------------------------------------------------------
        privacy_box = QGroupBox("Help improve PhotoFlow (optional)")
        privacy_layout = QVBoxLayout(privacy_box)

        self.consent_check = QCheckBox("Share anonymous usage counts")
        consent = manager.telemetry_consent()
        self.consent_check.setChecked(bool(consent))
        self.consent_check.toggled.connect(self._on_consent_toggled)
        privacy_layout.addWidget(self.consent_check)

        blurb = QLabel(
            "Your photos, file names and client details are never sent — "
            "processing always happens on this computer. Sharing counts of how "
            "often each tool is used simply helps us decide what to improve."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet("color: #9a9ca3;")
        privacy_layout.addWidget(blurb)

        self.btn_show_data = QPushButton("Show exactly what would be sent")
        self.btn_show_data.clicked.connect(self._on_show_data)
        privacy_layout.addWidget(self.btn_show_data)

        self.data_preview = QLabel()
        self.data_preview.setWordWrap(True)
        self.data_preview.setVisible(False)
        self.data_preview.setStyleSheet(
            "background:#14161d; border:1px solid #232732; border-radius:8px;"
            "padding:10px; color:#a5a9b6; font-family: Consolas, monospace;"
        )
        privacy_layout.addWidget(self.data_preview)
        layout.addWidget(privacy_box)

        # -- support ---------------------------------------------------------
        support_row = QHBoxLayout()
        self.btn_diagnostics = QPushButton("Copy diagnostics")
        self.btn_diagnostics.setToolTip(
            "Copies version, system details and recent log lines to the "
            "clipboard so you can paste them into a support email.\n"
            "No photos, file names or client details are included."
        )
        self.btn_diagnostics.clicked.connect(self._on_copy_diagnostics)
        support_row.addWidget(self.btn_diagnostics)
        support_row.addStretch(1)
        layout.addLayout(support_row)

        self.support_result = QLabel()
        self.support_result.setWordWrap(True)
        self.support_result.setStyleSheet("color: #9a9ca3; font-size: 12px;")
        layout.addWidget(self.support_result)

        footer = QLabel(
            f"PhotoFlow {__version__} · Need a key or having trouble? "
            f"Email {SUPPORT_EMAIL} or visit {COMPANY_DOMAIN}"
        )
        footer.setWordWrap(True)
        footer.setStyleSheet("color: #71747c; font-size: 12px;")
        layout.addWidget(footer)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        self.refresh()

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Redraw the status text and enable/disable the licence controls."""
        status = self._manager.status()
        colour = _STATE_COLOURS.get(status.state, "#a5a9b6")
        headline = {
            STATE_ACTIVE: "Licensed",
            STATE_TRIAL: f"Free trial — {status.days_left} day"
                         f"{'s' if status.days_left != 1 else ''} remaining",
            STATE_GRACE: "Licensed (working offline)",
            STATE_TRIAL_EXPIRED: "Trial ended",
            STATE_EXPIRED: "Licence needs attention",
        }.get(status.state, status.state)

        self.status_label.setText(headline)
        self.status_label.setStyleSheet(
            f"font-size: 15px; font-weight: 600; color: {colour};"
        )
        detail = status.message
        if status.customer:
            detail = f"{detail}  Licensed to {status.customer}."
        self.detail_label.setText(detail)

        licensed = status.state in (STATE_ACTIVE, STATE_GRACE)
        self.btn_deactivate.setVisible(licensed)
        self.key_input.setVisible(not licensed)
        self.btn_activate.setVisible(not licensed)
        if licensed and status.key:
            # Show only the tail: enough to identify it, not enough to copy.
            self.detail_label.setText(
                f"{detail}  Key ending {status.key[-6:]}."
            )

    # ------------------------------------------------------------------ #
    def _on_activate(self) -> None:
        key = self.key_input.text().strip()
        self.btn_activate.setEnabled(False)
        self.activate_result.setText("Checking…")
        try:
            result = self._manager.activate(key)
        except Exception as exc:  # noqa: BLE001 - never crash on activation
            logger.warning("Activation raised: %s", exc)
            self.activate_result.setText(
                "Something went wrong while activating. Please try again."
            )
            self.activate_result.setStyleSheet("color:#ff7a59;")
            return
        finally:
            self.btn_activate.setEnabled(True)

        if result.ok:
            self.activate_result.setText("Activated — thank you.")
            self.activate_result.setStyleSheet("color:#3fbf7f;")
            self.key_input.clear()
        elif result.offline:
            # Distinct wording: the key may be perfectly fine.
            self.activate_result.setText(
                "Couldn't reach the licence server. Check your internet "
                "connection and try again — your key is probably fine."
            )
            self.activate_result.setStyleSheet("color:#e0a03a;")
        else:
            self.activate_result.setText(result.message or "That key wasn't accepted.")
            self.activate_result.setStyleSheet("color:#ff7a59;")
        self.refresh()

    def _on_deactivate(self) -> None:
        self._manager.deactivate()
        self.activate_result.setText(
            "Licence removed from this computer. You can now activate it elsewhere."
        )
        self.activate_result.setStyleSheet("color:#a5a9b6;")
        self.refresh()

    def _on_consent_toggled(self, checked: bool) -> None:
        self._manager.set_telemetry_consent(checked)
        if self._telemetry is not None:
            self._telemetry.consent = checked
            if not checked:
                # Withdrawing consent should delete what was already collected,
                # not just stop adding to it.
                self._telemetry.clear()
                self.data_preview.setText("")
                self.data_preview.setVisible(False)

    def _on_copy_diagnostics(self) -> None:
        """
        Put a support report on the clipboard.

        Wrapped end to end: a diagnostics button that throws while reporting a
        problem would be worse than not having one.
        """
        try:
            from core.diagnostics import collect

            report = collect()
            clipboard = QApplication.clipboard()
            if clipboard is None:  # no clipboard offscreen/headless
                raise RuntimeError("clipboard unavailable")
            clipboard.setText(report)
            lines = len(report.splitlines())
            self.support_result.setText(
                f"Copied {lines} lines to the clipboard — paste them into your "
                f"email to {SUPPORT_EMAIL}."
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not copy diagnostics: %s", exc)
            self.support_result.setText(
                f"Could not copy to the clipboard ({exc}). The same details are "
                "in the log file."
            )

    def _on_show_data(self) -> None:
        # Tracked explicitly rather than read back from isVisible(): Qt reports
        # a widget as not visible whenever its window is hidden, minimised or on
        # an inactive tab, which would make this toggle behave unpredictably.
        self._preview_shown = not self._preview_shown
        if not self._preview_shown:
            self.data_preview.setVisible(False)
            return
        if self._telemetry is None:
            self.data_preview.setText("Usage sharing is not configured in this build.")
        else:
            self.data_preview.setText(self._telemetry.describe())
        self.data_preview.setVisible(True)
