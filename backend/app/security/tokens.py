"""
Access and refresh tokens.

Two different things with two different designs, and conflating them is a
common and expensive mistake:

* **Access token** -- a short-lived (30 min) JWT, HS256, minted and verified by
  this backend only. Symmetric signing is correct here precisely because no one
  else needs to verify it.
* **Refresh token** -- opaque random bytes, not a JWT. There is nothing for a
  client to read inside it, and because it is stored server-side as a SHA-256
  digest it can be *revoked*, which a stateless JWT cannot be. Logging out a
  stolen session has to actually work.

A third kind arrives in Phase 3+: the **entitlement token** the desktop app
verifies while offline. That one must be Ed25519, because the client has to
verify it without being able to mint one -- an HS256 secret compiled into a
Windows binary is a shared secret with every customer. The public key ships in
the app; the private key never leaves the backend host.
"""

from __future__ import annotations

import enum
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import Settings, get_settings

# Bytes of entropy in a refresh token. 32 bytes = 256 bits, urlsafe-encoded.
REFRESH_TOKEN_BYTES = 32


class TokenType(str, enum.Enum):
    ACCESS = "access"
    REFRESH = "refresh"


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired or not ours."""


@dataclass(frozen=True)
class TokenClaims:
    """The subset of JWT claims the application actually acts on."""

    subject: uuid.UUID
    role: str
    token_type: TokenType
    expires_at: datetime
    jti: str


def create_access_token(
    *,
    user_id: uuid.UUID,
    role: str,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Mint a signed access token.

    ``jti`` is included so an individual token can be denylisted later without
    revoking every session the user has, and ``iss`` so a token minted by a
    different PhotoFlow service cannot be replayed here.
    """
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    expiry = now + (
        expires_delta or timedelta(minutes=settings.access_token_ttl_minutes)
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": TokenType.ACCESS.value,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        # Never let a caller overwrite a security-relevant claim.
        reserved = {"sub", "role", "type", "iss", "iat", "nbf", "exp", "jti"}
        payload.update({k: v for k, v in extra_claims.items() if k not in reserved})
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(
    token: str, settings: Settings | None = None
) -> TokenClaims:
    """
    Verify a token and return its claims, or raise :class:`TokenError`.

    ``algorithms`` is pinned to the configured algorithm -- passing the list from
    the token's own header is the classic "alg: none" forgery.
    """
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid.") from exc

    if payload.get("type") != TokenType.ACCESS.value:
        # A refresh token presented as a bearer credential must not be accepted.
        raise TokenError("Token is not an access token.")

    try:
        subject = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise TokenError("Token subject is not a valid user id.") from exc

    return TokenClaims(
        subject=subject,
        role=str(payload.get("role", "")),
        token_type=TokenType.ACCESS,
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        jti=str(payload.get("jti", "")),
    )


def generate_refresh_token() -> str:
    """A fresh opaque refresh token. Returned to the client exactly once."""
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """
    SHA-256 of an opaque token, for storage and lookup.

    Not Argon2: a 256-bit random token has nothing to brute-force, and the
    lookup needs to stay a cheap indexed equality. The property we want is
    "a stolen database yields no usable tokens", and a fast hash gives us that.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
