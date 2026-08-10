"""Response models for the health endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """
    Liveness payload.

    Everything here is safe for an unauthenticated caller: the service name and
    API version are already visible in the installer, and the environment name
    is a word, not an address. There is deliberately no hostname, no region, no
    database, no build path, and no dependency versions -- an unauthenticated
    inventory of your stack is a gift to anyone scanning for known CVEs.
    """

    status: str = Field(examples=["ok"])
    service: str = Field(examples=["PhotoFlow API"])
    version: str = Field(examples=["1"])
    environment: str = Field(examples=["development"])


class ReadinessResponse(BaseModel):
    """
    Readiness payload: a per-dependency verdict, never a reason.

    ``database`` is ``"ok"`` or ``"unavailable"`` and nothing else. The driver's
    error message -- which routinely contains the host, port, database name and
    username -- goes to the log.
    """

    status: str = Field(examples=["ok"])
    database: str = Field(examples=["ok"])
