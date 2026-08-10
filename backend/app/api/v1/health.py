"""
The client-facing health endpoint, ``GET /api/v1/health``.

Separate from the infrastructure probes in ``app/api/health.py`` on purpose.
The unversioned ``/health`` belongs to the *host* -- Fly, Railway or Render
polls it, and its shape must never change or deployments break. This one belongs
to the *API contract*: the desktop app calls it to check reachability before
attempting a licence validation, and it may gain fields (a maintenance flag, a
minimum supported client version) under normal API versioning rules.

Mixing the two would mean either freezing a useful endpoint forever or breaking
the platform's probe with a routine API change.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.schemas.health import HealthResponse
from app.version import API_VERSION

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="API health")
def api_health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=API_VERSION,
        environment=settings.environment.value,
    )
