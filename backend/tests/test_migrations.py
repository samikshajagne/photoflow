"""
Migration tests.

The ``db`` fixture already proves ``downgrade base`` -> ``upgrade head`` works
from an empty database, since that is how the test schema is built. What these
add is the check that is easy to forget and expensive to discover late: that the
migration and the models have not drifted apart.

Drift is the normal failure mode. Someone adds a column to a model, the tests
pass because their local database was built with ``create_all``, and production
then runs a migration that does not have the column. Building the test schema
from the migration plus asserting no pending autogenerate operations closes both
halves of that gap.
"""

from __future__ import annotations

import os

from sqlalchemy import inspect

from tests.conftest import BACKEND_DIR, requires_database

pytestmark = requires_database

EXPECTED_TABLES = {
    "users",
    "licenses",
    "license_activations",
    "devices",
    "credit_transactions",
    "credit_reservations",
    "releases",
    "refresh_tokens",
    "audit_logs",
    "alembic_version",
}


class TestMigrationSchema:
    def test_every_expected_table_exists(self, engine):
        assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())

    def test_models_and_migration_have_not_drifted(self, engine):
        """
        ``alembic check`` in test form: autogenerate against the migrated
        database must find nothing to do.
        """
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        from app.models import Base

        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            diff = compare_metadata(context, Base.metadata)

        assert diff == [], (
            "Models and the Alembic migration have drifted. Run "
            "`alembic revision --autogenerate -m '...'` and review the result.\n"
            f"{diff}"
        )

    def test_partial_unique_indexes_exist(self, engine):
        """
        The two constraints that carry real weight -- seat limits and payment
        idempotency -- are partial indexes, which a plain UniqueConstraint
        cannot express. Assert they actually landed.
        """
        inspector = inspect(engine)

        activation_indexes = {
            index["name"]
            for index in inspector.get_indexes("license_activations")
        }

        credit_indexes = {
            index["name"]
            for index in inspector.get_indexes("credit_transactions")
        }

        assert "uq_license_activations_active_seat" in activation_indexes
        assert "uq_credit_transactions_user_reference" in credit_indexes

    def test_audit_logs_has_no_foreign_keys(self, engine):
        """Deleting a user must not be able to delete the record of it."""
        assert inspect(engine).get_foreign_keys("audit_logs") == []

    def test_single_head(self):
        """Two heads mean someone branched the migration history by accident."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))

        assert len(ScriptDirectory.from_config(config).get_heads()) == 1


class TestMigrationGuards:
    def test_production_requires_explicit_confirmation(self, monkeypatch):
        """
        Pointing a development migration at Neon production is one stale shell
        variable away, and is not recoverable from a laptop. The guard in
        migrations/env.py is what stands in the way.
        """
        import subprocess
        import sys

        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(BACKEND_DIR),
                "PHOTOFLOW_ENVIRONMENT": "production",
                "PHOTOFLOW_DATABASE_URL": (
                    "postgresql://u:p@ep-prod.neon.tech/photoflow"
                ),
                "PHOTOFLOW_JWT_SECRET": "z" * 48,
                "PHOTOFLOW_API_BASE_URL": "https://api.example.com",
                # Explicit, because the developer's own backend/.env is still
                # read by the subprocess and sets DEBUG=true, which production
                # rejects -- that would abort for the right reason but the wrong
                # one, and the test would pass without ever reaching the guard
                # under test.
                "PHOTOFLOW_DEBUG": "false",
                "PHOTOFLOW_CORS_ORIGINS": "https://admin.example.com",
                "PHOTOFLOW_TRUSTED_HOSTS": "api.example.com",
                "PHOTOFLOW_ALLOW_SINGLE_INSTANCE_RATE_LIMIT": "true",
                # PHOTOFLOW_MIGRATION_CONFIRM deliberately absent.
            }
        )

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode != 0

        combined = result.stdout + result.stderr

        assert "Refusing to run migrations against PRODUCTION" in combined

        # The refusal must name the target without leaking the password.
        assert "ep-prod.neon.tech/photoflow" in combined
        assert "u:p@" not in combined