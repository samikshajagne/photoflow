"""
The FastAPI application.

Run it with::

    python -m uvicorn backend.app.main:app --reload --app-dir .

from the repository root, or see ``backend/README.md`` for the full local setup.

The factory pattern (:func:`create_app`) exists so tests can build an app with
different settings without a module-level singleton getting in the way, while
``app`` at the bottom gives uvicorn something to import.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import health as infra_health
from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.database.session import check_database, dispose_engine
from app.errors import register_exception_handlers
from app.logging_config import configure_logging, new_request_id, request_id_var
from app.version import BACKEND_VERSION

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown.

    The database is *checked* at startup but a failure does not abort the boot.
    A licence server that refuses to start because Neon was briefly unreachable
    turns a 30-second blip into a manual redeploy; readiness reporting 503 until
    the database returns is the better failure mode. A missing or unsafe
    *configuration*, by contrast, does abort -- that will not fix itself.
    """
    settings: Settings = app.state.settings
    logger.info(
        "Starting %s (backend %s) in %s mode; database target %s",
        settings.app_name,
        BACKEND_VERSION,
        settings.environment.value,
        settings.safe_database_target(),  # host/db only, never credentials
    )
    if not check_database():
        logger.warning(
            "Database is not reachable at startup; /health/ready will report 503 "
            "until it is."
        )
    try:
        yield
    finally:
        dispose_engine()
        logger.info("Shutdown complete.")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Called once at import, and per-test."""
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version=BACKEND_VERSION,
        lifespan=lifespan,
        # The interactive docs are a complete map of the API surface. Useful on a
        # laptop, an unnecessary gift to a scanner in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings

    # CORS: an explicit allow-list, never "*". The desktop client is not a
    # browser and is unaffected by CORS; this exists for the local admin
    # dashboard, which is the only browser origin that should ever call this API.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            max_age=600,
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Give every request an id, and echo it back."""
        incoming = request.headers.get("X-Request-ID", "")
        # Never trust a client-supplied id verbatim into logs; bound and filter it.
        request_id = (
            incoming[:64]
            if incoming and incoming.replace("-", "").replace("_", "").isalnum()
            else new_request_id()
        )
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(token)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """
        Baseline response headers.

        HSTS is set only in production, where TLS actually terminates in front
        of this app -- sending it from a plain-HTTP dev server would pin
        localhost to HTTPS in the developer's browser and be a nuisance to undo.
        """
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    register_exception_handlers(app)

    # Unversioned infrastructure probes, then the versioned API.
    app.include_router(infra_health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
