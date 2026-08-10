"""Licences, and the device activations that consume their seats."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, uuid_pk
from app.models.enums import ActivationStatus, LicenseStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.device import Device
    from app.models.user import User


class License(Base, TimestampMixin):
    """
    An entitlement belonging to one user.

    **The key is stored as a hash.** ``key_hash`` is a SHA-256 of the licence key
    and ``key_last4`` is the display tail. A leaked database backup therefore
    does not hand an attacker a set of working keys, and lookup is still a single
    indexed equality on the hash. (SHA-256 rather than Argon2 on purpose: a
    licence key is high-entropy random text, not a human-chosen password, so
    there is nothing to brute-force and we want the lookup to stay cheap.)

    **Expiry is derived, never trusted from ``status``.** ``status`` records an
    administrative decision -- suspended, revoked. Whether a licence has run out
    is a question about ``expires_at`` and the current time, which is what
    :meth:`is_valid_at` answers. A nightly job may materialise ``EXPIRED`` for
    reporting, but no read path may depend on that job having run.

    ``plan`` is free text, not an enum: plans change with pricing decisions, and
    adding "studio" should be a row rather than a migration.
    """

    __tablename__ = "licenses"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_last4: Mapped[str] = mapped_column(String(8), nullable=False, default="")

    product: Mapped[str] = mapped_column(
        String(50), nullable=False, default="photoflow", server_default="photoflow"
    )
    plan: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[LicenseStatus] = mapped_column(
        Enum(LicenseStatus, name="license_status"),
        nullable=False,
        default=LicenseStatus.PENDING,
        server_default=LicenseStatus.PENDING.value,
    )

    activation_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL means perpetual -- matches core/licensing.py, where "" is perpetual.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="licenses")
    activations: Mapped[list[LicenseActivation]] = relationship(
        back_populates="license", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_licenses_user_id", "user_id"),
        Index("ix_licenses_status", "status"),
        Index("ix_licenses_expires_at", "expires_at"),
    )

    def is_valid_at(self, moment: datetime | None = None) -> bool:
        """
        Whether this licence entitles use at ``moment`` (default: now, UTC).

        Checks the administrative status *and* the date window, so a row whose
        ``status`` column was never updated by a batch job still reports
        correctly.
        """
        moment = moment or datetime.now(timezone.utc)
        if self.status in (
            LicenseStatus.REVOKED,
            LicenseStatus.SUSPENDED,
            LicenseStatus.PENDING,
        ):
            return False
        if self.starts_at is not None and moment < self.starts_at:
            return False
        if self.expires_at is not None and moment >= self.expires_at:
            return False
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<License id={self.id} plan={self.plan} status={self.status.value}>"


class LicenseActivation(Base, TimestampMixin):
    """
    One seat of a licence, held by one device.

    Seat counting is ``COUNT(*) WHERE license_id = ? AND status = 'ACTIVE'``.
    The partial unique index created in the initial migration --
    ``(license_id, device_id) WHERE status = 'ACTIVE'`` -- means a device cannot
    hold two live seats on the same licence even if two activation requests race,
    while still allowing a full history of deactivate/reactivate cycles.
    """

    __tablename__ = "license_activations"

    id: Mapped[uuid.UUID] = uuid_pk()
    license_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("licenses.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[ActivationStatus] = mapped_column(
        Enum(ActivationStatus, name="activation_status"),
        nullable=False,
        default=ActivationStatus.ACTIVE,
        server_default=ActivationStatus.ACTIVE.value,
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    license: Mapped[License] = relationship(back_populates="activations")
    device: Mapped[Device] = relationship(back_populates="activations")

    __table_args__ = (
        Index("ix_license_activations_license_id_status", "license_id", "status"),
        Index("ix_license_activations_device_id", "device_id"),
        # Uniqueness is deliberately *partial* -- "one ACTIVE seat per
        # (licence, device)" -- so the full deactivate/reactivate history can be
        # kept. SQLAlchemy's UniqueConstraint has no WHERE clause, so the index
        # is declared here with postgresql_where and created by the migration.
        Index(
            "uq_license_activations_active_seat",
            "license_id",
            "device_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )
