"""
The credit ledger and reservations.

**Feature-flagged.** ``PHOTOFLOW_CREDITS_ENABLED`` defaults to false and no
Phase 2 endpoint reads or writes these tables. They exist now so that the
migration history stays linear: adding tables to an empty database is free,
adding them later against a database holding real customers is a maintenance
window. Pricing is not finalised, so nothing here assumes a rate.

**The ledger is append-only.** A balance is ``SUM(amount)``. A correction is a
new ``ADJUSTMENT`` row, never an UPDATE of an existing one -- that is what makes
a billing dispute with a customer answerable. ``balance_after`` is materialised
so the common "what is my balance" read is a single indexed row rather than an
aggregate over the customer's whole history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, uuid_pk
from app.models.enums import CreditTransactionType, ReservationStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.user import User


class CreditTransaction(Base):
    """
    One immutable ledger entry.

    ``amount`` is signed: positive grants, negative spends. There is no
    ``updated_at`` because a row is never updated.

    ``reference_id`` carries the payment provider's id, or the client's
    idempotency key. The partial unique index on ``(user_id, reference_id)``
    added by the migration makes double-crediting impossible at the database
    level: a retried webhook after a network timeout cannot pay twice even if
    the retry logic above it has a bug. Idempotency belongs in a constraint, not
    in application code that can be refactored away.
    """

    __tablename__ = "credit_transactions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    license_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("licenses.id", ondelete="SET NULL")
    )
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credit_reservations.id", ondelete="SET NULL")
    )

    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_type: Mapped[CreditTransactionType] = mapped_column(
        Enum(CreditTransactionType, name="credit_transaction_type"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    reference_id: Mapped[str | None] = mapped_column(String(255))
    balance_after: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    user: Mapped[User] = relationship(back_populates="credit_transactions")

    __table_args__ = (
        Index("ix_credit_transactions_user_id_created_at", "user_id", "created_at"),
        Index("ix_credit_transactions_license_id", "license_id"),
        # Idempotency: at most one row per (user, external reference).
        Index(
            "uq_credit_transactions_user_reference",
            "user_id",
            "reference_id",
            unique=True,
            postgresql_where=text("reference_id IS NOT NULL"),
        ),
    )


class CreditReservation(Base):
    """
    A hold placed before local work starts.

    PhotoFlow does its expensive processing on the customer's own machine, which
    means the backend cannot observe the work happening. The pattern is
    ``reserve -> local work -> commit``: the server puts a hold on the credits,
    the client does the job offline, and commits when it finishes.

    ``expires_at`` is not optional and is the point of the whole design. If the
    client crashes, loses power, or is simply closed mid-job, the hold releases
    itself instead of stranding a paying customer's credits with no way to get
    them back short of a support ticket.

    Phase 2 creates the table. The reserve/commit/release endpoints are Phase 4.
    """

    __tablename__ = "credit_reservations"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    license_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("licenses.id", ondelete="SET NULL")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )

    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(ReservationStatus, name="reservation_status"),
        nullable=False,
        default=ReservationStatus.OPEN,
        server_default=ReservationStatus.OPEN.value,
    )
    reference_id: Mapped[str | None] = mapped_column(String(255))
    context: Mapped[dict | None] = mapped_column(JSONB)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="credit_reservations")

    __table_args__ = (
        Index("ix_credit_reservations_user_id_status", "user_id", "status"),
        Index(
            "ix_credit_reservations_open_expiry",
            "expires_at",
            postgresql_where=text("status = 'OPEN'"),
        ),
    )
