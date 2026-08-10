"""
Error handling that does not leak.

The contract: an unhandled exception returns a generic message and the request
id, and *nothing else*. A stack trace in an HTTP response tells an attacker the
framework, the file layout, the ORM, and often a hostname or a table name --
sometimes a connection string. The trace goes to the log, where the request id
links it back to the customer's report.

FastAPI's default 422 body is kept for validation errors, because it describes
the caller's own input and reveals nothing about the server.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_config import request_id_var

logger = logging.getLogger(__name__)

GENERIC_MESSAGE = "An internal error occurred. Please try again."


def _body(message: str) -> dict:
    return {"detail": message, "request_id": request_id_var.get()}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the handlers. Called once from :func:`app.main.create_app`."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # Deliberate, already-safe messages ("Not authenticated.") pass through.
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Request validation failed.",
                "errors": exc.errors(),
                "request_id": request_id_var.get(),
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def _database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # A driver error string can contain the host, the database and the user.
        logger.exception("Database error handling %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_body("The service is temporarily unavailable."),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error handling %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_body(GENERIC_MESSAGE),
        )
