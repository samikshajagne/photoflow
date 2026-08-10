"""The administrative and security audit trail."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

# Keys that must never appear in audit metadata. Enforced by
# :func:`app.services.audit.scrub_metadata`, which every writer goes through.
FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "new_password",
        "old_password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "openai_api_key",
        "secret",
        "jwt_secret",
        "private_key",
        "signing_key",
        "authorization",
        "database_url",
        "license_key",
    }
)


class AuditLog(Base):
    """
    An append-only record of who did what.

    **Deliberately not foreign-keyed.** ``target_type``/``target_id`` are loose
    text. If deleting a user cascaded away the record that the user was deleted,
    the audit trail would be worthless exactly when it matters. The cost is that
    a target id may point at a row that no longer exists, which is correct: the
    event still happened.

    ``bigserial`` rather than a UUID here, because this is the one table that is
    written far more often than it is read and is naturally ordered by time --
    and nothing external references an audit row by id.

    ``metadata_json`` must never contain passwords, tokens, API keys or licence
    keys; :data:`FORBIDDEN_METADATA_KEYS` and the scrubber in
    ``app/services/audit.py`` enforce that, with a test to match.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Nullable: a null actor means the system did it (a scheduled job, a webhook).
    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_ip: Mapped[str | None] = mapped_column(INET)

    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(100))

    # "metadata" is reserved by SQLAlchemy's declarative API, so the attribute is
    # renamed while the column keeps the obvious name.
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_actor_user_id", "actor_user_id"),
        Index("ix_audit_logs_target_type_target_id", "target_type", "target_id"),
        Index("ix_audit_logs_action", "action"),
    )
