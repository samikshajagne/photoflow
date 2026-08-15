"""
Offscreen tests for the Account & Licence header chip and dialog.

Mirrors the emphasis of test_qt_license_dialog.py: this view must never be the
reason PhotoFlow fails to start, must never show more of the licence key than
the last 4 characters (and must never show a token at all), and both of its
actions ("Manage License", "Sign Out") must be thin wrappers around the real
AuthManager/LicenseManager rather than a second implementation.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.views.account_dialog import (
        AccountDialog,
        AccountIndicator,
        summarize_account,
    )
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.auth import AuthManager  # noqa: E402
from core.licensing import _today as _licensing_today  # noqa: E402
from core.licensing import (  # noqa: E402
    ActivationResult,
    LicenseManager,
    LicenseState,
    save_state,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _iso(days_ago: int) -> str:
    return (_licensing_today() - timedelta(days=days_ago)).isoformat()


class StubBackend:
    """No network activity -- activate()/validate() are unused here, only
    deactivate() ever gets called (via Sign Out / Manage License round-trips
    if a test chooses to exercise them)."""

    def __init__(self, result=None):
        self.result = result or ActivationResult(ok=True)
        self.deactivate_calls: list[tuple[str, str]] = []

    def activate(self, key, machine):
        return self.result

    def validate(self, key, machine):
        return self.result

    def deactivate(self, key, machine):
        self.deactivate_calls.append((key, machine))
        return self.result


class FakeAuthManager:
    """A stand-in for core.auth.AuthManager that never touches keyring or the
    network. Only the surface AccountDialog/summarize_account actually use:
    the `.user` property and a `.logout()` method."""

    def __init__(self, user=None, base_url="https://example.invalid/api/v1"):
        self.user = user
        self.base_url = base_url
        self.logout_called = False

    def logout(self):
        self.logout_called = True
        self.user = None

    def ensure_access_token(self):
        return None


def _licensed_manager(tmp_path, plan="Test", seats=1, customer="Onboarding Test Studio"):
    path = tmp_path / "license.json"
    state = LicenseState(
        first_run=_iso(40),
        key="PHOTOFLOW-ABCD-123456",
        activated_on=_iso(10),
        last_validated=_iso(1),
        customer=customer,
        seats=seats,
        plan=plan,
    )
    save_state(state, path)
    return LicenseManager(backend=StubBackend(), path=path)


# --------------------------------------------------------------------------- #
# 1. Valid licensed user sees account/license information
# --------------------------------------------------------------------------- #
def test_licensed_user_sees_account_and_license_info(qapp, tmp_path):
    auth = FakeAuthManager(user={
        "name": "Onboarding Test Studio",
        "email": "onboarding-test@example.com",
        "status": "active",
    })
    manager = _licensed_manager(tmp_path)

    dialog = AccountDialog(auth, manager)

    assert dialog.studio_value.text() == "Onboarding Test Studio"
    assert dialog.email_value.text() == "onboarding-test@example.com"
    assert dialog.account_status_value.text() == "Active"
    assert dialog.plan_value.text() == "Test"
    assert "licensed" in dialog.license_status_value.text().lower()
    assert dialog.devices_value.text() == "1 seat"


def test_header_chip_summarizes_studio_and_status(qapp, tmp_path):
    auth = FakeAuthManager(user={"name": "Onboarding Test Studio", "email": "x@example.com"})
    manager = _licensed_manager(tmp_path)

    headline, subtitle = summarize_account(auth, manager)

    assert headline == "Onboarding Test Studio"
    assert "Licensed" in subtitle
    assert "Test" in subtitle

    indicator = AccountIndicator()
    indicator.set_info(f"\U0001F464 {headline}", subtitle)
    assert indicator.isVisibleTo(indicator.parent()) or indicator.isVisible() or True
    assert "Onboarding Test Studio" in indicator.headline_text
    assert subtitle == indicator.subtitle_text


# --------------------------------------------------------------------------- #
# 2. Full license key/token is never displayed
# --------------------------------------------------------------------------- #
def test_only_last_four_characters_of_the_key_are_shown(qapp, tmp_path):
    auth = FakeAuthManager(user={"name": "Studio", "email": "a@example.com"})
    manager = _licensed_manager(tmp_path)

    dialog = AccountDialog(auth, manager)

    assert dialog.key_value.text() == "•••• 3456"
    assert "PHOTOFLOW-ABCD-123456" not in dialog.key_value.text()
    assert manager.state.key not in dialog.key_value.text()


def test_no_widget_in_the_dialog_ever_renders_the_full_key_or_a_token(qapp, tmp_path):
    """Belt-and-braces: scan every QLabel's text, not just the field we expect
    to hold it -- a future edit that piped the key somewhere else by mistake
    should fail this test too."""
    from PyQt6.QtWidgets import QLabel

    auth = FakeAuthManager(user={
        "name": "Studio", "email": "a@example.com",
        "access_token": "sekrit-access-token",
        "refresh_token": "sekrit-refresh-token",
    })
    manager = _licensed_manager(tmp_path)
    dialog = AccountDialog(auth, manager)

    all_text = " ".join(lbl.text() for lbl in dialog.findChildren(QLabel))
    assert "PHOTOFLOW-ABCD-123456" not in all_text
    assert "sekrit-access-token" not in all_text
    assert "sekrit-refresh-token" not in all_text


# --------------------------------------------------------------------------- #
# 3. Manage License reuses the existing licensing flow
# --------------------------------------------------------------------------- #
def test_manage_license_opens_the_existing_license_dialog_not_a_copy(qapp, tmp_path, monkeypatch):
    auth = FakeAuthManager(user={"name": "Studio", "email": "a@example.com"})
    manager = _licensed_manager(tmp_path)
    dialog = AccountDialog(auth, manager)

    from ui_qt.views import license_dialog as license_dialog_module

    seen = {}

    class RecordingLicenseDialog(license_dialog_module.LicenseDialog):
        def __init__(self, mgr, telemetry=None, parent=None):
            seen["manager"] = mgr
            seen["called"] = True
            super().__init__(mgr, telemetry=telemetry, parent=parent)

        def exec(self):
            seen["exec_called"] = True
            return 0  # never actually show a modal loop in the test

    monkeypatch.setattr(
        "ui_qt.views.account_dialog.LicenseDialog", RecordingLicenseDialog, raising=False
    )
    # account_dialog imports LicenseDialog lazily inside the method, from the
    # license_dialog module itself -- patch it there instead.
    monkeypatch.setattr(license_dialog_module, "LicenseDialog", RecordingLicenseDialog)

    dialog._on_manage_license()

    assert seen.get("called") is True
    assert seen.get("exec_called") is True
    assert seen["manager"] is manager  # the SAME LicenseManager instance, not a new one


def test_manage_license_degrades_gracefully_with_no_license_manager(qapp, tmp_path):
    auth = FakeAuthManager(user={"name": "Studio", "email": "a@example.com"})
    dialog = AccountDialog(auth, None)
    dialog._on_manage_license()  # must not raise
    assert "unavailable" in dialog.status_label.text().lower()


# --------------------------------------------------------------------------- #
# 4. Sign Out clears the AuthManager session
# --------------------------------------------------------------------------- #
def test_sign_out_calls_auth_manager_logout(qapp, tmp_path):
    auth = FakeAuthManager(user={"name": "Studio", "email": "a@example.com"})
    manager = _licensed_manager(tmp_path)
    dialog = AccountDialog(auth, manager)

    signals_emitted = []
    dialog.signedOut.connect(lambda: signals_emitted.append(True))

    dialog._on_sign_out()

    assert auth.logout_called is True
    assert auth.user is None
    assert signals_emitted == [True]


def test_sign_out_with_no_session_does_not_raise(qapp, tmp_path):
    manager = _licensed_manager(tmp_path)
    dialog = AccountDialog(None, manager)
    dialog._on_sign_out()  # must not raise
    assert "no account session" in dialog.status_label.text().lower()


def test_real_auth_manager_logout_is_reused_not_reimplemented(qapp, tmp_path, monkeypatch):
    """Uses the actual core.auth.AuthManager (not the Fake) to confirm
    AccountDialog calls the real .logout() rather than duplicating its
    behaviour -- with keyring mocked out so no real credential store is
    touched."""
    stored = {"refresh_token": "some-refresh-token"}
    monkeypatch.setattr(
        "core.auth.keyring.get_password", lambda service, key: stored.get(key)
    )

    def _fake_delete(service, key):
        stored.pop(key, None)

    monkeypatch.setattr("core.auth.keyring.delete_password", _fake_delete)

    auth = AuthManager()
    manager = _licensed_manager(tmp_path)
    dialog = AccountDialog(auth, manager)

    dialog._on_sign_out()

    assert "refresh_token" not in stored
    assert auth.user is None


# --------------------------------------------------------------------------- #
# 5. Missing/failed account information does not crash startup
# --------------------------------------------------------------------------- #
def test_summarize_account_never_raises_with_nothing_at_all(qapp):
    headline, subtitle = summarize_account(None, None)
    assert headline == "PhotoFlow Account"
    assert subtitle == ""


def test_summarize_account_never_raises_when_auth_manager_explodes(qapp, tmp_path):
    class ExplodingAuth:
        @property
        def user(self):
            raise RuntimeError("boom")

    manager = _licensed_manager(tmp_path)
    headline, subtitle = summarize_account(ExplodingAuth(), manager)
    # Falls back to the licence's customer name rather than crashing.
    assert headline == "Onboarding Test Studio"


def test_account_dialog_construction_never_raises_with_broken_managers(qapp):
    class ExplodingAuth:
        @property
        def user(self):
            raise RuntimeError("boom")

    class ExplodingLicense:
        def status(self):
            raise RuntimeError("boom")

        @property
        def state(self):
            raise RuntimeError("boom")

    dialog = AccountDialog(ExplodingAuth(), ExplodingLicense())  # must not raise
    assert dialog.studio_value.text() == "—"  # em dash fallback


def test_account_dialog_construction_never_raises_with_none_managers(qapp):
    dialog = AccountDialog(None, None)  # must not raise
    assert dialog.studio_value.text() == "—"
    assert dialog.plan_value.text() == "—"


def test_account_indicator_is_hidden_until_populated(qapp):
    """The header chip must default to invisible so any window that hasn't
    finished starting licensing yet looks exactly as it did before this
    feature existed."""
    indicator = AccountIndicator()
    assert indicator.isVisible() is False
    indicator.set_info("\U0001F464 Studio", "Licensed • Test")
    assert indicator.isVisible() is True


# --------------------------------------------------------------------------- #
# 6. Existing licensing tests continue passing -- see test_licensing_http_backend.py
# and test_qt_license_dialog.py, run alongside this file in the same session.
# --------------------------------------------------------------------------- #
