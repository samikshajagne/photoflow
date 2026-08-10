"""
Authentication service tests.

Phase 3 builds the login endpoint on top of this; these tests pin the behaviour
it will rely on, most importantly that a failed login is indistinguishable from
a nonexistent account.
"""

from __future__ import annotations

from app.auth.service import (
    PasswordAuthenticationProvider,
    issue_session,
    revoke_refresh_token,
)
from app.models.enums import UserStatus
from app.models.token import RefreshToken
from app.security.tokens import decode_access_token, hash_token
from tests.conftest import requires_database

pytestmark = requires_database

PASSWORD = "correct-horse-battery-staple"


class TestPasswordAuthentication:
    def test_correct_credentials_authenticate(self, db, make_user):
        user = make_user(email="studio@example.test", password=PASSWORD)
        result = PasswordAuthenticationProvider().authenticate(
            db, email="studio@example.test", password=PASSWORD
        )
        assert result is not None and result.id == user.id

    def test_email_matching_is_case_insensitive(self, db, make_user):
        make_user(email="studio@example.test", password=PASSWORD)
        result = PasswordAuthenticationProvider().authenticate(
            db, email="  Studio@Example.TEST ", password=PASSWORD
        )
        assert result is not None

    def test_wrong_password_fails(self, db, make_user):
        make_user(email="studio@example.test", password=PASSWORD)
        assert (
            PasswordAuthenticationProvider().authenticate(
                db, email="studio@example.test", password="wrong-password-here"
            )
            is None
        )

    def test_unknown_email_fails(self, db):
        assert (
            PasswordAuthenticationProvider().authenticate(
                db, email="nobody@example.test", password=PASSWORD
            )
            is None
        )

    def test_disabled_account_cannot_authenticate(self, db, make_user):
        make_user(
            email="gone@example.test", password=PASSWORD, status=UserStatus.DISABLED
        )
        assert (
            PasswordAuthenticationProvider().authenticate(
                db, email="gone@example.test", password=PASSWORD
            )
            is None
        )

    def test_empty_credentials_fail(self, db):
        provider = PasswordAuthenticationProvider()
        assert provider.authenticate(db, email="", password="") is None
        assert provider.authenticate(db) is None


class TestSessionIssuance:
    def test_issues_a_usable_access_token(self, db, make_user):
        user = make_user()
        session = issue_session(db, user)
        claims = decode_access_token(session.access_token)
        assert claims.subject == user.id
        assert session.expires_in_seconds > 0

    def test_refresh_token_is_stored_only_as_a_hash(self, db, make_user):
        """A leaked database backup must not hand over working sessions."""
        user = make_user()
        session = issue_session(db, user)
        db.flush()

        row = db.query(RefreshToken).filter_by(user_id=user.id).one()
        assert row.token_hash == hash_token(session.refresh_token)
        assert row.token_hash != session.refresh_token
        assert session.refresh_token not in row.token_hash

    def test_login_timestamps_are_recorded(self, db, make_user):
        user = make_user()
        assert user.last_login_at is None
        issue_session(db, user)
        assert user.last_login_at is not None

    def test_revoking_a_token_works_once(self, db, make_user):
        user = make_user()
        session = issue_session(db, user)
        db.flush()
        assert revoke_refresh_token(db, session.refresh_token) is True
        assert revoke_refresh_token(db, session.refresh_token) is False

    def test_revoking_an_unknown_token_is_false_not_an_error(self, db):
        assert revoke_refresh_token(db, "never-issued-this-one") is False

    def test_revoked_token_is_not_usable(self, db, make_user):
        user = make_user()
        session = issue_session(db, user)
        db.flush()
        revoke_refresh_token(db, session.refresh_token)
        row = db.query(RefreshToken).filter_by(user_id=user.id).one()
        assert row.is_usable_at() is False
