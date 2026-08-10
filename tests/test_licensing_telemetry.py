"""
Tests for core.licensing and core.telemetry, plus utils.paths.

The licensing tests deliberately concentrate on the *failure* paths -- offline
servers, corrupt state files, read-only disks, tampering -- because the whole
design goal is "never lock out a paying customer", and that is only true if the
degraded paths behave.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from core import telemetry as telemetry_module
from core.licensing import _today as _licensing_today  # noqa: E402
from core.licensing import (
    GRACE_DAYS,
    RECHECK_DAYS,
    STATE_ACTIVE,
    STATE_EXPIRED,
    STATE_GRACE,
    STATE_TRIAL,
    STATE_TRIAL_EXPIRED,
    TRIAL_DAYS,
    ActivationResult,
    LicenseManager,
    LicenseState,
    OfflineBackend,
    load_state,
    machine_fingerprint,
    save_state,
)
from core.telemetry import ALLOWED_EVENTS, Telemetry, TelemetryError
from utils.paths import (
    bundle_root,
    is_frozen,
    resource_path,
    user_cache_dir,
    user_data_dir,
    user_log_dir,
    writable_model_dir,
)


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
    """Backend whose answers the test controls."""

    def __init__(self, result: ActivationResult):
        self.result = result
        self.activate_calls = 0
        self.validate_calls = 0

    def activate(self, key, machine):
        self.activate_calls += 1
        return self.result

    def validate(self, key, machine):
        self.validate_calls += 1
        return self.result


def _manager(tmp_path, backend=None, state: LicenseState | None = None) -> LicenseManager:
    path = tmp_path / "license.json"
    if state is not None:
        save_state(state, path)
    return LicenseManager(backend=backend or OfflineBackend(), path=path)


# --------------------------------------------------------------------------- #
# utils.paths
# --------------------------------------------------------------------------- #
def test_paths_are_absolute_and_exist():
    for getter in (user_data_dir, user_cache_dir, user_log_dir, writable_model_dir):
        path = getter()
        assert path.is_absolute()
        assert path.is_dir(), f"{getter.__name__} did not create its directory"


def test_resource_path_resolves_under_the_bundle_root():
    assert resource_path("data", "models").is_relative_to(bundle_root())


def test_not_frozen_when_running_tests():
    assert is_frozen() is False


def test_user_data_dir_is_not_inside_the_application_directory():
    """The whole point: writes must not land next to the installed app, which is
    read-only for normal users under Program Files."""
    assert not user_data_dir().is_relative_to(bundle_root())


# --------------------------------------------------------------------------- #
# Machine fingerprint
# --------------------------------------------------------------------------- #
def test_fingerprint_is_stable_and_opaque():
    first, second = machine_fingerprint(), machine_fingerprint()
    assert first == second
    assert len(first) == 32
    assert first.isalnum()


def test_fingerprint_does_not_leak_the_hostname():
    import platform

    node = platform.node()
    if node:
        assert node.lower() not in machine_fingerprint().lower()


# --------------------------------------------------------------------------- #
# State file
# --------------------------------------------------------------------------- #
def test_state_round_trips(tmp_path):
    path = tmp_path / "license.json"
    original = LicenseState(key="ABC-123", activated_on="2026-01-01", seats=2,
                            customer="Studio X", telemetry_consent=True)
    save_state(original, path)
    assert load_state(path) == original


def test_missing_state_file_gives_a_fresh_state(tmp_path):
    assert load_state(tmp_path / "nope.json") == LicenseState()


def test_tampered_state_is_rejected(tmp_path):
    """Hand-editing the expiry date must not work."""
    path = tmp_path / "license.json"
    save_state(LicenseState(key="K", activated_on="2026-01-01"), path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["state"]["expires_on"] = "2099-01-01"      # signature no longer matches
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert load_state(path) == LicenseState()      # falls back, doesn't trust it


def test_corrupt_state_file_does_not_raise(tmp_path):
    """A corrupt file must never stop the app from starting."""
    path = tmp_path / "license.json"
    path.write_text("{ not json at all", encoding="utf-8")
    assert load_state(path) == LicenseState()


def test_save_leaves_no_temp_file(tmp_path):
    path = tmp_path / "license.json"
    save_state(LicenseState(key="K"), path)
    assert list(tmp_path.glob("*.tmp")) == []


# --------------------------------------------------------------------------- #
# Trial
# --------------------------------------------------------------------------- #
def test_first_run_starts_the_trial(tmp_path):
    manager = _manager(tmp_path)
    status = manager.status()
    assert status.state == STATE_TRIAL
    assert status.days_left == TRIAL_DAYS
    assert status.usable


def test_trial_counts_down(tmp_path):
    manager = _manager(tmp_path, state=LicenseState(first_run=_iso(5)))
    assert manager.status().days_left == TRIAL_DAYS - 5


def test_trial_expires(tmp_path):
    manager = _manager(tmp_path, state=LicenseState(first_run=_iso(TRIAL_DAYS + 1)))
    status = manager.status()
    assert status.state == STATE_TRIAL_EXPIRED
    assert not status.usable
    assert status.should_nag


def test_trial_nags_near_the_end(tmp_path):
    manager = _manager(tmp_path, state=LicenseState(first_run=_iso(TRIAL_DAYS - 2)))
    status = manager.status()
    assert status.state == STATE_TRIAL
    assert status.should_nag          # 2 days left
    assert status.usable              # ...but still works


def test_read_only_disk_does_not_break_startup(tmp_path, monkeypatch):
    """If the state file can't be written, the app must still run."""
    from core import licensing

    monkeypatch.setattr(
        licensing, "save_state",
        lambda *a, **k: (_ for _ in ()).throw(licensing.LicenseError("read-only")),
    )
    manager = _manager(tmp_path)
    assert manager.status().state == STATE_TRIAL   # no exception


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #
def test_activation_stores_the_licence(tmp_path):
    manager = _manager(tmp_path, backend=StubBackend(
        ActivationResult(ok=True, seats=3, customer="Studio Y")))
    result = manager.activate("PHOTOFLOW-TEST-KEY")
    assert result.ok

    status = manager.status()
    assert status.state == STATE_ACTIVE
    assert status.customer == "Studio Y"
    assert status.usable

    # Persisted, so the next launch works offline.
    assert load_state(tmp_path / "license.json").key == "PHOTOFLOW-TEST-KEY"


def test_empty_key_is_rejected_without_calling_the_server(tmp_path):
    backend = StubBackend(ActivationResult(ok=True))
    manager = _manager(tmp_path, backend=backend)
    assert manager.activate("   ").ok is False
    assert backend.activate_calls == 0


def test_refused_key_is_reported_and_not_stored(tmp_path):
    manager = _manager(tmp_path, backend=StubBackend(
        ActivationResult(ok=False, message="Seat limit reached.")))
    result = manager.activate("SOME-KEY")
    assert not result.ok
    assert "Seat limit" in result.message
    assert manager.status().state == STATE_TRIAL   # unchanged


def test_offline_activation_is_distinguishable_from_a_bad_key(tmp_path):
    """The UI must be able to say "check your connection" rather than
    "your key is wrong"."""
    manager = _manager(tmp_path, backend=StubBackend(
        ActivationResult(ok=False, offline=True, message="unreachable")))
    result = manager.activate("SOME-KEY")
    assert not result.ok
    assert result.offline is True


def test_deactivate_clears_the_licence(tmp_path):
    manager = _manager(tmp_path, backend=StubBackend(ActivationResult(ok=True)))
    manager.activate("KEY-TO-MOVE")
    manager.deactivate()
    assert manager.status().state in (STATE_TRIAL, STATE_TRIAL_EXPIRED)
    assert load_state(tmp_path / "license.json").key == ""


# --------------------------------------------------------------------------- #
# Grace period — the "don't break a studio mid-wedding" rules
# --------------------------------------------------------------------------- #
def test_recently_validated_licence_is_active(tmp_path):
    manager = _manager(tmp_path, state=LicenseState(
        first_run=_iso(60), key="K", activated_on=_iso(30), last_validated=_iso(1)))
    assert manager.status().state == STATE_ACTIVE


def test_licence_enters_grace_when_it_cannot_check_in(tmp_path):
    manager = _manager(tmp_path, state=LicenseState(
        first_run=_iso(60), key="K", activated_on=_iso(40),
        last_validated=_iso(RECHECK_DAYS + 2)))
    status = manager.status()
    assert status.state == STATE_GRACE
    assert status.usable, "a paying customer must keep working while offline"


def test_licence_expires_only_after_the_full_grace_period(tmp_path):
    manager = _manager(tmp_path, state=LicenseState(
        first_run=_iso(200), key="K", activated_on=_iso(100),
        last_validated=_iso(GRACE_DAYS + 1)))
    status = manager.status()
    assert status.state == STATE_EXPIRED
    assert not status.usable


def test_explicit_expiry_date_is_honoured(tmp_path):
    manager = _manager(tmp_path, state=LicenseState(
        first_run=_iso(60), key="K", activated_on=_iso(10),
        last_validated=_iso(1), expires_on=_iso(1)))
    assert manager.status().state == STATE_EXPIRED


def test_future_expiry_date_is_fine(tmp_path):
    future = (date.today() + timedelta(days=200)).isoformat()
    manager = _manager(tmp_path, state=LicenseState(
        first_run=_iso(60), key="K", activated_on=_iso(10),
        last_validated=_iso(1), expires_on=future))
    assert manager.status().state == STATE_ACTIVE


# --------------------------------------------------------------------------- #
# Revalidation
# --------------------------------------------------------------------------- #
def test_revalidation_is_skipped_when_not_due(tmp_path):
    backend = StubBackend(ActivationResult(ok=True))
    manager = _manager(tmp_path, backend=backend, state=LicenseState(
        first_run=_iso(60), key="K", activated_on=_iso(10), last_validated=_iso(1)))
    assert manager.revalidate() is None
    assert backend.validate_calls == 0


def test_revalidation_runs_when_due_and_updates_the_date(tmp_path):
    backend = StubBackend(ActivationResult(ok=True))
    manager = _manager(tmp_path, backend=backend, state=LicenseState(
        first_run=_iso(60), key="K", activated_on=_iso(40),
        last_validated=_iso(RECHECK_DAYS + 1)))
    result = manager.revalidate()
    assert result is not None and result.ok
    assert backend.validate_calls == 1
    # The module's clock, not the local one — see _iso() above.
    assert manager.state.last_validated == _licensing_today().isoformat()


def test_offline_revalidation_does_not_deactivate(tmp_path):
    """A server outage on our side must never lock out a customer."""
    manager = _manager(tmp_path, backend=StubBackend(
        ActivationResult(ok=False, offline=True)), state=LicenseState(
        first_run=_iso(60), key="K", activated_on=_iso(40),
        last_validated=_iso(RECHECK_DAYS + 1)))
    manager.revalidate()
    assert manager.state.key == "K"
    assert manager.status().usable


def test_refused_revalidation_still_does_not_deactivate_immediately(tmp_path):
    manager = _manager(tmp_path, backend=StubBackend(
        ActivationResult(ok=False, message="revoked")), state=LicenseState(
        first_run=_iso(60), key="K", activated_on=_iso(40),
        last_validated=_iso(RECHECK_DAYS + 1)))
    manager.revalidate()
    assert manager.state.key == "K"   # status()/grace decides, not revalidate()


def test_revalidation_without_a_key_is_a_noop(tmp_path):
    backend = StubBackend(ActivationResult(ok=True))
    assert _manager(tmp_path, backend=backend).revalidate() is None
    assert backend.validate_calls == 0


# --------------------------------------------------------------------------- #
# Telemetry consent
# --------------------------------------------------------------------------- #
def test_consent_defaults_to_unasked(tmp_path):
    assert _manager(tmp_path).telemetry_consent() is None


def test_consent_persists(tmp_path):
    path = tmp_path / "license.json"
    manager = LicenseManager(path=path)
    manager.set_telemetry_consent(True)
    assert LicenseManager(path=path).telemetry_consent() is True

    manager.set_telemetry_consent(False)
    assert LicenseManager(path=path).telemetry_consent() is False


# --------------------------------------------------------------------------- #
# Telemetry
# --------------------------------------------------------------------------- #
def test_nothing_is_recorded_without_consent(tmp_path):
    t = Telemetry(consent=False, path=tmp_path / "counters.json")
    t.record("album_built")
    assert t.counts == {}
    assert not (tmp_path / "counters.json").exists(), "must not even touch the disk"


def test_events_are_counted_with_consent(tmp_path):
    t = Telemetry(consent=True, path=tmp_path / "counters.json")
    t.record("album_built")
    t.record("album_built", 3)
    assert t.counts["album_built"] == 4


def test_counts_persist_across_instances(tmp_path):
    path = tmp_path / "counters.json"
    Telemetry(consent=True, path=path).record("collage_exported", 2)
    assert Telemetry(consent=True, path=path).counts["collage_exported"] == 2


def test_unknown_events_are_rejected_loudly(tmp_path):
    """The closed vocabulary is what keeps telemetry to counts only."""
    t = Telemetry(consent=True, path=tmp_path / "c.json")
    with pytest.raises(TelemetryError, match="Unknown telemetry event"):
        t.record("client_name_entered")


def test_no_event_name_looks_like_user_content():
    """Guards the design rule: nothing here should be able to carry a path,
    a filename or anything the user typed."""
    banned = ("path", "file", "name", "folder", "title", "caption", "client", "email")
    for event in ALLOWED_EVENTS:
        assert not any(word in event for word in banned), event


def test_payload_contains_only_expected_fields(tmp_path):
    t = Telemetry(consent=True, path=tmp_path / "c.json")
    t.record("mode_collage")
    data = t.payload().to_dict()
    assert set(data) == {
        "machine", "app_version", "os_name", "os_version", "counts", "generated_at"
    }
    assert data["counts"] == {"mode_collage": 1}


def test_describe_is_human_readable_and_reassuring(tmp_path):
    t = Telemetry(consent=True, path=tmp_path / "c.json")
    t.record("album_built", 2)
    text = t.describe()
    assert "album_built: 2" in text
    assert "No file names" in text


def test_clear_forgets_everything(tmp_path):
    path = tmp_path / "c.json"
    t = Telemetry(consent=True, path=path)
    t.record("album_built")
    t.clear()
    assert t.counts == {}
    assert not path.exists()


def test_flush_does_nothing_without_an_endpoint(tmp_path):
    t = Telemetry(consent=True, path=tmp_path / "c.json")
    t.record("album_built")
    assert t.flush() is False


def test_flush_does_nothing_without_consent(tmp_path):
    t = Telemetry(consent=False, endpoint="https://example.invalid/u",
                  path=tmp_path / "c.json")
    assert t.flush() is False


def test_module_level_default_has_no_consent():
    telemetry_module._instance = None       # reset the shared instance
    assert telemetry_module.telemetry().consent is False


def test_configure_sets_the_shared_instance(tmp_path):
    telemetry_module._instance = None
    configured = telemetry_module.configure(consent=True)
    assert telemetry_module.telemetry() is configured
    assert configured.consent is True
    telemetry_module._instance = None
