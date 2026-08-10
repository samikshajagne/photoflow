"""
Authentication and authorisation dependencies.

This is the whole authorisation surface for the backend: an endpoint is either
public, or it declares ``Depends(get_current_user)`` /
``Depends(require_admin)``. Keeping it to one small module means "which
endpoints are protected" is answerable by grep, rather than by reading every
router.

Phase 2 provides the boundary; Phase 3 adds the login endpoint that issues the
tokens these dependencies consume.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.security.tokens import TokenError, decode_access_token

# auto_error=False so a missing header produces our own 401 with a
# ``WWW-Authenticate`` header, rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> User:
    """
    Resolve the caller from a bearer token, or raise 401.

    The user is loaded from the database on every request rather than trusted
    from the token's claims. A 30-minute access token issued before an account
    was disabled would otherwise keep working for up to 30 minutes -- which is
    exactly the window during which you disable an account.

    Every failure returns the same generic 401. Distinguishing "no such user"
    from "wrong token" from "disabled account" tells an attacker which emails
    have accounts.
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED

    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError:
        raise _UNAUTHENTICATED from None

    user = db.get(User, claims.subject)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise _UNAUTHENTICATED

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: UserRole) -> Callable[[User], User]:
    """
    Build a dependency that admits only the given roles.

    Returns 403 (authenticated, but not allowed) rather than 401, because the
    caller has already proved who they are and retrying with the same
    credentials will not help.
    """
    allowed = frozenset(roles)

    def _dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
            )
        return user

    return _dependency


require_admin = require_role(UserRole.ADMIN)
require_client = require_role(UserRole.CLIENT, UserRole.ADMIN)

AdminUser = Annotated[User, Depends(require_admin)]
