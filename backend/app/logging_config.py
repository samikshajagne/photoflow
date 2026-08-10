"""
Structured logging.

Human-readable lines in development, one JSON object per line in production
(``PHOTOFLOW_LOG_JSON=true``) so a hosted log search can filter on
``request_id`` instead of grepping text.

Every request gets an id, returned in the ``X-Request-ID`` response header and
attached to every log record emitted while handling it. When a customer reports
"it failed at 14:32", that id is what turns the report into a single log line.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

# Set per request by RequestContextMiddleware; read by the log filter.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """
    Install handlers on the root logger. Idempotent.

    Note what is *not* configured: no handler writes the settings object, a
    database URL, or a token. Log formatting is one of the easiest places to
    leak a secret, so the formatters here only ever see the message a caller
    passed.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"
            )
        )

    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn duplicates access lines through its own handlers; let ours win.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
