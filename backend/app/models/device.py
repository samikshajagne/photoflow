"""Devices: the machines a customer's licence seats are bound to."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, uuid_pk
from app.models.enums import ActivationStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.license import LicenseActivation
    from app.models.user import User


class Device(Base, TimestampMixin):
    """
    One installation of PhotoFlow on one machine.

    ``fingerprint`` is the opaque digest produced by
    ``core.licensing.machine_fingerprint()`` on the client -- a SHA-256 over
    hostname, MAC, architecture and OS, truncated. Three things follow from that
    and are worth being explicit about:

    * **It is not a MAC address.** A raw MAC is trivially spoofed, is personal
      data under the DPDP Act and GDPR, and changes when a user docks a laptop
      or enables a VPN adapter. The composite hash is stable across reboots and
      updates, and reveals nothing about the customer.
    * **The client asserts it; the backend decides.** The fingerprint arrives
      over the wire from software running on hardware the customer controls, so
      it can be forged. It is an input to a seat-limit policy, not an
      authentication factor. Anything that grants value must additionally
      require a valid access token.
    * **It is scoped per user.** ``UNIQUE (user_id, fingerprint)`` -- the same
      shared studio machine may legitimately appear under two accounts, and a
      global unique constraint would lock the second customer out.
    """

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    platform: Mapped[str | None] = mapped_column(String(100))
    app_version: Mapped[str | None] = mapped_column(String(50))

    # Its own PostgreSQL enum type rather than sharing "activation_status":
    # two tables sharing one type means neither can gain a member without the
    # other, and a device being retired is a different event from a seat being
    # released even though the words currently coincide.
    status: Mapped[ActivationStatus] = mapped_column(
        Enum(ActivationStatus, name="device_status"),
        nullable=False,
        default=ActivationStatus.ACTIVE,
        server_default=ActivationStatus.ACTIVE.value,
    )

    first_activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="devices")
    activations: Mapped[list[LicenseActivation]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint", name="uq_devices_user_fingerprint"),
        Index("ix_devices_fingerprint", "fingerprint"),
        Index("ix_devices_user_id", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Device id={self.id} platform={self.platform}>"
