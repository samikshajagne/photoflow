"""
Authentication endpoint tests.

The negative cases outnumber the positive ones, which is the correct ratio for
an authentication surface: there is one way to log in and a great many ways
someone might try not to.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import select

from app.models.enums import UserRole, UserStatus
from app.models.token import RefreshToken
from app.security.tokens import create_access_token
from tests.conftest import requires_database

pytestmark = requires_database

PASSWORD = "correct-horse-battery-staple"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def account(make_user):
    """An ordinary active client account with a known password."""
    return make_user(email="studio@example.com", password=PASSWORD)


@pytest.fixture
def logged_in(client, account):
    """A live session: the parsed body of a successful login."""
    response = client.post(
        LOGIN, json={"email": account.email, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()


class TestLogin:
    def test_valid_login_returns_tokens_and_user(self, client, account):
        response = client.post(
            LOGIN, json={"email": account.email, "password": PASSWORD}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0
        assert body["user"]["email"] == account.email

    def test_email_is_case_insensitive(self, client, account):
        response = client.post(
            LOGIN, json={"email": "STUDIO@Example.COM", "password": PASSWORD}
        )
        assert response.status_code == 200

    def test_wrong_password_is_401(self, client, account):
        response = client.post(
            LOGIN, json={"email": account.email, "password": "wrong-password-here"}
        )
        assert response.status_code == 401

    def test_unknown_email_is_401(self, client):
        response = client.post(
            LOGIN, json={"email": "nobody@example.com", "password": PASSWORD}
        )
        assert response.status_code == 401

    def test_disabled_account_is_401(self, client, make_user):
        user = make_user(
            email="gone@example.com", password=PASSWORD, status=UserStatus.DISABLED
        )
        response = client.post(
            LOGIN, json={"email": user.email, "password": PASSWORD}
        )
        assert response.status_code == 401

    def test_all_failures_are_indistinguishable(self, client, account, make_user):
        """
        Wrong password, unknown address and disabled account must produce the
        same status and the same body. Any difference is an enumeration oracle:
        it tells a credential-stuffing run which of a million leaked addresses
        are worth attacking.
        """
        disabled = make_user(
            email="disabled@example.com", password=PASSWORD, status=UserStatus.DISABLED
        )
        responses = [
            client.post(LOGIN, json={"email": account.email, "password": "nope-nope-nope"}),
            client.post(LOGIN, json={"email": "ghost@example.com", "password": PASSWORD}),
            client.post(LOGIN, json={"email": disabled.email, "password": PASSWORD}),
        ]
        assert {r.status_code for r in responses} == {401}
        assert len({r.json()["detail"] for r in responses}) == 1

    def test_malformed_request_is_422(self, client):
        assert client.post(LOGIN, json={"email": "not-an-email", "password": "x"}).status_code == 422
        assert client.post(LOGIN, json={}).status_code == 422
        # Missing password entirely.
        assert client.post(LOGIN, json={"email": "a@b.com"}).status_code == 422

    def test_response_never_contains_the_password_hash(self, client, account):
        body = client.post(
            LOGIN, json={"email": account.email, "password": PASSWORD}
        ).text
        assert "password_hash" not in body
        assert "$argon2" not in body
        assert PASSWORD not in body

    def test_user_payload_is_an_allow_list(self, client, logged_in):
        assert set(logged_in["user"]) == {
            "id",
            "email",
            "name",
            "role",
            "status",
            "email_verified",
            "created_at",
            "last_login_at",
        }


class TestAccessTokens:
    def test_issued_token_reaches_a_protected_endpoint(self, client, logged_in):
        response = client.get(ME, headers=_auth(logged_in["access_token"]))
        assert response.status_code == 200

    def test_token_carries_no_personal_data(self, client, logged_in, account):
        """
        A JWT is signed, not encrypted. Anything in it is readable by anyone
        holding it -- and tokens end up in logs, proxies and crash reports.
        """
        claims = jwt.decode(
            logged_in["access_token"], options={"verify_signature": False}
        )
        serialised = str(claims)
        assert account.email not in serialised
        assert "password" not in serialised
        assert set(claims) <= {
            "sub", "role", "type", "iss", "aud", "iat", "nbf", "exp", "jti", "sid",
        }

    def test_expired_token_is_rejected(self, client, account):
        token = create_access_token(
            user_id=account.id,
            role=account.role.value,
            expires_delta=timedelta(seconds=-30),
        )
        assert client.get(ME, headers=_auth(token)).status_code == 401

    def test_wrong_signature_is_rejected(self, client, account):
        from app.config import Environment, Settings

        foreign = Settings(
            environment=Environment.TEST,
            jwt_secret="a-different-secret-entirely-for-this-test",
            _env_file=None,
        )
        token = create_access_token(
            user_id=account.id, role=account.role.value, settings=foreign
        )
        assert client.get(ME, headers=_auth(token)).status_code == 401

    def test_wrong_issuer_is_rejected(self, client, account, settings_for_test):
        forged = jwt.encode(
            {
                "sub": str(account.id),
                "role": "CLIENT",
                "type": "access",
                "iss": "some-other-service",
                "aud": settings_for_test.jwt_audience,
                "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
            },
            settings_for_test.jwt_secret,
            algorithm="HS256",
        )
        assert client.get(ME, headers=_auth(forged)).status_code == 401

    def test_wrong_audience_is_rejected(self, client, account, settings_for_test):
        forged = jwt.encode(
            {
                "sub": str(account.id),
                "role": "CLIENT",
                "type": "access",
                "iss": settings_for_test.jwt_issuer,
                "aud": "some-other-consumer",
                "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
            },
            settings_for_test.jwt_secret,
            algorithm="HS256",
        )
        assert client.get(ME, headers=_auth(forged)).status_code == 401

    def test_alg_none_is_rejected(self, client, account, settings_for_test):
        """The canonical JWT forgery: drop the signature, declare alg=none."""
        forged = jwt.encode(
            {
                "sub": str(account.id),
                "role": "ADMIN",
                "type": "access",
                "iss": settings_for_test.jwt_issuer,
                "aud": settings_for_test.jwt_audience,
                "exp": 9999999999,
            },
            key="",
            algorithm="none",
        )
        assert client.get(ME, headers=_auth(forged)).status_code == 401

    def test_refresh_token_is_not_a_bearer_credential(self, client, logged_in):
        """An opaque refresh token presented as a bearer token must not work."""
        assert client.get(ME, headers=_auth(logged_in["refresh_token"])).status_code == 401

    def test_malformed_tokens_are_rejected(self, client):
        for bad in ("", "not-a-jwt", "a.b.c", "Bearer", "." * 40):
            assert client.get(ME, headers=_auth(bad)).status_code == 401

    def test_missing_header_is_rejected(self, client):
        response = client.get(ME)
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"


class TestRefresh:
    def test_valid_refresh_returns_a_new_pair(self, client, logged_in):
        response = client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["refresh_token"] != logged_in["refresh_token"]
        assert body["access_token"]

    def test_new_access_token_works(self, client, logged_in):
        body = client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        ).json()
        assert client.get(ME, headers=_auth(body["access_token"])).status_code == 200

    def test_rotation_invalidates_the_previous_token(self, client, logged_in):
        client.post(REFRESH, json={"refresh_token": logged_in["refresh_token"]})
        second = client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        )
        assert second.status_code == 401

    def test_reuse_revokes_the_whole_family(self, client, db, logged_in):
        """
        The stolen-token scenario. A thief refreshes once; the real client then
        presents the token it still holds. The server cannot tell which is
        which, so it ends the session for both -- an interruption the user
        notices beats a compromise nobody does.
        """
        rotated = client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        ).json()

        replayed = client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        )
        assert replayed.status_code == 401

        # The successor, which was perfectly valid a moment ago, is now dead too.
        assert client.post(
            REFRESH, json={"refresh_token": rotated["refresh_token"]}
        ).status_code == 401

    def test_reuse_is_recorded_in_the_audit_log(self, client, db, logged_in):
        from app.models.audit import AuditLog
        from app.services.audit import AuditAction

        client.post(REFRESH, json={"refresh_token": logged_in["refresh_token"]})
        client.post(REFRESH, json={"refresh_token": logged_in["refresh_token"]})

        actions = db.execute(select(AuditLog.action)).scalars().all()
        assert AuditAction.REFRESH_REUSE_DETECTED in actions

    def test_unknown_token_is_401(self, client):
        assert client.post(
            REFRESH, json={"refresh_token": "never-issued-this-one"}
        ).status_code == 401

    def test_revoked_token_is_401(self, client, logged_in):
        client.post(LOGOUT, json={"refresh_token": logged_in["refresh_token"]})
        assert client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        ).status_code == 401

    def test_expired_token_is_401(self, client, db, logged_in):
        row = db.execute(select(RefreshToken)).scalars().one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.flush()
        assert client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        ).status_code == 401

    def test_expiry_is_not_extended_by_rotation(self, client, db, logged_in):
        """
        Otherwise a client refreshing every 29 days would hold an immortal
        session, and "30-day sessions" would mean nothing.
        """
        original = db.execute(select(RefreshToken)).scalars().one().expires_at
        client.post(REFRESH, json={"refresh_token": logged_in["refresh_token"]})
        successors = db.execute(
            select(RefreshToken).where(RefreshToken.replaced_by_id.is_(None))
        ).scalars().all()
        assert len(successors) == 1
        assert successors[0].expires_at == original

    def test_rotation_keeps_the_session_family(self, client, db, logged_in):
        client.post(REFRESH, json={"refresh_token": logged_in["refresh_token"]})
        families = {
            row.session_id for row in db.execute(select(RefreshToken)).scalars().all()
        }
        assert len(families) == 1

    def test_disabled_user_cannot_refresh(self, client, db, account, logged_in):
        account.status = UserStatus.DISABLED
        db.flush()
        assert client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        ).status_code == 401

    def test_malformed_request_is_422(self, client):
        assert client.post(REFRESH, json={}).status_code == 422


class TestLogout:
    def test_logout_succeeds(self, client, logged_in):
        response = client.post(
            LOGOUT, json={"refresh_token": logged_in["refresh_token"]}
        )
        assert response.status_code == 200

    def test_revoked_session_cannot_refresh(self, client, logged_in):
        client.post(LOGOUT, json={"refresh_token": logged_in["refresh_token"]})
        assert client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        ).status_code == 401

    def test_logout_revokes_the_whole_family(self, client, logged_in):
        rotated = client.post(
            REFRESH, json={"refresh_token": logged_in["refresh_token"]}
        ).json()
        client.post(LOGOUT, json={"refresh_token": rotated["refresh_token"]})
        assert client.post(
            REFRESH, json={"refresh_token": rotated["refresh_token"]}
        ).status_code == 401

    def test_unknown_token_still_returns_200(self, client):
        """
        Logging out must not be a probe for which tokens exist -- and a client
        that gets an error from logout tends to retry rather than discard its
        credentials, which is the opposite of what we want.
        """
        assert client.post(
            LOGOUT, json={"refresh_token": "never-issued"}
        ).status_code == 200

    def test_logout_is_idempotent(self, client, logged_in):
        for _ in range(3):
            assert client.post(
                LOGOUT, json={"refresh_token": logged_in["refresh_token"]}
            ).status_code == 200

    def test_access_token_survives_logout_within_its_window(self, client, logged_in):
        """
        Documenting the honest limit rather than pretending otherwise: a signed,
        stateless access token cannot be recalled from the client's machine. The
        30-minute lifetime is the bound, and a disabled account is still cut off
        immediately because the user is re-read on every request.
        """
        client.post(LOGOUT, json={"refresh_token": logged_in["refresh_token"]})
        assert client.get(ME, headers=_auth(logged_in["access_token"])).status_code == 200


class TestCurrentUser:
    def test_returns_the_caller(self, client, logged_in, account):
        body = client.get(ME, headers=_auth(logged_in["access_token"])).json()
        assert body["id"] == str(account.id)
        assert body["email"] == account.email
        assert body["role"] == UserRole.CLIENT.value

    def test_never_exposes_secrets(self, client, logged_in):
        text = client.get(ME, headers=_auth(logged_in["access_token"])).text
        for forbidden in (
            "password_hash",
            "$argon2",
            "refresh",
            "token_hash",
            "auth_provider_id",
            "postgres",
        ):
            assert forbidden not in text

    def test_anonymous_is_rejected(self, client):
        assert client.get(ME).status_code == 401

    def test_disabled_user_loses_access_immediately(
        self, client, db, account, logged_in
    ):
        """
        The access token is still cryptographically valid and unexpired. It stops
        working anyway, because the user row is re-read on every request.
        """
        token = logged_in["access_token"]
        assert client.get(ME, headers=_auth(token)).status_code == 200
        account.status = UserStatus.DISABLED
        db.flush()
        assert client.get(ME, headers=_auth(token)).status_code == 401

    def test_token_for_a_deleted_user_is_rejected(self, client):
        token = create_access_token(user_id=uuid.uuid4(), role="ADMIN")
        assert client.get(ME, headers=_auth(token)).status_code == 401


class TestAuditTrail:
    def test_successful_login_is_recorded(self, client, db, account):
        from app.models.audit import AuditLog
        from app.services.audit import AuditAction

        client.post(LOGIN, json={"email": account.email, "password": PASSWORD})
        actions = db.execute(select(AuditLog.action)).scalars().all()
        assert AuditAction.LOGIN_SUCCESS in actions

    def test_failed_login_is_recorded(self, client, db, account):
        from app.models.audit import AuditLog
        from app.services.audit import AuditAction

        client.post(LOGIN, json={"email": account.email, "password": "wrong-one-here"})
        actions = db.execute(select(AuditLog.action)).scalars().all()
        assert AuditAction.LOGIN_FAILURE in actions

    def test_audit_metadata_never_holds_credentials(self, client, db, account):
        from app.models.audit import AuditLog

        client.post(LOGIN, json={"email": account.email, "password": PASSWORD})
        client.post(LOGIN, json={"email": account.email, "password": "wrong-one-here"})

        for entry in db.execute(select(AuditLog)).scalars().all():
            serialised = str(entry.metadata_json or {})
            assert PASSWORD not in serialised
            assert "wrong-one-here" not in serialised
            assert "$argon2" not in serialised
