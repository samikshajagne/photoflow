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

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import health as infra_health
from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.database.session import check_database, dispose_engine
from app.errors import register_exception_handlers
from app.logging_config import configure_logging, new_request_id, request_id_var
from app.security.rate_limit import RateLimiter, build_backend
from app.security.signing import SigningService
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
    logger.info(
        "Rate limiting: enabled=%s backend=%s | entitlement signing: %s",
        settings.rate_limit_enabled,
        settings.rate_limit_backend,
        "configured" if app.state.signing.available else "not configured",
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
    # Built once per application, not per request: an in-memory limiter that is
    # rebuilt on each call counts to one forever.
    app.state.rate_limiter = RateLimiter(build_backend(settings))
    # Constructed at startup so a malformed key is a boot failure with a clear
    # message, rather than a 500 on the first entitlement request in Phase 4.
    app.state.signing = SigningService.from_settings(settings)

    # Trusted hosts. A Host header the application echoes into a redirect or a
    # password-reset link is a cache-poisoning and phishing vector, so in
    # production the set of names this API answers to is explicit. Left open on
    # a laptop, where the host is localhost, an IP, or whatever a tunnel decided.
    if settings.trusted_hosts:
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts
        )

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
    async def limit_request_body(request: Request, call_next):
        """
        Refuse oversized requests before they are read.

        Every Phase 3 endpoint takes a small JSON object; nothing legitimately
        approaches 1 MiB. Checking ``Content-Length`` rejects the honest case
        cheaply. A client that lies about or omits the header is not stopped
        here -- that needs a streaming counter, and in practice the reverse
        proxy in front of this app enforces the real ceiling. This is defence in
        depth, not the only defence, and it is worth being clear about which.
        """
        declared = request.headers.get("content-length")
        if declared and declared.isdigit():
            if int(declared) > settings.max_request_body_bytes:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={
                        "detail": "Request body too large.",
                        "request_id": request_id_var.get(),
                    },
                )
        return await call_next(request)

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
        # This API returns JSON only. A restrictive CSP costs nothing here and
        # blunts the damage if a future endpoint ever reflects HTML.
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        # Browser features this API has no use for.
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        # Note: the ``Server`` header is *not* set here. uvicorn writes its own
        # at the transport layer, after middleware, so setting one here produces
        # two Server headers rather than replacing it. Suppress uvicorn's with
        # `--no-server-header` (or `server_header=False`), which is what the
        # documented run commands do -- see backend/README.md.
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
