"""
Shared fixtures for the backend test suite.

**The test database is never the development or production database.** Tests
require ``PHOTOFLOW_TEST_DATABASE_URL`` to be set and will skip the whole
database-backed suite if it is not, rather than falling back to
``PHOTOFLOW_DATABASE_URL``. A fallback is exactly how a test run drops the
tables in a database someone was using -- and a fallback to a *Neon production*
URL, which is what a stale shell variable would give you, is unrecoverable from
a laptop. Refusing to guess is the whole point.

The schema is built by running the real Alembic migration, not
``Base.metadata.create_all()``. Those two can drift, and the migration is what
production will actually run -- so the migration is what the tests exercise.
Each test then runs inside a transaction that is rolled back, so tests neither
see each other's rows nor pay to rebuild the schema.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_DATABASE_URL = os.environ.get("PHOTOFLOW_TEST_DATABASE_URL", "").strip()

requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason=(
        "Set PHOTOFLOW_TEST_DATABASE_URL to a throwaway PostgreSQL database to "
        "run the database-backed tests. It must not be your development or "
        "production database -- the schema is dropped and rebuilt."
    ),
)


def _configure_test_environment() -> None:
    """Point the settings object at the test database before anything imports it."""
    os.environ["PHOTOFLOW_ENVIRONMENT"] = "test"
    os.environ["PHOTOFLOW_DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["PHOTOFLOW_JWT_SECRET"] = "test-secret-that-is-long-enough-to-pass"
    os.environ["PHOTOFLOW_CORS_ORIGINS"] = "http://localhost:8787"
    os.environ["PHOTOFLOW_LOG_LEVEL"] = "WARNING"


@pytest.fixture(scope="session", autouse=True)
def _test_environment() -> Iterator[None]:
    if TEST_DATABASE_URL:
        _configure_test_environment()
    yield


@pytest.fixture(scope="session")
def engine(_test_environment):
    """A session-wide engine bound to the test database, with the schema built."""
    if not TEST_DATABASE_URL:
        pytest.skip("PHOTOFLOW_TEST_DATABASE_URL is not set")

    from alembic import command
    from alembic.config import Config

    from app.config import reset_settings_cache
    from app.database.session import dispose_engine, get_engine

    reset_settings_cache()
    dispose_engine()

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))

    # Start from an empty schema so a previous failed run cannot mask a
    # migration bug, then apply the migration exactly as production will.
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")

    engine = get_engine()
    yield engine

    command.downgrade(alembic_cfg, "base")
    dispose_engine()


@pytest.fixture
def db(engine) -> Iterator[Session]:  # noqa: F821 - quoted for lazy import
    """
    A session wrapped in a transaction that is rolled back after each test.

    Nested inside an outer transaction on a dedicated connection, so even code
    under test that calls ``commit()`` does not persist anything beyond the test.
    """
    from sqlalchemy.orm import Session

    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db):
    """
    A TestClient whose requests use the test's rolled-back session.

    Overriding ``get_db`` rather than letting the app open its own connection is
    what keeps request-scoped writes inside the test transaction.
    """
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.database.session import get_db
    from app.main import create_app

    app = create_app(get_settings())
    app.dependency_overrides[get_db] = lambda: db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Object factories
# --------------------------------------------------------------------------- #
@pytest.fixture
def make_user(db):
    """Create a persisted user. Unique email per call, so tests cannot collide."""
    from app.models.enums import UserRole, UserStatus
    from app.models.user import User
    from app.security.passwords import hash_password

    def _make(
        *,
        email: str | None = None,
        password: str = "correct-horse-battery-staple",
        role: UserRole = UserRole.CLIENT,
        status: UserStatus = UserStatus.ACTIVE,
        name: str = "Test User",
    ) -> User:
        user = User(
            email=email or f"user-{uuid.uuid4().hex[:12]}@example.test",
            name=name,
            password_hash=hash_password(password),
            role=role,
            status=status,
        )
        db.add(user)
        db.flush()
        return user

    return _make


@pytest.fixture
def make_license(db):
    """Create a persisted licence for a user."""
    import hashlib

    from app.models.enums import LicenseStatus
    from app.models.license import License

    def _make(user, *, key: str | None = None, plan: str = "monthly", **kwargs):
        key = key or f"PF-{uuid.uuid4().hex.upper()}"
        licence = License(
            user_id=user.id,
            key_hash=hashlib.sha256(key.encode()).hexdigest(),
            key_last4=key[-4:],
            plan=plan,
            status=kwargs.pop("status", LicenseStatus.ACTIVE),
            **kwargs,
        )
        db.add(licence)
        db.flush()
        return licence

    return _make


@pytest.fixture
def make_device(db):
    """Create a persisted device for a user."""
    from app.models.device import Device

    def _make(user, *, fingerprint: str | None = None, **kwargs):
        device = Device(
            user_id=user.id,
            fingerprint=fingerprint or uuid.uuid4().hex,
            platform=kwargs.pop("platform", "Windows 11"),
            **kwargs,
        )
        db.add(device)
        db.flush()
        return device

    return _make
