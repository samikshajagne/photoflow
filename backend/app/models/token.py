"""Refresh tokens, stored as hashes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, uuid_pk

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.user import User


class RefreshToken(Base):
    """
    A long-lived credential that can mint short-lived access tokens.

    Only the SHA-256 of the token is stored. If the database leaks, the rows are
    useless: an attacker has the digests, not the bearer tokens. This is the same
    reason a password is hashed, and it costs nothing here because lookup is by
    exact digest.

    Rows are kept after revocation rather than deleted, so "was this session
    revoked, and when" remains answerable during an incident.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    def is_usable_at(self, moment: datetime | None = None) -> bool:
        """Not revoked and not past its expiry."""
        moment = moment or datetime.now(timezone.utc)
        return self.revoked_at is None and moment < self.expires_at
