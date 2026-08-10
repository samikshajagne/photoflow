"""
All ORM models.

Importing this package imports every model, which is what makes
``Base.metadata`` complete. Alembic's ``env.py`` and the test fixtures rely on
that: a model that is never imported is invisible to autogenerate and silently
missing from the schema.
"""

from app.database.base import Base
from app.models.audit import FORBIDDEN_METADATA_KEYS, AuditLog
from app.models.credit import CreditReservation, CreditTransaction
from app.models.device import Device
from app.models.enums import (
    ActivationStatus,
    CreditTransactionType,
    LicenseStatus,
    ReleaseStatus,
    ReservationStatus,
    UserRole,
    UserStatus,
)
from app.models.license import License, LicenseActivation
from app.models.release import Release
from app.models.token import RefreshToken
from app.models.user import User

__all__ = [
    "Base",
    "AuditLog",
    "FORBIDDEN_METADATA_KEYS",
    "CreditReservation",
    "CreditTransaction",
    "Device",
    "License",
    "LicenseActivation",
    "Release",
    "RefreshToken",
    "User",
    "ActivationStatus",
    "CreditTransactionType",
    "LicenseStatus",
    "ReleaseStatus",
    "ReservationStatus",
    "UserRole",
    "UserStatus",
]
