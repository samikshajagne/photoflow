"""
Admin user-management endpoint tests.

The point of this file is the authorisation boundary. A CLIENT constructing the
HTTP request by hand must get the same 403 as one who never saw a button — there
is no UI in this test, which is precisely why it is worth writing.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.enums import UserRole, UserStatus
from app.models.user import User
from tests.conftest import requires_database

pytestmark = requires_database

USERS = "/api/v1/admin/users"
STRONG_PASSWORD = "a-perfectly-fine-password"


class TestAuthorization:
    """Every route, three ways: anonymous, CLIENT, ADMIN."""

    @pytest.fixture
    def routes(self):
        target = uuid.uuid4()
        return [
            ("post", USERS, {"email": "x@example.com", "password": STRONG_PASSWORD}),
            ("get", USERS, None),
            ("get", f"{USERS}/{target}", None),
            ("post", f"{USERS}/{target}/disable", None),
            ("post", f"{USERS}/{target}/enable", None),
        ]

    def test_anonymous_is_rejected_everywhere(self, client, routes):
        for method, path, body in routes:
            response = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
            assert response.status_code == 401, path

    def test_client_role_is_forbidden_everywhere(
        self, client, client_role_headers, routes
    ):
        headers, _ = client_role_headers
        for method, path, body in routes:
            call = getattr(client, method)
            response = call(path, json=body, headers=headers) if body else call(path, headers=headers)
            assert response.status_code == 403, path

    def test_a_forged_admin_claim_does_not_help(self, client, db, make_user):
        """
        The role is read from the database row the token's subject points at,
        never from the token itself.
        """
        from app.security.tokens import create_access_token

        user = make_user(role=UserRole.CLIENT)
        token = create_access_token(user_id=user.id, role="ADMIN")
        response = client.get(USERS, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403

    def test_disabled_admin_loses_access(self, client, db, make_user):
        from app.security.tokens import create_access_token

        admin = make_user(role=UserRole.ADMIN)
        token = create_access_token(user_id=admin.id, role="ADMIN")
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get(USERS, headers=headers).status_code == 200

        admin.status = UserStatus.DISABLED
        db.flush()
        assert client.get(USERS, headers=headers).status_code == 401

    def test_admin_is_allowed(self, admin_client):
        client, headers, _ = admin_client
        assert client.get(USERS, headers=headers).status_code == 200


class TestCreateUser:
    def test_creates_a_client_account(self, admin_client, db):
        client, headers, _ = admin_client
        response = client.post(
            USERS,
            json={
                "email": "newstudio@example.com",
                "name": "New Studio",
                "password": STRONG_PASSWORD,
            },
            headers=headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "newstudio@example.com"
        assert body["role"] == UserRole.CLIENT.value
        assert body["status"] == UserStatus.ACTIVE.value

    def test_password_is_hashed_not_stored(self, admin_client, db):
        client, headers, _ = admin_client
        client.post(
            USERS,
            json={"email": "hashed@example.com", "password": STRONG_PASSWORD},
            headers=headers,
        )
        user = db.execute(
            select(User).where(User.email == "hashed@example.com")
        ).scalar_one()
        assert user.password_hash.startswith("$argon2id$")
        assert STRONG_PASSWORD not in user.password_hash

    def test_response_never_contains_the_hash(self, admin_client):
        client, headers, _ = admin_client
        response = client.post(
            USERS,
            json={"email": "quiet@example.com", "password": STRONG_PASSWORD},
            headers=headers,
        )
        assert "password" not in response.text
        assert "$argon2" not in response.text

    def test_the_new_account_can_log_in(self, admin_client):
        client, headers, _ = admin_client
        client.post(
            USERS,
            json={"email": "loginme@example.com", "password": STRONG_PASSWORD},
            headers=headers,
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "loginme@example.com", "password": STRONG_PASSWORD},
        )
        assert login.status_code == 200

    def test_duplicate_email_is_409(self, admin_client):
        client, headers, _ = admin_client
        payload = {"email": "twice@example.com", "password": STRONG_PASSWORD}
        assert client.post(USERS, json=payload, headers=headers).status_code == 201
        assert client.post(USERS, json=payload, headers=headers).status_code == 409

    def test_weak_password_is_rejected(self, admin_client):
        client, headers, _ = admin_client
        response = client.post(
            USERS,
            json={"email": "weak@example.com", "password": "short"},
            headers=headers,
        )
        assert response.status_code == 422

    def test_invalid_email_is_rejected(self, admin_client):
        client, headers, _ = admin_client
        response = client.post(
            USERS,
            json={"email": "not-an-email", "password": STRONG_PASSWORD},
            headers=headers,
        )
        assert response.status_code == 422

    def test_creating_an_admin_is_audited_distinctly(self, admin_client, db):
        from app.models.audit import AuditLog
        from app.services.audit import AuditAction

        client, headers, _ = admin_client
        client.post(
            USERS,
            json={
                "email": "second-admin@example.com",
                "password": STRONG_PASSWORD,
                "role": "ADMIN",
            },
            headers=headers,
        )
        actions = db.execute(select(AuditLog.action)).scalars().all()
        assert AuditAction.ADMIN_CREATED in actions

    def test_creating_a_client_is_audited(self, admin_client, db):
        from app.models.audit import AuditLog
        from app.services.audit import AuditAction

        client, headers, _ = admin_client
        client.post(
            USERS,
            json={"email": "audited@example.com", "password": STRONG_PASSWORD},
            headers=headers,
        )
        actions = db.execute(select(AuditLog.action)).scalars().all()
        assert AuditAction.CLIENT_CREATED in actions

    def test_audit_metadata_holds_no_password(self, admin_client, db):
        from app.models.audit import AuditLog

        client, headers, _ = admin_client
        client.post(
            USERS,
            json={"email": "scrubbed@example.com", "password": STRONG_PASSWORD},
            headers=headers,
        )
        for entry in db.execute(select(AuditLog)).scalars().all():
            assert STRONG_PASSWORD not in str(entry.metadata_json or {})


class TestListAndGet:
    def test_list_returns_accounts(self, admin_client, make_user):
        client, headers, _ = admin_client
        make_user()
        make_user()
        body = client.get(USERS, headers=headers).json()
        assert body["total"] >= 3  # two plus the admin
        assert len(body["items"]) == body["total"]

    def test_list_never_leaks_hashes(self, admin_client, make_user):
        client, headers, _ = admin_client
        make_user()
        assert "$argon2" not in client.get(USERS, headers=headers).text

    def test_list_can_filter_by_role(self, admin_client, make_user):
        client, headers, _ = admin_client
        make_user(role=UserRole.CLIENT)
        body = client.get(f"{USERS}?role=ADMIN", headers=headers).json()
        assert all(item["role"] == "ADMIN" for item in body["items"])

    def test_list_pagination_is_bounded(self, admin_client):
        client, headers, _ = admin_client
        assert client.get(f"{USERS}?limit=100000", headers=headers).status_code == 422

    def test_get_returns_one_account(self, admin_client, make_user):
        client, headers, _ = admin_client
        user = make_user()
        body = client.get(f"{USERS}/{user.id}", headers=headers).json()
        assert body["id"] == str(user.id)

    def test_get_unknown_is_404(self, admin_client):
        client, headers, _ = admin_client
        assert client.get(f"{USERS}/{uuid.uuid4()}", headers=headers).status_code == 404


class TestDisableEnable:
    def test_disable_sets_status(self, admin_client, make_user):
        client, headers, _ = admin_client
        user = make_user()
        body = client.post(f"{USERS}/{user.id}/disable", headers=headers).json()
        assert body["status"] == UserStatus.DISABLED.value

    def test_disabled_user_cannot_log_in(self, admin_client, make_user):
        client, headers, _ = admin_client
        user = make_user(email="tobedisabled@example.com", password=STRONG_PASSWORD)
        client.post(f"{USERS}/{user.id}/disable", headers=headers)
        login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": STRONG_PASSWORD},
        )
        assert login.status_code == 401

    def test_disable_revokes_live_sessions(self, admin_client, make_user):
        """
        Status alone would leave live refresh tokens able to mint access tokens.
        Both halves have to happen for the account to actually be out.
        """
        client, headers, _ = admin_client
        user = make_user(email="sessions@example.com", password=STRONG_PASSWORD)
        session = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": STRONG_PASSWORD},
        ).json()

        client.post(f"{USERS}/{user.id}/disable", headers=headers)

        assert client.post(
            "/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]}
        ).status_code == 401
        assert client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {session['access_token']}"},
        ).status_code == 401

    def test_enable_restores_login(self, admin_client, make_user):
        client, headers, _ = admin_client
        user = make_user(email="restore@example.com", password=STRONG_PASSWORD)
        client.post(f"{USERS}/{user.id}/disable", headers=headers)
        client.post(f"{USERS}/{user.id}/enable", headers=headers)
        login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": STRONG_PASSWORD},
        )
        assert login.status_code == 200

    def test_enable_does_not_resurrect_old_sessions(self, admin_client, make_user):
        """A session that survived a suspension would defeat the suspension."""
        client, headers, _ = admin_client
        user = make_user(email="zombie@example.com", password=STRONG_PASSWORD)
        session = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": STRONG_PASSWORD},
        ).json()
        client.post(f"{USERS}/{user.id}/disable", headers=headers)
        client.post(f"{USERS}/{user.id}/enable", headers=headers)
        assert client.post(
            "/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]}
        ).status_code == 401

    def test_admin_cannot_disable_themselves(self, admin_client):
        """With one administrator this would lock everyone out permanently."""
        client, headers, admin = admin_client
        response = client.post(f"{USERS}/{admin.id}/disable", headers=headers)
        assert response.status_code == 400

    def test_disable_and_enable_are_audited(self, admin_client, db, make_user):
        from app.models.audit import AuditLog
        from app.services.audit import AuditAction

        client, headers, _ = admin_client
        user = make_user()
        client.post(f"{USERS}/{user.id}/disable", headers=headers)
        client.post(f"{USERS}/{user.id}/enable", headers=headers)

        actions = db.execute(select(AuditLog.action)).scalars().all()
        assert AuditAction.USER_DISABLED in actions
        assert AuditAction.USER_ENABLED in actions

    def test_disable_unknown_is_404(self, admin_client):
        client, headers, _ = admin_client
        assert client.post(
            f"{USERS}/{uuid.uuid4()}/disable", headers=headers
        ).status_code == 404
