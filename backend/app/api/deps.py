"""
Shared request-scoped dependencies.

Two things every security-relevant endpoint needs and neither of which should be
re-derived inline: the caller's IP address, and the rate limiter.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings
from app.security.rate_limit import RateLimiter, RateLimitVerdict


def client_ip(request: Request) -> str:
    """
    The caller's address, as well as it can be known.

    Behind a reverse proxy the socket address is the proxy, so
    ``X-Forwarded-For`` is consulted — but only its **first** entry, and only
    because this API is expected to run behind exactly one trusted proxy that
    appends to the header. Note plainly what that means: a client can put
    anything in ``X-Forwarded-For``, so this value is *not* an authentication
    signal. It is used for rate-limit bucketing and audit context, where the
    worst case of a forged value is that an attacker spreads their own attempts
    across buckets — which the per-account limit, keyed on the email rather than
    the IP, already covers.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    return request.client.host if request.client else "unknown"


ClientIp = Annotated[str, Depends(client_ip)]


def get_rate_limiter(request: Request) -> RateLimiter:
    """The application-wide limiter, built once at startup."""
    return request.app.state.rate_limiter


Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def enforce(verdict: RateLimitVerdict) -> None:
    """
    Raise 429 when a limit is exceeded.

    ``Retry-After`` is included because a well-behaved client should back off
    rather than hammer, and the header is the standard way to say how long. The
    body says nothing about which limit was hit or how much budget remains — a
    limiter that reports its own state precisely is a limiter that can be
    mapped and worked around.
    """
    if verdict.allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please wait and try again.",
        headers={"Retry-After": str(verdict.retry_after_seconds)},
    )
