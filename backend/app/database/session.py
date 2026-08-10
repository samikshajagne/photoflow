"""
Engine and session management.

Synchronous SQLAlchemy 2.x, deliberately. FastAPI runs a ``def`` endpoint in a
worker threadpool, so a synchronous driver does not block the event loop, and a
licensing/entitlement API is bounded by network round-trips to a handful of
clients rather than by concurrency. In exchange we get an Alembic setup and a
test suite with no event-loop plumbing, which is worth more at this stage than
the throughput an async driver would add. If a future endpoint genuinely needs
async (long-poll, streaming), the swap is ``create_async_engine`` plus an async
``get_db`` -- the models and migrations are unaffected.

``pool_pre_ping`` is on by default because Neon closes idle connections when a
branch scales to zero; without it the first request after an idle period fails
with a stale-connection error.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _connect_args(settings: Settings) -> dict:
    """
    Driver connect arguments.

    Neon requires TLS. psycopg will negotiate it automatically when the URL
    carries ``sslmode=require``; we set a sensible default for any non-local
    host so that a URL pasted without the parameter still connects securely
    rather than silently in the clear.
    """
    target = settings.safe_database_target()
    is_local = target.startswith(("localhost/", "127.0.0.1/", "::1/"))
    if is_local or "sslmode" in settings.database_url:
        return {}
    return {"sslmode": "require"}


def get_engine(settings: Settings | None = None) -> Engine:
    """The process-wide engine, created on first use."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=settings.db_pool_pre_ping,
            echo=settings.db_echo,
            connect_args=_connect_args(settings),
            future=True,
        )
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> sessionmaker[Session]:
    """The process-wide session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(settings),
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )
    return _SessionLocal


def dispose_engine() -> None:
    """Drop the engine and session factory (tests, and application shutdown)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_db() -> Iterator[Session]:
    """
    FastAPI dependency yielding a session per request.

    The session is rolled back and closed on the way out. Endpoints commit
    explicitly; nothing is committed implicitly on a successful response,
    because "the request returned 200 so the write must have happened" is a
    guess we do not want to encode.
    """
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database(timeout_seconds: float = 5.0) -> bool:
    """
    True when the database answers a trivial query.

    Used by the readiness probe. Deliberately returns a bool and swallows the
    exception detail: the caller must not be able to surface a driver error (and
    with it a hostname or username) to an unauthenticated HTTP client. The
    detail is logged instead.
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database readiness check failed")
        return False
