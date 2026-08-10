"""
Application-level tests: startup, health, and the authorisation boundary.

The authorisation tests are the load-bearing ones. Phase 2 ships no protected
endpoints yet, so they mount a throwaway router onto a test app -- which checks
the dependencies themselves rather than an endpoint that might later be
deleted. When Phase 3 adds real endpoints, these keep guarding the mechanism
underneath them.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user, require_admin
from app.database.session import get_db
from app.models.enums import UserRole, UserStatus
from app.security.tokens import create_access_token
from tests.conftest import requires_database

pytestmark = requires_database


def _protected_app(db) -> FastAPI:
    """A minimal app exposing one authenticated and one admin-only route."""
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    router = APIRouter()

    @router.get("/_test/me")
    def me(user=Depends(get_current_user)):  # noqa: B008 - FastAPI's DI idiom
        return {"id": str(user.id), "role": user.role.value}

    @router.get("/_test/admin")
    def admin_only(user=Depends(require_admin)):  # noqa: B008 - FastAPI's DI idiom
        return {"ok": True}

    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return app


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestApplicationStartup:
    def test_app_starts(self, client):
        """The lifespan runs without raising -- the smoke test for wiring."""
        assert client.app.state.settings is not None

    def test_openapi_is_available_outside_production(self, client):
        assert client.get("/openapi.json").status_code == 200

    def test_docs_are_disabled_in_production(self):
        """An unauthenticated map of the API surface is a gift to a scanner."""
        from app.config import Environment, Settings
        from app.main import create_app

        production = Settings(
            environment=Environment.PRODUCTION,
            database_url="postgresql://u:p@db.neon.tech/photoflow",
            api_base_url="https://api.photoflow.example",
            jwt_secret="y" * 48,
            cors_origins=["https://admin.photoflow.example"],
            trusted_hosts=["api.photoflow.example"],
            allow_single_instance_rate_limit=True,
            _env_file=None,
        )
        app = create_app(production)
        assert app.docs_url is None
        assert app.openapi_url is None


class TestHealth:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_health_does_not_leak_infrastructure(self, client):
        """No host, no database, no driver, no path -- anywhere in the body."""
        body = client.get("/health").text.lower()
        for leak in (
            "postgres",
            "psycopg",
            "neon",
            "password",
            "@",
            "5432",
            "/home/",
            "traceback",
        ):
            assert leak not in body

    def test_readiness_reports_the_database(self, client):
        response = client.get("/health/ready")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["database"] == "ok"

    def test_readiness_says_nothing_about_why(self, client):
        """A verdict, never a driver error string with a hostname in it."""
        assert set(client.get("/health/ready").json()) == {"status", "database"}

    def test_versioned_health_endpoint(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_request_id_is_returned(self, client):
        assert client.get("/health").headers.get("X-Request-ID")

    def test_security_headers_are_set(self, client):
        headers = client.get("/health").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"

    def test_unknown_route_is_404_without_a_stack_trace(self, client):
        response = client.get("/api/v1/nope")
        assert response.status_code == 404
        assert "traceback" not in response.text.lower()


class TestAuthorizationBoundary:
    def test_missing_token_is_rejected(self, db):
        with TestClient(_protected_app(db)) as client:
            response = client.get("/_test/me")
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    def test_garbage_token_is_rejected(self, db):
        with TestClient(_protected_app(db)) as client:
            response = client.get("/_test/me", headers=_auth("not-a-jwt"))
        assert response.status_code == 401

    def test_token_for_a_nonexistent_user_is_rejected(self, db):
        token = create_access_token(user_id=uuid.uuid4(), role="ADMIN")
        with TestClient(_protected_app(db)) as client:
            response = client.get("/_test/me", headers=_auth(token))
        assert response.status_code == 401

    def test_valid_token_is_accepted(self, db, make_user):
        user = make_user()
        token = create_access_token(user_id=user.id, role=user.role.value)
        with TestClient(_protected_app(db)) as client:
            response = client.get("/_test/me", headers=_auth(token))
        assert response.status_code == 200
        assert response.json()["id"] == str(user.id)

    def test_disabled_user_is_rejected_despite_a_valid_token(self, db, make_user):
        """
        The reason the user is re-read from the database on every request: a
        token minted before an account was disabled must stop working now, not
        in thirty minutes.
        """
        user = make_user(status=UserStatus.DISABLED)
        token = create_access_token(user_id=user.id, role=user.role.value)
        with TestClient(_protected_app(db)) as client:
            response = client.get("/_test/me", headers=_auth(token))
        assert response.status_code == 401

    def test_client_cannot_reach_an_admin_route(self, db, make_user):
        user = make_user(role=UserRole.CLIENT)
        token = create_access_token(user_id=user.id, role=user.role.value)
        with TestClient(_protected_app(db)) as client:
            response = client.get("/_test/admin", headers=_auth(token))
        assert response.status_code == 403

    def test_role_comes_from_the_database_not_the_token(self, db, make_user):
        """A forged 'role: ADMIN' claim on an otherwise valid token buys nothing."""
        user = make_user(role=UserRole.CLIENT)
        token = create_access_token(user_id=user.id, role="ADMIN")
        with TestClient(_protected_app(db)) as client:
            response = client.get("/_test/admin", headers=_auth(token))
        assert response.status_code == 403

    def test_admin_reaches_the_admin_route(self, db, make_user):
        user = make_user(role=UserRole.ADMIN)
        token = create_access_token(user_id=user.id, role=user.role.value)
        with TestClient(_protected_app(db)) as client:
            response = client.get("/_test/admin", headers=_auth(token))
        assert response.status_code == 200

    def test_failures_are_indistinguishable(self, db, make_user):
        """
        No-such-user, wrong-token and disabled-account must all look identical.
        Anything else is an account-enumeration oracle.
        """
        disabled = make_user(status=UserStatus.DISABLED)
        bodies = set()
        with TestClient(_protected_app(db)) as client:
            for token in (
                create_access_token(user_id=uuid.uuid4(), role="CLIENT"),
                create_access_token(user_id=disabled.id, role="CLIENT"),
            ):
                response = client.get("/_test/me", headers=_auth(token))
                bodies.add(response.json()["detail"])
        assert len(bodies) == 1
