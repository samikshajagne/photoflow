"""
Offscreen tests for the licence/privacy dialog.

The emphasis is on the promises the dialog makes to the customer: that it can
always be closed, that "offline" reads differently from "wrong key", and that
declining usage sharing actually deletes what was collected.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.views.license_dialog import LicenseDialog
except ImportError as exc:  # pragma: no cover - no Qt
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.licensing import _today as _licensing_today  # noqa: E402
from core.licensing import (  # noqa: E402
    GRACE_DAYS,
    RECHECK_DAYS,
    TRIAL_DAYS,
    ActivationResult,
    LicenseManager,
    LicenseState,
    save_state,
)
from core.telemetry import Telemetry  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _iso(days_ago: int) -> str:
    """
    An ISO date ``days_ago`` before *the module's own notion of today*.

    Deliberately uses ``core.licensing._today()`` rather than
    ``date.today()``: licensing works in UTC, so a local-time helper disagrees
    with it between local midnight and UTC midnight — a window that makes these
    tests fail for hours a day in any timezone ahead of UTC (IST included).
    """
    return (_licensing_today() - timedelta(days=days_ago)).isoformat()


class StubBackend:
    def __init__(self, result: ActivationResult):
        self.result = result

    def activate(self, key, machine):
        return self.result

    def validate(self, key, machine):
        return self.result


def _dialog(tmp_path, result=None, state=None, consent=None):
    path = tmp_path / "license.json"
    if state is not None:
        save_state(state, path)
    manager = LicenseManager(
        backend=StubBackend(result or ActivationResult(ok=True)), path=path
    )
    if consent is not None:
        manager.set_telemetry_consent(consent)
    counters = Telemetry(consent=bool(consent), path=tmp_path / "counters.json")
    return LicenseDialog(manager, telemetry=counters), manager, counters


# --------------------------------------------------------------------------- #
# Status display
# --------------------------------------------------------------------------- #
def test_trial_state_is_shown(qapp, tmp_path):
    dialog, _m, _t = _dialog(tmp_path, state=LicenseState(first_run=_iso(3)))
    assert "trial" in dialog.status_label.text().lower()
    assert str(TRIAL_DAYS - 3) in dialog.status_label.text()


def test_expired_trial_is_shown(qapp, tmp_path):
    dialog, _m, _t = _dialog(tmp_path, state=LicenseState(first_run=_iso(TRIAL_DAYS + 2)))
    assert "ended" in dialog.status_label.text().lower()


def test_active_licence_hides_the_key_field_and_offers_removal(qapp, tmp_path):
    dialog, _m, _t = _dialog(tmp_path, state=LicenseState(
        first_run=_iso(40), key="PHOTOFLOW-ABC-123456",
        activated_on=_iso(10), last_validated=_iso(1)))
    # isVisibleTo() rather than isVisible(): a child of a dialog that has never
    # been shown always reports isVisible() == False, which would make these
    # assertions meaningless offscreen.
    assert dialog.btn_deactivate.isVisibleTo(dialog)
    assert not dialog.key_input.isVisibleTo(dialog)
    # Only the tail of the key is shown, not the whole thing.
    assert "123456" in dialog.detail_label.text()
    assert "PHOTOFLOW-ABC-123456" not in dialog.detail_label.text()


def test_grace_state_is_shown_as_working_offline(qapp, tmp_path):
    dialog, _m, _t = _dialog(tmp_path, state=LicenseState(
        first_run=_iso(60), key="K-999999", activated_on=_iso(40),
        last_validated=_iso(RECHECK_DAYS + 2)))
    assert "offline" in dialog.status_label.text().lower()


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #
def test_successful_activation_updates_the_dialog(qapp, tmp_path):
    dialog, manager, _t = _dialog(tmp_path, result=ActivationResult(
        ok=True, customer="Studio Z"))
    dialog.key_input.setText("PHOTOFLOW-GOOD-KEY")
    dialog._on_activate()

    assert "activated" in dialog.activate_result.text().lower()
    assert manager.status().state == "active"
    assert dialog.key_input.text() == ""     # cleared after success


def test_rejected_key_shows_the_server_message(qapp, tmp_path):
    dialog, _m, _t = _dialog(tmp_path, result=ActivationResult(
        ok=False, message="Seat limit reached."))
    dialog.key_input.setText("PHOTOFLOW-USED-KEY")
    dialog._on_activate()
    assert "Seat limit reached." in dialog.activate_result.text()


def test_offline_activation_says_check_your_connection_not_bad_key(qapp, tmp_path):
    """A customer with a perfectly good key must not be told it's invalid."""
    dialog, _m, _t = _dialog(tmp_path, result=ActivationResult(
        ok=False, offline=True, message="unreachable"))
    dialog.key_input.setText("PHOTOFLOW-FINE-KEY")
    dialog._on_activate()

    text = dialog.activate_result.text().lower()
    assert "internet" in text or "connection" in text
    assert "probably fine" in text


def test_activation_failure_never_raises(qapp, tmp_path):
    class Exploding:
        def activate(self, key, machine):
            raise RuntimeError("boom")

        def validate(self, key, machine):
            raise RuntimeError("boom")

    manager = LicenseManager(backend=Exploding(), path=tmp_path / "license.json")
    dialog = LicenseDialog(manager, telemetry=Telemetry(consent=False))
    dialog.key_input.setText("ANY-KEY")
    dialog._on_activate()                     # must not propagate
    assert "went wrong" in dialog.activate_result.text().lower()


def test_deactivation_frees_the_seat(qapp, tmp_path):
    dialog, manager, _t = _dialog(tmp_path, state=LicenseState(
        first_run=_iso(40), key="K-123456", activated_on=_iso(5),
        last_validated=_iso(1)))
    dialog._on_deactivate()
    assert manager.state.key == ""
    assert "activate it elsewhere" in dialog.activate_result.text().lower()


# --------------------------------------------------------------------------- #
# Privacy / telemetry consent
# --------------------------------------------------------------------------- #
def test_consent_defaults_to_unchecked(qapp, tmp_path):
    dialog, _m, _t = _dialog(tmp_path)
    assert dialog.consent_check.isChecked() is False


def test_granting_consent_persists_and_enables_collection(qapp, tmp_path):
    dialog, manager, counters = _dialog(tmp_path)
    dialog.consent_check.setChecked(True)
    assert manager.telemetry_consent() is True
    assert counters.consent is True


def test_withdrawing_consent_deletes_what_was_collected(qapp, tmp_path):
    """Turning it off must not merely stop adding to the pile."""
    dialog, manager, counters = _dialog(tmp_path, consent=True)
    counters.record("album_built", 5)
    assert counters.counts

    dialog.consent_check.setChecked(False)
    assert manager.telemetry_consent() is False
    assert counters.counts == {}
    assert not (tmp_path / "counters.json").exists()


def test_show_data_reveals_the_actual_payload(qapp, tmp_path):
    dialog, _m, counters = _dialog(tmp_path, consent=True)
    counters.record("collage_built", 3)

    assert not dialog.data_preview.isVisibleTo(dialog)
    dialog._on_show_data()
    assert dialog.data_preview.isVisibleTo(dialog)
    text = dialog.data_preview.text()
    assert "collage_built: 3" in text
    assert "No file names" in text


def test_show_data_toggles_off_again(qapp, tmp_path):
    dialog, _m, _t = _dialog(tmp_path, consent=True)
    dialog._on_show_data()
    dialog._on_show_data()
    assert not dialog.data_preview.isVisibleTo(dialog)


# --------------------------------------------------------------------------- #
# The dialog must never trap the user
# --------------------------------------------------------------------------- #
def test_dialog_is_closable_even_with_an_expired_trial(qapp, tmp_path):
    """A photographer on a deadline must always be able to dismiss this."""
    dialog, _m, _t = _dialog(tmp_path, state=LicenseState(
        first_run=_iso(TRIAL_DAYS + 30)))
    dialog.accept()
    assert not dialog.isVisible()


def test_dialog_works_without_a_telemetry_object(qapp, tmp_path):
    manager = LicenseManager(path=tmp_path / "license.json")
    dialog = LicenseDialog(manager, telemetry=None)
    dialog._on_show_data()                    # must not raise
    assert "not configured" in dialog.data_preview.text().lower()
