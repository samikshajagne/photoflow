"""
Health endpoints.

Three routes, three different questions, and keeping them separate is what lets
the liveness probe stay honest:

``GET /health``          -- *liveness*. Is this process running? Answers without
                            touching the database, so a database blip does not
                            cause the platform to kill and restart an otherwise
                            healthy app (which makes an outage worse, not better).
``GET /health/ready``    -- *readiness*. Can this process serve traffic, i.e. is
                            the database reachable? Returns 503 when not, so a
                            load balancer stops routing to it.
``GET /api/v1/health``   -- the *application* health the desktop client calls,
                            under the versioned prefix along with every other
                            client endpoint.

None of them reveal the database host, the connection string, the driver, a
stack trace, or which cloud this runs on. ``/health/ready`` answers a boolean and
nothing more; the reason for a failure is in the logs, where it belongs.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.config import get_settings
from app.database.session import check_database
from app.schemas.health import HealthResponse, ReadinessResponse
from app.version import API_VERSION

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health() -> HealthResponse:
    """Process is up. Deliberately does not touch the database."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=API_VERSION,
        environment=settings.environment.value,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"description": "A dependency is unavailable."}},
)
def readiness(response: Response) -> ReadinessResponse:
    """Process is up *and* the database answers."""
    database_ok = check_database()
    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ok" if database_ok else "degraded",
        database="ok" if database_ok else "unavailable",
    )
