"""
Authentication endpoints.

``POST /api/v1/auth/login``    email + password  -> access + refresh
``POST /api/v1/auth/refresh``  refresh           -> new access + new refresh
``POST /api/v1/auth/logout``   refresh           -> session revoked
``GET  /api/v1/auth/me``       access            -> the caller's own account

There is deliberately **no signup endpoint**. PhotoFlow is a controlled
commercial product: an account exists because it was sold, and self-service
account creation would let anyone mint a row in a table the licensing model
treats as the customer list. Accounts are created by an administrator
(``POST /api/v1/admin/users``) or by the bootstrap CLI. If a self-service trial
is ever wanted, it is a new endpoint with its own rate limits, email
verification and abuse handling — not a flag on this one.

One rule runs through the whole module: **every authentication failure returns
the same 401 with the same body.** Wrong password, unknown email, disabled
account, revoked token, expired token, replayed token — indistinguishable from
outside. The server knows precisely which it was, records that in the audit log,
and says none of it to the caller. Anything else is an account-enumeration
oracle, and enumeration is how a credential-stuffing run decides which addresses
are worth attacking.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import ClientIp, Limiter, SettingsDep, enforce
from app.auth.dependencies import CurrentUser
from app.auth.service import (
    PasswordAuthenticationProvider,
    RefreshOutcome,
    issue_session,
    revoke_refresh_token,
    rotate_session,
)
from app.database.session import get_db
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from app.services import audit
from app.services.audit import AuditAction

router = APIRouter(tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]

# The single response every authentication failure produces.
_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid credentials.",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange email and password for tokens",
    responses={
        401: {"description": "Invalid credentials."},
        429: {"description": "Too many attempts."},
    },
)
def login(
    payload: LoginRequest,
    db: DbSession,
    limiter: Limiter,
    settings: SettingsDep,
    ip: ClientIp,
) -> TokenResponse:
    """
    Authenticate and start a session.

    Rate limited on **two** keys: the email address and the caller's IP. The
    per-email key stops someone working through a password list against one
    known account; the per-IP key stops them spraying one common password across
    many accounts, which the per-email key alone would not catch. Both are
    checked before the password is verified, so a blocked attacker does not even
    get to spend the server's Argon2id time.
    """
    email = payload.email.strip().lower()

    enforce(
        limiter.check(
            "login:email",
            email,
            limit=settings.rate_limit_login_attempts,
            window_seconds=settings.rate_limit_login_window_seconds,
        )
    )
    enforce(
        limiter.check(
            "login:ip",
            ip,
            # A studio behind one NAT may have several legitimate users, so the
            # IP budget is deliberately looser than the per-account one.
            limit=settings.rate_limit_login_attempts * 4,
            window_seconds=settings.rate_limit_login_window_seconds,
        )
    )

    user = PasswordAuthenticationProvider().authenticate(
        db, email=email, password=payload.password
    )

    if user is None:
        # The audit row records the attempted address; the response does not
        # acknowledge whether it exists.
        audit.record(
            db,
            action=AuditAction.LOGIN_FAILURE,
            actor_ip=_safe_ip(ip),
            target_type="email",
            target_id=email,
            metadata={"reason": "invalid_credentials"},
        )
        db.commit()
        raise _INVALID_CREDENTIALS

    session = issue_session(db, user, settings=settings)

    audit.record(
        db,
        action=AuditAction.LOGIN_SUCCESS,
        actor_user_id=user.id,
        actor_ip=_safe_ip(ip),
        target_type="user",
        target_id=str(user.id),
        metadata={"session_id": str(session.session_id)},
    )
    db.commit()

    # A correct password clears the budget, so a user who mistyped three times
    # and then succeeded is not one attempt from being locked out.
    limiter.reset("login:email", email)

    return _token_response(session)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new pair",
    responses={
        401: {"description": "Invalid credentials."},
        429: {"description": "Too many attempts."},
    },
)
def refresh(
    payload: RefreshRequest,
    db: DbSession,
    limiter: Limiter,
    settings: SettingsDep,
    ip: ClientIp,
) -> TokenResponse:
    """
    Rotate a session.

    The presented token is spent and replaced. Presenting an already-spent token
    revokes the entire session family — see ``rotate_session`` for the reasoning,
    which is worth reading before changing anything here.
    """
    enforce(
        limiter.check(
            "refresh:ip",
            ip,
            limit=settings.rate_limit_refresh_attempts,
            window_seconds=settings.rate_limit_refresh_window_seconds,
        )
    )

    result = rotate_session(db, payload.refresh_token, settings=settings)

    if result.outcome is RefreshOutcome.REUSED:
        # The one failure worth shouting about in the log: either a token was
        # stolen, or a client is replaying. Both need to be visible.
        audit.record(
            db,
            action=AuditAction.REFRESH_REUSE_DETECTED,
            actor_user_id=result.user_id,
            actor_ip=_safe_ip(ip),
            target_type="session",
            target_id=str(result.session_id),
            metadata={"outcome": "family_revoked"},
        )
        db.commit()
        raise _INVALID_CREDENTIALS

    if not result.ok or result.session is None:
        audit.record(
            db,
            action=AuditAction.LOGIN_FAILURE,
            actor_user_id=result.user_id,
            actor_ip=_safe_ip(ip),
            target_type="session",
            target_id=str(result.session_id) if result.session_id else None,
            metadata={"reason": f"refresh_{result.outcome.value}"},
        )
        db.commit()
        raise _INVALID_CREDENTIALS

    audit.record(
        db,
        action=AuditAction.REFRESH_SUCCESS,
        actor_user_id=result.user_id,
        actor_ip=_safe_ip(ip),
        target_type="session",
        target_id=str(result.session_id),
    )
    db.commit()

    return _token_response(result.session)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Revoke the session a refresh token belongs to",
)
def logout(
    payload: LogoutRequest,
    db: DbSession,
    ip: ClientIp,
) -> MessageResponse:
    """
    End a session.

    Two honest notes about what this does and does not do:

    * It revokes the **refresh** family, so no new access token can be minted
      for this session. That part is real and immediate.
    * It cannot invalidate an access token already on the client's machine. A
      signed, stateless token is valid until it expires; that is what stateless
      means. The bound on the damage is the 30-minute lifetime, plus the fact
      that every request re-reads the user, so a disabled account loses access
      instantly regardless.

    The response is always 200, even for an unknown or already-revoked token.
    Logging out is not an operation that should let a caller probe which tokens
    exist, and a client that gets an error from logout tends to retry rather
    than discard its credentials — which is the opposite of what we want.
    """
    revoked = revoke_refresh_token(db, payload.refresh_token, reason="logout")
    if revoked:
        audit.record(
            db,
            action=AuditAction.LOGOUT,
            actor_ip=_safe_ip(ip),
            target_type="session",
            target_id=None,
        )
    db.commit()
    return MessageResponse(detail="Signed out.")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="The authenticated caller's own account",
    responses={401: {"description": "Not authenticated."}},
)
def me(user: CurrentUser) -> UserResponse:
    """
    Safe account information for the caller.

    ``get_current_user`` has already re-read the row from the database and
    rejected any non-ACTIVE account, so a disabled user reaches a 401 here
    rather than a body describing their own suspension.
    """
    return UserResponse.model_validate(user)


def _token_response(session) -> TokenResponse:
    return TokenResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in_seconds,
        user=UserResponse.model_validate(session.user),
    )


def _safe_ip(value: str) -> str | None:
    """
    ``audit_logs.actor_ip`` is an INET column, so a forged header that is not an
    address must become NULL rather than abort the transaction that was trying
    to record a failed login.
    """
    import ipaddress

    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value
