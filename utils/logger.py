"""
Logging setup for PhotoFlow.

Provides one entry point — ``setup_logging()`` — that configures the
``photoflow`` logger hierarchy with a rotating file handler and a console
handler, plus a ``get_logger()`` helper for modules to fetch a properly
namespaced child logger (e.g. ``photoflow.core.scanner``) that inherits
those handlers.

``setup_logging()`` is idempotent: calling it more than once (which
happens naturally in tests, and would happen if a future Streamlit
rerun re-entered application startup) replaces previously attached
handlers instead of stacking duplicates, so log lines never get
written twice.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from utils.config import LoggingConfig

_ROOT_LOGGER_NAME = "photoflow"
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_FILENAME = "photoflow.log"


def setup_logging(config: LoggingConfig) -> logging.Logger:
    """
    Configure the ``photoflow`` root logger and attach handlers.

    Creates ``config.log_dir`` if it doesn't exist, attaches a
    size-based ``RotatingFileHandler`` writing to
    ``<log_dir>/photoflow.log`` and a ``StreamHandler`` for the console,
    both using the same formatter.

    Args:
        config: Validated logging configuration (see ``utils.config``).

    Returns:
        The configured ``photoflow`` logger (also accessible later via
        ``logging.getLogger("photoflow")`` or ``get_logger(__name__)``).
    """
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(config.level)
    # Don't also hand records to the root logger's own handlers (e.g.
    # pytest's log capture) once we've attached our own.
    logger.propagate = False

    # Idempotency: drop any handlers from a previous call before adding
    # new ones, closing each so its file descriptor is released cleanly.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / _LOG_FILENAME

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_file,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger namespaced under ``photoflow``.

    Call this with ``__name__`` from any module, e.g. from
    ``core/scanner.py`` this returns a logger named
    ``photoflow.core.scanner``, which inherits the handlers attached by
    ``setup_logging()`` via Python's standard logger hierarchy.

    Args:
        name: Typically a module's ``__name__``. The special value
            ``"__main__"`` (what ``__name__`` equals when a script is
            run directly) is mapped to the friendlier suffix ``"main"``.
    """
    suffix = "main" if name == "__main__" else name
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{suffix}")
