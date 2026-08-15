"""
Account & Licence panel, plus the small header chip that opens it.

This is a *view* over state that already exists elsewhere -- ``AuthManager``
(``core/auth.py``) and ``LicenseManager`` (``core/licensing.py``). Nothing here
talks to the network, stores a credential, or duplicates the activate/validate/
deactivate flow: "Manage License" opens the very same
:class:`~ui_qt.views.license_dialog.LicenseDialog` used at startup, and
"Sign Out" calls :meth:`~core.auth.AuthManager.logout` directly.

Deliberate choices, mirroring ``license_dialog.py``'s own notes:
* **Never the full licence key.** At most the last 4 characters -- enough to
  confirm which key is active, not enough to be useful to anyone else. Access
  and refresh tokens are never shown at all.
* **Every field degrades independently.** A missing studio name, a licence
  that hasn't been activated, an ``AuthManager`` that failed to construct --
  none of it raises. The header chip simply shows less, never crashes.
* **The header chip is optional chrome.** It stays hidden until
  ``MainWindow.set_account_context`` supplies real data, so a bare
  ``MainWindow()`` (every existing test, and any mode before licensing has
  finished starting up) looks exactly as it did before this module existed.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.auth import AuthManager
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

logger = get_logger("ui_qt.account_dialog")

# Same wording family as license_dialog.py's headline map, kept local rather
# than imported: this dialog needs a compact one-line status ("Licensed"),
# license_dialog.py needs a fuller headline ("Free trial — 3 days remaining").
# Duplicating five short strings is cheaper than coupling the two dialogs.
_STATUS_HEADLINES = {
    STATE_ACTIVE: "Licensed",
    STATE_TRIAL: "Trial",
    STATE_GRACE: "Licensed (offline)",
    STATE_TRIAL_EXPIRED: "Trial ended",
    STATE_EXPIRED: "Needs attention",
}


def summarize_account(
    auth_manager: Optional[AuthManager],
    license_manager: Optional[LicenseManager],
) -> tuple[str, str]:
    """
    Return ``(headline, subtitle)`` for the header chip, e.g.
    ``("Onboarding Test Studio", "Licensed • Test")``.

    Never raises. A problem reading either manager just falls back to a
    generic label -- this is a display convenience, not something that should
    ever be able to affect whether the app opens.
    """
    studio = "PhotoFlow Account"
    user = None
    try:
        user = auth_manager.user if auth_manager is not None else None
    except Exception as exc:  # noqa: BLE001 - header text must never break the UI
        logger.debug("Could not read the account name for the header chip: %s", exc)

    # Read the account name and the licence-customer fallback as two
    # independent attempts: a broken AuthManager must not also suppress the
    # licence-side fallback, or vice versa.
    try:
        if user and user.get("name"):
            studio = user["name"]
        elif license_manager is not None and license_manager.state.customer:
            studio = license_manager.state.customer
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not summarize account for the header chip: %s", exc)

    subtitle = ""
    try:
        if license_manager is not None:
            status = license_manager.status()
            headline = _STATUS_HEADLINES.get(status.state, status.state)
            plan = license_manager.state.plan
            subtitle = f"{headline} • {plan}" if plan else headline
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not summarize licence for the header chip: %s", exc)

    return studio, subtitle


class AccountIndicator(QFrame):
    """
    A small clickable chip for the window header:

        👤 Onboarding Test Studio
           Licensed • Test                ▾

    Hidden until :meth:`set_info` is called -- see the module docstring for
    why that matters for every window that never touches licensing at all.
    """

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("AccountIndicator")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "#AccountIndicator {"
            "  background: #25262a;"
            "  border: 1px solid #33353b;"
            "  border-radius: 18px;"
            "}"
            "#AccountIndicator:hover {"
            "  border: 1px solid #3A82F6;"
            "  background: #292a2f;"
            "}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        avatar = QLabel("👤")
        avatar.setStyleSheet("background: transparent; border: none; font-size: 15px;")
        layout.addWidget(avatar)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self._headline = QLabel("")
        self._headline.setStyleSheet(
            "background: transparent; border: none; font-size: 12px; "
            "font-weight: 600; color: #f2f2f3;"
        )
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(
            "background: transparent; border: none; font-size: 11px; color: #96989E;"
        )
        text_col.addWidget(self._headline)
        text_col.addWidget(self._subtitle)
        layout.addLayout(text_col)

        chevron = QLabel("▾")
        chevron.setStyleSheet(
            "background: transparent; border: none; color: #96989E; font-size: 11px;"
        )
        layout.addWidget(chevron)

        self.setVisible(False)  # nothing to show until set_info() is called

    def set_info(self, headline: str, subtitle: str) -> None:
        """Populate the chip's text. Does not affect this widget's parent's
        own visibility -- see ``MainWindow.set_account_context``."""
        self._headline.setText(headline)
        self._subtitle.setText(subtitle)
        self.setVisible(True)

    @property
    def headline_text(self) -> str:
        return self._headline.text()

    @property
    def subtitle_text(self) -> str:
        return self._subtitle.text()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().mousePressEvent(event)
        self.clicked.emit()


class AccountDialog(QDialog):
    """
    Read-only account summary, plus the two actions a signed-in customer
    actually needs: manage the licence, or sign out.

    Reuses ``AuthManager``/``LicenseManager`` exactly as constructed by
    ``ui_qt.main`` -- no second licensing implementation, no separate
    credential store, and the full licence key is never rendered anywhere in
    this dialog.
    """

    signedOut = pyqtSignal()

    def __init__(
        self,
        auth_manager: Optional[AuthManager],
        license_manager: Optional[LicenseManager],
        telemetry: Optional[Telemetry] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._auth_manager = auth_manager
        self._license_manager = license_manager
        self._telemetry = telemetry
        self.setWindowTitle("PhotoFlow — Account & Licence")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)

        account_box = QGroupBox("Account")
        account_form = QFormLayout(account_box)
        self.studio_value = QLabel("—")
        self.email_value = QLabel("—")
        self.account_status_value = QLabel("—")
        account_form.addRow("Studio:", self.studio_value)
        account_form.addRow("Email:", self.email_value)
        account_form.addRow("Status:", self.account_status_value)
        layout.addWidget(account_box)

        license_box = QGroupBox("License")
        license_form = QFormLayout(license_box)
        self.plan_value = QLabel("—")
        self.license_status_value = QLabel("—")
        self.devices_value = QLabel("—")
        self.expires_value = QLabel("—")
        self.key_value = QLabel("—")
        license_form.addRow("Plan:", self.plan_value)
        license_form.addRow("Status:", self.license_status_value)
        license_form.addRow("Devices:", self.devices_value)
        license_form.addRow("Expires:", self.expires_value)
        license_form.addRow("Key:", self.key_value)
        layout.addWidget(license_box)

        actions = QHBoxLayout()
        self.btn_manage_license = QPushButton("Manage License")
        self.btn_manage_license.clicked.connect(self._on_manage_license)
        actions.addWidget(self.btn_manage_license)

        self.btn_sign_out = QPushButton("Sign Out")
        self.btn_sign_out.clicked.connect(self._on_sign_out)
        actions.addWidget(self.btn_sign_out)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #96989E; font-size: 12px;")
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        self.refresh()

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Repaint every field from the live AuthManager/LicenseManager state."""
        self._refresh_account()
        self._refresh_license()

    def _refresh_account(self) -> None:
        user: Optional[dict[str, Any]] = None
        try:
            user = self._auth_manager.user if self._auth_manager is not None else None
        except Exception as exc:  # noqa: BLE001 - this panel must never crash
            logger.warning("Could not read account info: %s", exc)

        customer_fallback = ""
        try:
            if self._license_manager is not None:
                customer_fallback = self._license_manager.state.customer
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read licence customer as a fallback: %s", exc)

        studio = (user or {}).get("name") or customer_fallback or "—"
        email = (user or {}).get("email") or "—"
        status = (user or {}).get("status") or ""

        self.studio_value.setText(str(studio))
        self.email_value.setText(str(email))
        self.account_status_value.setText(str(status).title() if status else "—")

    def _refresh_license(self) -> None:
        if self._license_manager is None:
            return
        try:
            status = self._license_manager.status()
            state = self._license_manager.state
        except Exception as exc:  # noqa: BLE001 - this panel must never crash
            logger.warning("Could not read licence info: %s", exc)
            return

        self.plan_value.setText(state.plan or "—")
        self.license_status_value.setText(_STATUS_HEADLINES.get(status.state, status.state))

        seats = state.seats
        self.devices_value.setText(f"{seats} seat{'s' if seats != 1 else ''}" if seats else "—")

        activated = bool(state.key and state.activated_on)
        if state.expires_on:
            try:
                expires = date.fromisoformat(state.expires_on).strftime("%d %b %Y")
            except ValueError:
                expires = state.expires_on
        else:
            expires = "No expiry" if activated else "—"
        self.expires_value.setText(expires)

        # Never the full key -- at most the last 4 characters (per product
        # requirement), enough to confirm which key is active.
        self.key_value.setText(f"•••• {state.key[-4:]}" if state.key else "—")

    # ------------------------------------------------------------------ #
    def _on_manage_license(self) -> None:
        """Reuse the existing LicenseDialog/LicenseManager -- no second
        licensing implementation."""
        if self._license_manager is None:
            self.status_label.setText("Licence information is unavailable right now.")
            self.status_label.setStyleSheet("color:#ff7a59; font-size: 12px;")
            return

        from ui_qt.views.license_dialog import LicenseDialog

        LicenseDialog(
            self._license_manager, telemetry=self._telemetry, parent=self
        ).exec()
        self.refresh()

    def _on_sign_out(self) -> None:
        if self._auth_manager is None:
            self.status_label.setText("No account session to sign out of.")
            self.status_label.setStyleSheet("color:#96989E; font-size: 12px;")
            return

        try:
            self._auth_manager.logout()
        except Exception as exc:  # noqa: BLE001 - sign-out must never crash the app
            logger.warning("Sign-out raised: %s", exc)

        self.status_label.setText("Signed out of your PhotoFlow account.")
        self.status_label.setStyleSheet("color:#96989E; font-size: 12px;")
        self.refresh()
        self.signedOut.emit()


__all__ = ["AccountDialog", "AccountIndicator", "summarize_account"]
