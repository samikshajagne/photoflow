"""
Tests for core.licensing.HttpBackend: the production licence API client.

None of this touches the network. ``urllib.request.urlopen`` is replaced with
a fake that inspects the outgoing request and returns a canned response, so
the whole suite runs offline and deterministically -- exactly like the rest
of the licensing tests in ``test_licensing_telemetry.py``.

Covers:
* the Authorization header is sent, built from a token provider
* the token provider (standing in for AuthManager.ensure_access_token) is
  re-invoked per request, so a refreshed token is always used
* activation/validation/deactivation each carry the right payload
* no signed-in session is handled gracefully, without attempting a request
* a reachable-but-refused request (bad key, wrong owner, seat limit) is
  reported distinctly from an unreachable server
* LicenseManager.deactivate() calls the backend before clearing local state,
  and still clears local state if the backend can't be reached
* the pre-existing OfflineBackend path is untouched by any of the above
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from core.licensing import (
    ActivationResult,
    HttpBackend,
    LicenseManager,
    LicenseState,
    OfflineBackend,
    save_state,
)

try:
    import keyring  # noqa: F401

    from core.auth import AuthManager

    _AUTH_AVAILABLE = True
except ImportError:  # pragma: no cover - keyring backend unavailable in this environment
    AuthManager = None  # type: ignore[assignment]
    _AUTH_AVAILABLE = False


LICENSES_BASE_URL = "https://example.test/api/v1/licenses"


class _FakeResponse:
    """A minimal stand-in for the context manager urlopen() returns."""

    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _http_error(code: int, body: dict) -> urllib.error.HTTPError:
    """Build an HTTPError carrying a JSON body, like the backend's error responses."""
    return urllib.error.HTTPError(
        url=f"{LICENSES_BASE_URL}/activate",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(json.dumps(body).encode("utf-8")),
    )


def _install_fake_urlopen(monkeypatch, handler):
    """
    ``handler(request)`` decides the fake server's answer:

    * returning a ``dict`` yields a 200 response with that JSON body
    * returning an ``Exception`` instance (e.g. from ``_http_error``, or a
      plain ``urllib.error.URLError``) makes ``urlopen`` raise it
    """

    def fake_urlopen(request, timeout=None):  # noqa: ARG001 - signature must match urlopen
        result = handler(request)
        if isinstance(result, BaseException):
            raise result
        return _FakeResponse(result)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


# --------------------------------------------------------------------------- #
# Authorization header / token provider
# --------------------------------------------------------------------------- #
def test_activate_sends_the_bearer_token_and_the_right_payload(monkeypatch):
    captured = {}

    def handler(request):
        captured["auth"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return {
            "ok": True, "message": "Activated.", "seats": 1,
            "customer": "Test Studio", "license_id": "lic-1",
        }

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: "test-access-token")
    result = backend.activate("PF-FAKE-TEST-KEY", "machine-123")

    assert result.ok
    assert result.license_id == "lic-1"
    assert captured["auth"] == "Bearer test-access-token"
    assert captured["payload"] == {
        "key": "PF-FAKE-TEST-KEY", "machine": "machine-123",
        "product": "photoflow", "version": captured["payload"]["version"],
    }


def test_token_provider_is_re_invoked_for_every_request(monkeypatch):
    """
    Simulates AuthManager.ensure_access_token() transparently refreshing an
    expired token: each call to the provider may return a different value,
    and every request must use whatever it returns *at that moment*.
    """
    tokens = iter(["token-for-activate", "token-for-validate"])
    seen = []

    def handler(request):
        seen.append(request.get_header("Authorization"))
        return {"ok": True, "message": "ok"}

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: next(tokens))
    backend.activate("KEY", "machine")
    backend.validate("KEY", "machine")

    assert seen == ["Bearer token-for-activate", "Bearer token-for-validate"]


def test_no_authenticated_session_is_handled_gracefully_without_a_network_call(monkeypatch):
    """
    A token provider standing in for ensure_access_token() returning None
    (nobody logged in, and refresh failed) must not crash, must not attempt
    the request, and must not be reported as 'offline' -- there's nothing
    wrong with the server or the licence.
    """

    def handler(request):  # pragma: no cover - must never be reached
        raise AssertionError("must not contact the server without a token")

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: None)
    result = backend.activate("PF-FAKE-TEST-KEY", "machine-123")

    assert result.ok is False
    assert result.offline is False
    assert "sign" in result.message.lower() or "log in" in result.message.lower()


def test_backend_with_no_token_provider_configured_sends_no_header(monkeypatch):
    """A token_provider is optional: omitting it entirely (rather than one
    that returns None) just means no Authorization header is sent."""
    captured = {}

    def handler(request):
        captured["auth"] = request.get_header("Authorization")
        return {"ok": True, "message": "ok"}

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(base_url=LICENSES_BASE_URL)
    result = backend.activate("KEY", "machine")

    assert result.ok
    assert captured["auth"] is None


# --------------------------------------------------------------------------- #
# Distinguishing offline / reachable-but-refused / accepted
# --------------------------------------------------------------------------- #
def test_rejected_key_is_reported_distinctly_from_offline(monkeypatch):
    """A reachable server that refuses the key (403/409/...) must not be
    folded into 'offline' -- the UI shows very different wording for each."""

    def handler(request):
        return _http_error(403, {"detail": "This license is not available for this account."})

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: "token")
    result = backend.activate("SOMEONE-ELSES-FAKE-KEY", "machine")

    assert result.ok is False
    assert result.offline is False
    assert "not available for this account" in result.message


def test_unreachable_server_is_reported_as_offline(monkeypatch):
    def handler(request):
        return urllib.error.URLError("Name or service not known")

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: "token")
    result = backend.validate("PF-FAKE-KEY", "machine")

    assert result.ok is False
    assert result.offline is True


# --------------------------------------------------------------------------- #
# Deactivation
# --------------------------------------------------------------------------- #
def test_backend_deactivate_posts_license_id_and_machine_to_the_right_url(monkeypatch):
    captured = {}

    def handler(request):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["url"] = request.full_url
        return {"ok": True, "message": "Deactivated."}

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: "token")
    result = backend.deactivate("lic-42", "machine-9")

    assert result.ok
    assert captured["payload"] == {"license_id": "lic-42", "machine": "machine-9"}
    assert captured["url"] == f"{LICENSES_BASE_URL}/deactivate"


def test_manager_deactivate_calls_the_backend_before_clearing_local_state(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.data.decode("utf-8")))
        return {"ok": True, "message": "Deactivated."}

    _install_fake_urlopen(monkeypatch, handler)

    path = tmp_path / "license.json"
    save_state(
        LicenseState(
            first_run="2026-01-01", key="PF-FAKE-KEY", activated_on="2026-01-01",
            last_validated="2026-01-01", license_id="lic-abc",
        ),
        path,
    )
    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: "token")
    manager = LicenseManager(backend=backend, path=path)

    manager.deactivate()

    assert len(calls) == 1
    assert calls[0]["license_id"] == "lic-abc"
    assert manager.state.key == ""
    assert manager.state.license_id == ""
    # Persisted, not just in-memory.
    from core.licensing import load_state
    assert load_state(path).key == ""


def test_manager_deactivate_clears_local_state_even_if_the_server_is_unreachable(monkeypatch, tmp_path):
    """Preserves the pre-existing 'never lock out / never break the UI'
    guarantee: a network error during deactivation must not stop the seat
    from being freed locally."""

    def handler(request):
        return urllib.error.URLError("offline")

    _install_fake_urlopen(monkeypatch, handler)

    path = tmp_path / "license.json"
    save_state(
        LicenseState(
            first_run="2026-01-01", key="PF-FAKE-KEY", activated_on="2026-01-01",
            last_validated="2026-01-01", license_id="lic-abc",
        ),
        path,
    )
    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: "token")
    manager = LicenseManager(backend=backend, path=path)

    manager.deactivate()  # must not raise

    assert manager.state.key == ""


def test_offline_backend_deactivate_is_a_local_only_success(tmp_path):
    """The pre-existing offline path: no server, nothing to call, deactivation
    still succeeds locally."""
    manager = LicenseManager(backend=OfflineBackend(), path=tmp_path / "license.json")
    manager.activate("PF-FAKE-OFFLINE-KEY")
    manager.deactivate()  # must not raise even though OfflineBackend has no server
    assert manager.state.key == ""


# --------------------------------------------------------------------------- #
# Manager-level activation / validation through an authenticated backend
# --------------------------------------------------------------------------- #
def test_manager_activation_with_an_authenticated_backend_stores_the_license_id(monkeypatch, tmp_path):
    def handler(request):
        assert request.get_header("Authorization") == "Bearer session-token"
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["key"] == "PF-FAKE-ACTIVATE-KEY"
        return {
            "ok": True, "message": "Activated.", "seats": 2, "customer": "Studio A",
            "expires_on": "", "license_id": "lic-77",
        }

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: "session-token")
    manager = LicenseManager(backend=backend, path=tmp_path / "license.json")

    result = manager.activate("PF-FAKE-ACTIVATE-KEY")

    assert result.ok
    assert manager.state.license_id == "lic-77"
    assert manager.status().state == "active"


def test_manager_revalidation_with_an_authenticated_backend(monkeypatch, tmp_path):
    def handler(request):
        assert request.get_header("Authorization") == "Bearer session-token"
        return {"ok": True, "message": "License active.", "seats": 1, "customer": "Studio B"}

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(base_url=LICENSES_BASE_URL, token_provider=lambda: "session-token")
    manager = LicenseManager(backend=backend, path=tmp_path / "license.json")
    manager.state.key = "PF-FAKE-VALIDATE-KEY"  # simulate an already-activated licence

    result = manager.revalidate(force=True)

    assert result is not None and result.ok


@pytest.mark.skipif(not _AUTH_AVAILABLE, reason="keyring backend unavailable in this environment")
def test_auth_manager_ensure_access_token_works_directly_as_the_token_provider(monkeypatch):
    """
    The intended production wiring:
    ``HttpBackend(token_provider=auth_manager.ensure_access_token)``.

    No network call to the auth endpoints happens here: ensure_access_token()
    short-circuits because an in-memory access token is already present, so
    this only proves the plumbing -- AuthManager's own login/refresh logic is
    exercised elsewhere and is not duplicated by this test.
    """
    monkeypatch.setattr("keyring.get_password", lambda *a, **k: None)
    auth_manager = AuthManager()
    auth_manager._access_token = "already-signed-in-token"  # simulate a prior login

    captured = {}

    def handler(request):
        captured["auth"] = request.get_header("Authorization")
        return {"ok": True, "message": "ok"}

    _install_fake_urlopen(monkeypatch, handler)

    backend = HttpBackend(
        base_url=f"{auth_manager.base_url}/licenses",
        token_provider=auth_manager.ensure_access_token,
    )
    result = backend.activate("KEY", "machine")

    assert result.ok
    assert captured["auth"] == "Bearer already-signed-in-token"
