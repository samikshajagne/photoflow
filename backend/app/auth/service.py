"""
The authentication service.

The HTTP layer never learns how credentials are checked: it calls
:class:`PasswordAuthenticationProvider`, :func:`issue_session` and
:func:`rotate_session`, and adding an OAuth provider later means a second
implementation of :class:`AuthenticationProvider` rather than surgery on a
router.

Nothing in this module writes a plaintext password anywhere -- not to the
database, not to a log line.

The session model, stated once so the rest of the code can assume it:

    login  ──►  access token   (30 min, signed, stateless, NOT revocable)
                refresh token  (30 days, opaque, hashed, revocable, rotating)

    refresh ──► the presented refresh token is spent and replaced.
                Its successor shares the same session_id -- the "family".

    logout ──►  the family is revoked. Every refresh token descended from that
                login stops working immediately.

An access token cannot be un-issued; that is what "stateless" means, and
pretending otherwise would be dishonest. The 30-minute lifetime *is* the
revocation window, and it is bounded further by re-reading the user from the
database on every request, so a disabled account loses access at once even
though its token is still cryptographically valid.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select, update
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
    session_id: uuid.UUID


class RefreshOutcome(str, enum.Enum):
    """Why a refresh attempt ended the way it did."""

    OK = "ok"
    UNKNOWN = "unknown"           # no such token, ever
    EXPIRED = "expired"
    REVOKED = "revoked"           # explicitly killed, e.g. by logout
    REUSED = "reused"             # already rotated -- theft or a buggy client
    USER_UNAVAILABLE = "user_unavailable"  # deleted or disabled since issue


@dataclass(frozen=True)
class RefreshResult:
    """
    The result of :func:`rotate_session`.

    ``session`` is populated only on :attr:`RefreshOutcome.OK`. The outcome is
    for the *server's* benefit -- audit logging, and deciding whether to burn
    the family -- and must never be echoed to the caller, who gets one
    undifferentiated 401 whatever went wrong.
    """

    outcome: RefreshOutcome
    session: AuthenticatedSession | None = None
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is RefreshOutcome.OK


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
    Start a new session: mint an access token and persist a hashed refresh token.

    The caller is responsible for committing. The returned plaintext refresh
    token is the only place it will ever exist; the database sees only its
    SHA-256 digest.
    """
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    session_id = uuid.uuid4()

    refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            device_id=device_id,
            session_id=session_id,
            token_hash=hash_token(refresh),
            issued_at=now,
            expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        )
    )

    access = create_access_token(
        user_id=user.id,
        role=user.role.value,
        settings=settings,
        session_id=session_id,
    )

    user.last_login_at = now
    user.last_seen_at = now

    return AuthenticatedSession(
        user=user,
        access_token=access,
        refresh_token=refresh,
        expires_in_seconds=settings.access_token_ttl_minutes * 60,
        session_id=session_id,
    )


def rotate_session(
    db: Session,
    presented_token: str,
    *,
    settings: Settings | None = None,
) -> RefreshResult:
    """
    Exchange a refresh token for a new pair, or explain why not.

    **Rotation.** The presented token is spent: it is marked revoked and pointed
    at its successor. A refresh token is therefore single-use, which is what
    makes theft detectable at all — without rotation a stolen token works
    silently for its full 30 days and nothing anywhere notices.

    **Reuse detection.** If a token that has *already* been rotated is presented
    again, exactly one of two things happened: the legitimate client replayed a
    request (a network retry, a restored browser tab), or someone is using a
    stolen copy. The server cannot tell which, and the safe reading of an
    ambiguous signal is the hostile one — so the entire family is revoked. The
    real user is logged out and has to sign in again; the thief is logged out
    too. An interruption the user notices is a far better outcome than a
    compromise nobody does.

    **The user is re-checked.** A refresh issued before an account was disabled
    must not be able to mint a fresh access token afterwards.

    The caller commits. Returning a structured outcome rather than raising lets
    the endpoint audit-log precisely what happened while returning one
    indistinguishable 401 to the caller.
    """
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    digest = hash_token(presented_token)

    row = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == digest)
    ).scalar_one_or_none()

    if row is None:
        return RefreshResult(RefreshOutcome.UNKNOWN)

    if row.was_rotated:
        # Already spent. Burn the whole family and report it.
        row.reused_at = row.reused_at or now
        revoke_session_family(db, row.session_id, reason="reuse_detected", moment=now)
        return RefreshResult(
            RefreshOutcome.REUSED,
            user_id=row.user_id,
            session_id=row.session_id,
        )

    if row.revoked_at is not None:
        return RefreshResult(
            RefreshOutcome.REVOKED, user_id=row.user_id, session_id=row.session_id
        )

    if now >= row.expires_at:
        return RefreshResult(
            RefreshOutcome.EXPIRED, user_id=row.user_id, session_id=row.session_id
        )

    user = db.get(User, row.user_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        # Kill the family too: an account that has been disabled should not have
        # a live session waiting for it if it is ever re-enabled.
        revoke_session_family(db, row.session_id, reason="user_disabled", moment=now)
        return RefreshResult(
            RefreshOutcome.USER_UNAVAILABLE,
            user_id=row.user_id,
            session_id=row.session_id,
        )

    # Mint the successor, keeping the family and the device association.
    successor_plaintext = generate_refresh_token()
    successor = RefreshToken(
        user_id=user.id,
        device_id=row.device_id,
        session_id=row.session_id,
        token_hash=hash_token(successor_plaintext),
        issued_at=now,
        # The family does not outlive the original login: the expiry is carried
        # forward, not extended. Otherwise a token refreshed every 29 days would
        # be immortal, and "30-day sessions" would mean nothing.
        expires_at=row.expires_at,
    )
    db.add(successor)
    db.flush()  # populate successor.id before pointing the old row at it

    row.replaced_by_id = successor.id
    row.revoked_at = now
    row.revoked_reason = "rotated"

    access = create_access_token(
        user_id=user.id,
        role=user.role.value,
        settings=settings,
        session_id=row.session_id,
    )
    user.last_seen_at = now

    return RefreshResult(
        RefreshOutcome.OK,
        session=AuthenticatedSession(
            user=user,
            access_token=access,
            refresh_token=successor_plaintext,
            expires_in_seconds=settings.access_token_ttl_minutes * 60,
            session_id=row.session_id,
        ),
        user_id=user.id,
        session_id=row.session_id,
    )


def revoke_session_family(
    db: Session,
    session_id: uuid.UUID,
    *,
    reason: str,
    moment: datetime | None = None,
) -> int:
    """
    Revoke every still-live refresh token descended from one login.

    One indexed UPDATE, which is the reason ``session_id`` exists as a column
    rather than being reconstructed by walking ``replaced_by_id`` backwards.
    Returns how many rows were affected.
    """
    moment = moment or datetime.now(timezone.utc)
    result = db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.session_id == session_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=moment, revoked_reason=reason)
    )
    return int(result.rowcount or 0)


def revoke_refresh_token(
    db: Session, token: str, *, reason: str = "logout"
) -> bool:
    """
    Revoke the session the given refresh token belongs to. True if one was live.

    Deliberately family-wide rather than single-token: "log out" means the
    session ends, and revoking only the token the client happened to be holding
    would leave its predecessors' successors alive if rotation had raced.
    """
    digest = hash_token(token)
    row = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == digest)
    ).scalar_one_or_none()
    if row is None:
        return False
    revoked = revoke_session_family(db, row.session_id, reason=reason)
    return revoked > 0


def revoke_all_sessions_for_user(
    db: Session, user_id: uuid.UUID, *, reason: str
) -> int:
    """
    Revoke every live refresh token a user holds, across all sessions.

    Used when an account is disabled: the access tokens already issued still
    work until they expire (they are stateless), but no new ones can be minted,
    and ``get_current_user`` re-reads the user, so the practical effect is
    immediate.
    """
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
    )
    return int(result.rowcount or 0)
