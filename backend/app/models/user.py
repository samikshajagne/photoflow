"""Users: the identity every licence, device and credit row hangs off."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database.base import Base, TimestampMixin, uuid_pk
from app.models.enums import UserRole, UserStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.credit import CreditReservation, CreditTransaction
    from app.models.device import Device
    from app.models.license import License
    from app.models.token import RefreshToken


class User(Base, TimestampMixin):
    """
    A PhotoFlow account.

    ``password_hash`` holds an Argon2id digest and nothing else -- there is no
    code path in this package that writes a plaintext password to the database,
    and ``tests/test_security.py`` asserts it.

    ``auth_provider`` / ``auth_provider_id`` are present but unused: they are
    what a later "Sign in with Google" would populate, and having the columns
    now means adding that provider is code rather than a migration against a
    table that by then holds real customers. For a password account,
    ``auth_provider`` is ``"password"`` and ``auth_provider_id`` is NULL.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()

    # Stored lower-cased (see the validator) so a unique index on the plain
    # column is enough and we avoid depending on the citext extension, which
    # not every managed PostgreSQL enables by default.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String(200))

    password_hash: Mapped[str | None] = mapped_column(String(255))
    auth_provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="password", server_default="password"
    )
    auth_provider_id: Mapped[str | None] = mapped_column(String(255))

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.CLIENT,
        server_default=UserRole.CLIENT.value,
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    licenses: Mapped[list[License]] = relationship(
        back_populates="user", cascade="save-update, merge"
    )
    devices: Mapped[list[Device]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    credit_transactions: Mapped[list[CreditTransaction]] = relationship(
        back_populates="user"
    )
    credit_reservations: Mapped[list[CreditReservation]] = relationship(
        back_populates="user"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_users_status", "status"),
        Index("ix_users_role", "role"),
    )

    @validates("email")
    def _normalise_email(self, _key: str, value: str) -> str:
        """Case- and whitespace-insensitive uniqueness, enforced on write."""
        if value is None:
            raise ValueError("email is required")
        normalised = value.strip().lower()
        if not normalised:
            raise ValueError("email must not be blank")
        return normalised

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Deliberately no email: reprs end up in logs and tracebacks.
        return f"<User id={self.id} role={self.role.value} status={self.status.value}>"
