"""
Declarative base and shared column conventions for every PhotoFlow model.

Two things live here rather than being repeated in each model file:

* **A constraint naming convention.** Without it PostgreSQL invents names like
  ``licenses_user_id_fkey1`` and Alembic autogenerate produces migrations that
  cannot drop what they created. With it, every index, unique constraint and
  foreign key has a deterministic name, so ``downgrade()`` actually works.
* **Timestamp behaviour.** ``created_at``/``updated_at`` default on the database
  side (``now()``), not in Python, so a row inserted by a migration, a psql
  session or the API all get consistent values.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names -- see the module docstring.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by all models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    """
    A UUID4 primary key generated in Python.

    Non-sequential on purpose: licence and user ids appear in URLs and support
    emails, and a sequential id leaks how many customers exist and lets someone
    walk the range. Generated client-side rather than with ``gen_random_uuid()``
    so the value is known before flush, which makes writing an audit-log row in
    the same transaction straightforward.
    """
    return mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    """``created_at`` / ``updated_at``, both database-side."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
