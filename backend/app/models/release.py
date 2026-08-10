"""Release metadata for the desktop updater."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, uuid_pk
from app.models.enums import ReleaseStatus


class Release(Base, TimestampMixin):
    """
    One published (or draft) build of PhotoFlow.

    The installer itself is **not** stored here or served by this backend --
    it lives on GitHub Releases, whose bandwidth is free and whose
    ``releases/latest/download/...`` URL is permanent. What this table holds is
    the part that must be trustworthy: the version, the SHA-256, and an Ed25519
    ``signature`` over the manifest. The desktop app carries the corresponding
    public key, so a tampered ``download_url`` or a corrupted download is
    detected on the client before anything is executed.

    ``channel`` is text rather than an enum so adding an "insiders" channel is a
    row, not a migration. Uniqueness is on ``(version, channel)``: the same
    version number may legitimately exist on both stable and beta.
    """

    __tablename__ = "releases"

    id: Mapped[uuid.UUID] = uuid_pk()

    version: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(
        String(30), nullable=False, default="stable", server_default="stable"
    )
    status: Mapped[ReleaseStatus] = mapped_column(
        Enum(ReleaseStatus, name="release_status"),
        nullable=False,
        default=ReleaseStatus.DRAFT,
        server_default=ReleaseStatus.DRAFT.value,
    )

    release_notes: Mapped[str | None] = mapped_column(Text)
    release_notes_url: Mapped[str | None] = mapped_column(String(500))
    download_url: Mapped[str | None] = mapped_column(String(500))
    installer_filename: Mapped[str | None] = mapped_column(String(255))

    sha256: Mapped[str | None] = mapped_column(String(64))
    signature: Mapped[str | None] = mapped_column(Text)

    minimum_supported_version: Mapped[str | None] = mapped_column(String(50))
    critical: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("version", "channel", name="uq_releases_version_channel"),
        Index("ix_releases_channel_status_published_at", "channel", "status", "published_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Release {self.version} ({self.channel}) {self.status.value}>"
