"""
Administrative user management.

``POST   /api/v1/admin/users``              create a client (or another admin)
``GET    /api/v1/admin/users``              list accounts
``GET    /api/v1/admin/users/{id}``         one account
``POST   /api/v1/admin/users/{id}/disable`` suspend
``POST   /api/v1/admin/users/{id}/enable``  restore

This is the API the local admin dashboard will call in Phase 6. It is not the
dashboard, and building the endpoints first is deliberate: the authorisation
boundary is the part that has to be right, and it is much easier to test
exhaustively without a UI in the way.

**Every route here requires ADMIN, enforced server-side.** A CLIENT who
constructs the HTTP request by hand gets 403, exactly as one who clicks a button
that should not exist would. There is no code path in this module that consults
anything the caller sent about their own role — the role is read from the
database row that the access token's subject points at.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ClientIp
from app.auth.dependencies import AdminUser
from app.auth.service import revoke_all_sessions_for_user
from app.database.session import get_db
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.schemas.auth import (
    CreateUserRequest,
    UserListResponse,
    UserResponse,
)
from app.security.passwords import PasswordPolicyError, hash_password
from app.services import audit
from app.services.audit import AuditAction

router = APIRouter(tags=["admin"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account (admin only)",
    responses={
        403: {"description": "Insufficient permissions."},
        409: {"description": "An account with that email already exists."},
    },
)
def create_user(
    payload: CreateUserRequest,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> UserResponse:
    """
    Create a client account.

    The administrator sets the initial password and communicates it out of band.
    That is a deliberate Phase 3 simplification, and it has a real cost worth
    naming: for a moment, two people know the password. The fix is an invitation
    flow — create the account with no password, email a single-use token, let
    the customer choose their own — which needs outbound email that does not
    exist yet. Until then, tell customers to change it, and note that
    ``PASSWORD_CHANGED`` is already in the audit vocabulary waiting for that
    endpoint.

    Duplicate emails return 409 rather than a generic error: this endpoint is
    already ADMIN-only, so "does this address exist" is not information the
    caller lacks, and a clear conflict is worth more than a uniform failure to
    someone doing data entry.
    """
    email = payload.email.strip().lower()

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    try:
        password_hash = hash_password(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    user = User(
        email=email,
        name=payload.name,
        password_hash=password_hash,
        role=payload.role,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()

    audit.record(
        db,
        action=(
            AuditAction.ADMIN_CREATED
            if payload.role is UserRole.ADMIN
            else AuditAction.CLIENT_CREATED
        ),
        actor_user_id=admin.id,
        actor_ip=_safe_ip(ip),
        target_type="user",
        target_id=str(user.id),
        # The password is not here, and the scrubber would redact it if a future
        # edit tried. The email is fine: it is the thing being created.
        metadata={"email": email, "role": payload.role.value},
    )
    db.commit()

    return UserResponse.model_validate(user)


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="List accounts (admin only)",
)
def list_users(
    db: DbSession,
    admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    role: UserRole | None = None,
    # noqa on the next line: Query() in a default is FastAPI's declaration
    # syntax, not the mutable-default bug B008 is about.
    account_status: UserStatus | None = Query(default=None, alias="status"),  # noqa: B008
) -> UserListResponse:
    """
    Paginated account list.

    ``limit`` is capped at 200 by the query constraint rather than by trimming
    afterwards, so a caller asking for 100000 gets a clear 422 instead of a
    response that silently disagrees with what they requested.
    """
    conditions = []
    if role is not None:
        conditions.append(User.role == role)
    if account_status is not None:
        conditions.append(User.status == account_status)

    total = db.execute(
        select(func.count()).select_from(User).where(*conditions)
    ).scalar_one()

    rows = (
        db.execute(
            select(User)
            .where(*conditions)
            .order_by(User.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    return UserListResponse(
        items=[UserResponse.model_validate(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="One account (admin only)",
    responses={404: {"description": "No such account."}},
)
def get_user(user_id: uuid.UUID, db: DbSession, admin: AdminUser) -> UserResponse:
    return UserResponse.model_validate(_require_user(db, user_id))


@router.post(
    "/users/{user_id}/disable",
    response_model=UserResponse,
    summary="Suspend an account (admin only)",
    responses={
        400: {"description": "An administrator cannot disable their own account."},
        404: {"description": "No such account."},
    },
)
def disable_user(
    user_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> UserResponse:
    """
    Disable an account and revoke its sessions.

    Both halves matter. Setting the status alone would leave live refresh tokens
    able to mint access tokens; revoking the sessions alone would let the user
    log in again. Together, the account is out immediately: existing access
    tokens fail at the next request because ``get_current_user`` re-reads the
    row, and nothing can issue new ones.

    An admin cannot disable themselves — not because it is dangerous in itself,
    but because with one administrator it locks everybody out of admin functions
    permanently, and recovering means a database edit at 2am.
    """
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An administrator cannot disable their own account.",
        )

    user = _require_user(db, user_id)
    user.status = UserStatus.DISABLED
    revoked = revoke_all_sessions_for_user(db, user.id, reason="user_disabled")

    audit.record(
        db,
        action=AuditAction.USER_DISABLED,
        actor_user_id=admin.id,
        actor_ip=_safe_ip(ip),
        target_type="user",
        target_id=str(user.id),
        metadata={"sessions_revoked": revoked},
    )
    db.commit()
    return UserResponse.model_validate(user)


@router.post(
    "/users/{user_id}/enable",
    response_model=UserResponse,
    summary="Restore an account (admin only)",
    responses={404: {"description": "No such account."}},
)
def enable_user(
    user_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> UserResponse:
    """
    Re-activate an account.

    Sessions revoked while it was disabled stay revoked — the user signs in
    again. Resurrecting old refresh tokens would mean a session that survived a
    suspension, which defeats the point of suspending it.
    """
    user = _require_user(db, user_id)
    user.status = UserStatus.ACTIVE

    audit.record(
        db,
        action=AuditAction.USER_ENABLED,
        actor_user_id=admin.id,
        actor_ip=_safe_ip(ip),
        target_type="user",
        target_id=str(user.id),
    )
    db.commit()
    return UserResponse.model_validate(user)


def _require_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such account."
        )
    return user


def _safe_ip(value: str) -> str | None:
    import ipaddress

    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value
