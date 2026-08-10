"""
Request and response models for authentication.

The response models are an allow-list, not a convenience. ``UserResponse``
enumerates the fields that may leave the server; anything added to the ``User``
model later — a password hash, an internal flag, a billing note — is invisible
here until someone deliberately adds it. A serializer that dumps the ORM object
wholesale is how ``password_hash`` ends up in a JSON body.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRole, UserStatus
from app.security.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class LoginRequest(BaseModel):
    """Credentials for ``POST /api/v1/auth/login``."""

    email: EmailStr
    # Not constrained to the *policy* minimum: rejecting a short password at
    # login with a validation error would tell an attacker that the policy
    # exists and, worse, would 422 instead of 401 for a real user whose password
    # predates a policy change. Only an absurd length is refused, as a DoS guard.
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    # Optional client hint, used to associate the session with a device row in a
    # later phase. Purely informational today.
    device_fingerprint: str | None = Field(default=None, max_length=128)
    device_name: str | None = Field(default=None, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class UserResponse(BaseModel):
    """
    Safe account information.

    Explicitly absent: ``password_hash``, ``auth_provider_id``, refresh tokens,
    and anything else that describes how the account is secured rather than what
    it is.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # Plain str, not EmailStr, deliberately. This is an *output* model reading a
    # value that was validated when it went in. Re-validating stored data on the
    # way out converts a historical address the validator no longer likes into a
    # 500 on a read endpoint -- the row is already there, and refusing to
    # display it helps nobody.
    email: str
    name: str | None
    role: UserRole
    status: UserStatus
    email_verified: bool
    created_at: datetime
    last_login_at: datetime | None


class TokenResponse(BaseModel):
    """
    What a successful login or refresh returns.

    ``expires_in`` is seconds, matching OAuth 2.0 convention, so a client knows
    when to refresh without having to parse the JWT — which it should not be
    doing, since the token's contents are the server's business.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class MessageResponse(BaseModel):
    """A bare acknowledgement, for endpoints with nothing useful to return."""

    detail: str


class CreateUserRequest(BaseModel):
    """
    Admin-only account creation.

    There is no public signup endpoint — see ``app/api/v1/admin_users.py`` for
    why — so this is the only way an account comes into existence besides the
    bootstrap CLI.
    """

    email: EmailStr
    name: str | None = Field(default=None, max_length=200)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    # Defaults to CLIENT. Creating a second ADMIN over HTTP is possible but must
    # be deliberate, and is audit-logged distinctly.
    role: UserRole = UserRole.CLIENT


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int
