"""Refresh-token rotation, session families and reuse detection.

Adds four columns to ``refresh_tokens``:

``session_id``
    The rotation family. Every token descended from a single login shares one,
    so "log this session out everywhere" is one indexed UPDATE rather than a
    walk up a chain of replacements.

``replaced_by_id``
    Set when a token is rotated. Its presence is what turns a spent token from
    merely *refused* into *detectably reused* — presenting one means either the
    legitimate client replayed a request, or someone stole it. Deliberately not
    a foreign key: a self-referential FK makes insert ordering fiddly for no
    benefit, and the value is only ever read for forensics, never joined on in a
    hot path.

``reused_at``
    Stamped the first time an already-rotated token is presented again. That
    event revokes the whole family, so a stolen token buys at most one refresh
    before the real user and the thief are both logged out — which is the point:
    a theft the user notices beats one nobody does.

``revoked_reason``
    Why a token died — ``logout``, ``rotated``, ``reuse_detected``,
    ``user_disabled``. Read during incidents; never contains a token value.

The NOT NULL on ``session_id`` is added in three steps (nullable → backfill →
NOT NULL) rather than in one, because a single-step NOT NULL fails against any
table that already holds rows. There are no rows in production yet — this
backend has never been deployed — but a migration that only works on an empty
table is a trap for whoever runs it second.

Revision ID: 0002_refresh_rotation
Revises: 0001_initial
Create Date: 2026-08-10

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_refresh_rotation"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refresh_tokens", sa.Column("session_id", sa.UUID(), nullable=True)
    )
    # Every pre-existing token becomes its own single-member family. Correct:
    # nothing older was ever rotated, so no two of them share an ancestry.
    op.execute(
        sa.text(
            "UPDATE refresh_tokens SET session_id = gen_random_uuid() "
            "WHERE session_id IS NULL"
        )
    )
    op.alter_column("refresh_tokens", "session_id", nullable=False)

    op.add_column(
        "refresh_tokens", sa.Column("replaced_by_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("reused_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("revoked_reason", sa.String(length=50), nullable=True),
    )
    op.create_index(
        "ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_session_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "revoked_reason")
    op.drop_column("refresh_tokens", "reused_at")
    op.drop_column("refresh_tokens", "replaced_by_id")
    op.drop_column("refresh_tokens", "session_id")
