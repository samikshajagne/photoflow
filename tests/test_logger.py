"""
Unit tests for utils.logger.

Verifies that setup_logging() wires up a working rotating file handler
and console handler, that log records actually reach the log file, and
that calling it more than once doesn't accumulate duplicate handlers.
"""

import logging
import logging.handlers
from pathlib import Path

from utils.config import LoggingConfig
from utils.logger import get_logger, setup_logging


def _make_logging_config(tmp_path: Path, level: str = "DEBUG") -> LoggingConfig:
    return LoggingConfig(
        level=level,
        log_dir=str(tmp_path / "logs"),
        max_bytes=1_000_000,
        backup_count=1,
    )


def test_setup_logging_creates_log_directory_and_file(tmp_path: Path):
    config = _make_logging_config(tmp_path)

    logger = setup_logging(config)
    test_logger = get_logger("tests.test_logger")
    test_logger.info("hello from test")
    for handler in logger.handlers:
        handler.flush()

    log_file = Path(config.log_dir) / "photoflow.log"
    assert log_file.exists()
    assert "hello from test" in log_file.read_text(encoding="utf-8")


def test_setup_logging_is_idempotent(tmp_path: Path):
    config = _make_logging_config(tmp_path)

    setup_logging(config)
    setup_logging(config)

    logger = logging.getLogger("photoflow")
    file_handlers = [
        h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    console_handlers = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
    ]

    assert len(file_handlers) == 1
    assert len(console_handlers) == 1


def test_get_logger_returns_namespaced_logger():
    logger = get_logger("core.scanner")
    assert logger.name == "photoflow.core.scanner"


def test_get_logger_maps_dunder_main_to_main():
    logger = get_logger("__main__")
    assert logger.name == "photoflow.main"


def test_logger_respects_configured_level(tmp_path: Path):
    config = _make_logging_config(tmp_path, level="WARNING")

    logger = setup_logging(config)
    test_logger = get_logger("tests.test_logger_level")
    test_logger.info("this should be suppressed")
    test_logger.warning("this should appear")
    for handler in logger.handlers:
        handler.flush()

    log_file = Path(config.log_dir) / "photoflow.log"
    contents = log_file.read_text(encoding="utf-8")
    assert "this should be suppressed" not in contents
    assert "this should appear" in contents
