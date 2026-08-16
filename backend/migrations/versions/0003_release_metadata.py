"""Add product, platform and size_bytes to releases.

Three additive columns, needed so the admin dashboard and public website can
show which product and platform a build is for, and its installer size,
without inventing a second table:

``product``
    Free text, defaulting to ``"photoflow"`` -- mirrors ``licenses.product``,
    added in ``0001_initial`` for the same reason: a second product should be a
    row, not a migration.

``platform``
    Nullable free text (e.g. ``"Windows"``). Left out of
    ``uq_releases_version_channel`` deliberately -- see the docstring on
    ``app.models.release.Release`` for why widening that constraint to include
    a nullable column would silently stop enforcing it at all.

``size_bytes``
    The installer's size in bytes, for display. Nullable because a ``DRAFT``
    release may be registered before the artifact is finished uploading.

All three are backfillable with a default or ``NULL``, so this is a single-step
migration -- there are no rows in production yet (see ``0002``'s note that this
backend has never been deployed), and even if there were, ``product`` backfills
via its ``server_default`` and the other two are nullable.

Revision ID: 0003_release_metadata
Revises: 0002_refresh_rotation
Create Date: 2026-08-15

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_release_metadata"
down_revision: str | None = "0002_refresh_rotation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "releases",
        sa.Column(
            "product",
            sa.String(length=50),
            server_default="photoflow",
            nullable=False,
        ),
    )
    op.add_column(
        "releases",
        sa.Column("platform", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "releases",
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("releases", "size_bytes")
    op.drop_column("releases", "platform")
    op.drop_column("releases", "product")
