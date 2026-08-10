"""
Alembic environment.

Two things this file does beyond the generated template, both of them about not
destroying the wrong database:

1. **The URL comes from ``PHOTOFLOW_DATABASE_URL``, never from ``alembic.ini``.**
   The connection string is a secret and this repository has a public remote.

2. **Production needs an explicit, typed confirmation.** With
   ``PHOTOFLOW_ENVIRONMENT=production`` set, Alembic refuses to run unless
   ``PHOTOFLOW_MIGRATION_CONFIRM=production`` is also set for that one command.
   Pointing a development migration at production is a single stale shell
   variable away, and the damage is not recoverable from a laptop. The guard
   costs one extra word on the one command a month that genuinely targets prod.

Every run prints ``host/database`` (never credentials) before doing anything, so
the target is on screen and wrong ones are noticed before ``head`` is reached.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the `app` package importable when alembic is run from backend/.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Environment, get_settings  # noqa: E402
from app.models import Base  # noqa: E402  (imports every model -> full metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()


def _guard_production() -> None:
    """Refuse to migrate production without an explicit confirmation."""
    if settings.environment is not Environment.PRODUCTION:
        return
    confirm = os.environ.get("PHOTOFLOW_MIGRATION_CONFIRM", "")
    if confirm != "production":
        raise SystemExit(
            "\nRefusing to run migrations against PRODUCTION.\n"
            f"  Target: {settings.safe_database_target()}\n\n"
            "If that is really what you want, re-run the command with:\n"
            "  PHOTOFLOW_MIGRATION_CONFIRM=production alembic upgrade head\n"
        )


def _announce() -> None:
    print(
        f"[alembic] environment={settings.environment.value} "
        f"target={settings.safe_database_target()}",
        file=sys.stderr,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting. ``alembic upgrade head --sql``."""
    _guard_production()
    _announce()
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect and run migrations."""
    _guard_production()
    _announce()

    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = settings.database_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # Wrap the whole upgrade in one transaction: PostgreSQL has
            # transactional DDL, so a migration that fails halfway rolls back
            # entirely instead of leaving a half-built schema.
            transaction_per_migration=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
