"""
The authentication service abstraction.

Phase 3 will add ``/auth/login``, ``/auth/refresh`` and ``/auth/logout``. What
lives here now is the seam those endpoints will call, so the HTTP layer never
learns how credentials are checked. The point of the abstraction is that adding
an OAuth provider later means a second implementation of
:class:`AuthenticationProvider`, not surgery on a router.

Nothing in this module writes a plaintext password anywhere -- not to the
database, not to a log line.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.enums import UserStatus
from app.models.token import RefreshToken
from app.models.user import User
from app.security.passwords import hash_password, needs_rehash, verify_password
from app.security.tokens import (
    create_access_token,
    generate_refresh_token,
    hash_token,
)


@dataclass(frozen=True)
class AuthenticatedSession:
    """What a successful login hands back to the HTTP layer."""

    user: User
    access_token: str
    refresh_token: str
    expires_in_seconds: int


class AuthenticationProvider(Protocol):
    """
    How a caller proves who they are.

    Kept to one method so a future ``GoogleOAuthProvider`` is a drop-in. The
    return is ``Optional[User]``: ``None`` means "not authenticated", with no
    detail about why, because the caller must not be able to turn the reason
    into an account-enumeration oracle.
    """

    def authenticate(self, db: Session, **credentials: object) -> User | None: ...


class PasswordAuthenticationProvider:
    """Email + password against the Argon2id hash stored on the user row."""

    def authenticate(self, db: Session, **credentials: object) -> User | None:
        email = str(credentials.get("email") or "").strip().lower()
        password = str(credentials.get("password") or "")
        if not email or not password:
            return None

        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

        if user is None:
            # Hash anyway so a missing account and a wrong password take the
            # same time. Without this, response latency alone reveals which
            # email addresses are registered.
            _burn_time(password)
            return None

        if not verify_password(password, user.password_hash or ""):
            return None

        if user.status is not UserStatus.ACTIVE:
            return None

        # The one moment the plaintext exists and the parameters can be upgraded.
        if needs_rehash(user.password_hash or ""):
            user.password_hash = hash_password(password)

        return user


def _burn_time(password: str) -> None:
    """Spend roughly one hash's worth of time, discarding the result."""
    try:
        hash_password(password if len(password) >= 12 else password.ljust(12, "x"))
    except Exception:  # noqa: BLE001 - timing padding must never raise
        pass


def issue_session(
    db: Session,
    user: User,
    *,
    device_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> AuthenticatedSession:
    """
    Mint an access token and persist a hashed refresh token.

    The caller is responsible for committing. Returning the plaintext refresh
    token here is the only place it exists; the database sees only its digest.
    """
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)

    access = create_access_token(
        user_id=user.id, role=user.role.value, settings=settings
    )
    refresh = generate_refresh_token()

    db.add(
        RefreshToken(
            user_id=user.id,
            device_id=device_id,
            token_hash=hash_token(refresh),
            issued_at=now,
            expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    user.last_login_at = now
    user.last_seen_at = now

    return AuthenticatedSession(
        user=user,
        access_token=access,
        refresh_token=refresh,
        expires_in_seconds=settings.access_token_ttl_minutes * 60,
    )


def revoke_refresh_token(db: Session, token: str) -> bool:
    """Mark a refresh token revoked. True if a live token was found."""
    digest = hash_token(token)
    row = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == digest)
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    return True
