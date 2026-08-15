"""
Shared request-scoped dependencies.

Two things every security-relevant endpoint needs and neither of which should be
re-derived inline: the caller's IP address, and the rate limiter.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings
from app.security.rate_limit import RateLimiter, RateLimitVerdict


def client_ip(request: Request) -> str:
    """
    Return a valid IP address for rate limiting and audit logging.

    X-Forwarded-For is accepted only when its first value is a valid IP.
    Invalid/non-IP client addresses (such as Starlette's ``testclient``)
    fall back to a valid unspecified IPv4 address so PostgreSQL's INET
    column can always accept the value.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        try:
            ipaddress.ip_address(first)
            return first
        except ValueError:
            pass

    host = request.client.host if request.client else ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return "0.0.0.0"


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
    rather than hammer, and the header is the standard way to say how long.
    The body says nothing about which limit was hit or how much budget remains.
    """
    if verdict.allowed:
        return

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please wait and try again.",
        headers={"Retry-After": str(verdict.retry_after_seconds)},
    )