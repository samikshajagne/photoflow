"""
Enumerations shared by the models.

These are stored as native PostgreSQL enum types. The trade-off against a plain
``text`` column with a CHECK constraint: a native enum makes an invalid value
impossible even from psql, at the cost of needing a migration to add a member.
For values that change with business decisions rather than code -- licence
*plans*, release *channels* -- we deliberately use ``text`` instead, so adding
"studio" or "insiders" is a row, not a deployment.
"""

from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    CLIENT = "CLIENT"


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    PENDING = "PENDING"


class LicenseStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class ActivationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DEACTIVATED = "DEACTIVATED"
    REVOKED = "REVOKED"


class CreditTransactionType(str, enum.Enum):
    """
    Ledger entry kinds.

    ``RESERVATION``/``COMMIT``/``RELEASE`` exist because PhotoFlow does its
    expensive work locally: the client reserves credits before starting, does
    the work offline, then commits (or the reservation expires and is released).
    Phase 2 creates the vocabulary; the workflow itself is Phase 4.
    """

    PURCHASE = "PURCHASE"
    ADMIN_GRANT = "ADMIN_GRANT"
    USAGE = "USAGE"
    REFUND = "REFUND"
    BONUS = "BONUS"
    ADJUSTMENT = "ADJUSTMENT"
    RESERVATION = "RESERVATION"
    COMMIT = "COMMIT"
    RELEASE = "RELEASE"
    EXPIRY = "EXPIRY"


class ReservationStatus(str, enum.Enum):
    OPEN = "OPEN"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class ReleaseStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    YANKED = "YANKED"
