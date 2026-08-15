"""Business logic for PhotoFlow licence activation and validation."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.enums import ActivationStatus, LicenseStatus
from app.models.license import License, LicenseActivation
from app.services.audit import record


class LicensingError(Exception):
    """Base exception for expected licensing failures."""


class LicenseNotFoundError(LicensingError):
    """The supplied license key does not identify a usable license."""


class LicenseOwnershipError(LicensingError):
    """The license does not belong to the authenticated user."""


class LicenseInvalidError(LicensingError):
    """The license exists but cannot currently be used."""


class ActivationLimitError(LicensingError):
    """No activation seat is available."""


class DeviceActivationError(LicensingError):
    """The requested device cannot be activated."""


@dataclass(frozen=True)
class ActivationResult:
    license: License
    device: Device
    activation: LicenseActivation
    reused: bool


@dataclass(frozen=True)
class ValidationResult:
    license: License
    device: Device
    activation: LicenseActivation


_KEY_SEPARATOR_RE = re.compile(r"[\s\-]+")


def normalize_license_key(key: str) -> str:
    """
    Normalize a license key before hashing.

    Spaces and hyphens are ignored and the key is upper-cased so users can
    enter the same key in a few common human-friendly formats.
    """
    if not isinstance(key, str):
        return ""

    normalized = _KEY_SEPARATOR_RE.sub("", key).strip().upper()

    if not normalized:
        return ""

    return normalized


def hash_license_key(key: str) -> str:
    """Return the SHA-256 hash used for database lookup."""
    normalized = normalize_license_key(key)

    if not normalized:
        return ""

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def license_last4(key: str) -> str:
    """Return the final four display characters of a normalized key."""
    normalized = normalize_license_key(key)
    return normalized[-4:] if len(normalized) >= 4 else normalized


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_valid_license(license: License, *, now: datetime) -> None:
    """Raise if the license cannot currently grant access."""
    if license.status == LicenseStatus.REVOKED:
        raise LicenseInvalidError("This license has been revoked.")

    if license.status == LicenseStatus.SUSPENDED:
        raise LicenseInvalidError("This license has been suspended.")

    if license.status == LicenseStatus.PENDING:
        raise LicenseInvalidError("This license is not active yet.")

    if license.starts_at is not None and now < license.starts_at:
        raise LicenseInvalidError("This license is not active yet.")

    if license.expires_at is not None and now >= license.expires_at:
        raise LicenseInvalidError("This license has expired.")


def find_license(
    db: Session,
    key: str,
) -> License:
    """Find a license by its normalized key hash."""
    key_hash = hash_license_key(key)

    if not key_hash:
        raise LicenseNotFoundError("Enter a valid license key.")

    license = db.scalar(
        select(License).where(License.key_hash == key_hash)
    )

    if license is None:
        raise LicenseNotFoundError("The license key is invalid.")

    return license


def _get_or_create_device(
    db: Session,
    *,
    user_id: uuid.UUID,
    fingerprint: str,
    platform: str | None = None,
    app_version: str | None = None,
    name: str | None = None,
    now: datetime,
) -> Device:
    """Get a user's device by fingerprint, or register it."""
    fingerprint = (fingerprint or "").strip()

    if not fingerprint:
        raise DeviceActivationError("A device fingerprint is required.")

    device = db.scalar(
        select(Device).where(
            Device.user_id == user_id,
            Device.fingerprint == fingerprint,
        )
    )

    if device is None:
        device = Device(
            user_id=user_id,
            fingerprint=fingerprint,
            name=name,
            platform=platform,
            app_version=app_version,
            status=ActivationStatus.ACTIVE,
            first_activated_at=now,
            last_seen_at=now,
        )
        db.add(device)
        db.flush()
        return device

    if device.status == ActivationStatus.REVOKED:
        raise DeviceActivationError("This device has been revoked.")

    device.status = ActivationStatus.ACTIVE
    device.last_seen_at = now

    if platform:
        device.platform = platform

    if app_version:
        device.app_version = app_version

    if name:
        device.name = name

    if device.first_activated_at is None:
        device.first_activated_at = now

    return device


def activate_license(
    db: Session,
    *,
    user_id: uuid.UUID,
    key: str,
    fingerprint: str,
    platform: str | None = None,
    app_version: str | None = None,
    device_name: str | None = None,
    actor_ip: str | None = None,
) -> ActivationResult:
    """
    Activate a license for an authenticated user's device.

    The license row is locked during seat allocation so concurrent activation
    requests cannot both observe the same free seat.
    """
    key_hash = hash_license_key(key)

    if not key_hash:
        raise LicenseNotFoundError("Enter a valid license key.")

    now = _utcnow()

    license = db.scalar(
        select(License)
        .where(License.key_hash == key_hash)
        .with_for_update()
    )

    if license is None:
        raise LicenseNotFoundError("The license key is invalid.")

    if license.user_id != user_id:
        raise LicenseOwnershipError(
            "This license does not belong to the authenticated account."
        )

    _require_valid_license(license, now=now)

    device = _get_or_create_device(
        db,
        user_id=user_id,
        fingerprint=fingerprint,
        platform=platform,
        app_version=app_version,
        name=device_name,
        now=now,
    )

    # If this exact license/device already has an active seat, activation is
    # idempotent. Do not consume another seat.
    active_activation = db.scalar(
        select(LicenseActivation).where(
            LicenseActivation.license_id == license.id,
            LicenseActivation.device_id == device.id,
            LicenseActivation.status == ActivationStatus.ACTIVE,
        )
    )

    if active_activation is not None:
        active_activation.activated_at = (
            active_activation.activated_at or now
        )

        license.last_validated_at = now

        record(
            db,
            action="LICENSE_ACTIVATION_REUSED",
            actor_user_id=user_id,
            actor_ip=actor_ip,
            target_type="license",
            target_id=str(license.id),
            metadata={
                "device_id": str(device.id),
                "plan": license.plan,
            },
        )

        return ActivationResult(
            license=license,
            device=device,
            activation=active_activation,
            reused=True,
        )

    active_seats = db.scalar(
        select(func.count(LicenseActivation.id)).where(
            LicenseActivation.license_id == license.id,
            LicenseActivation.status == ActivationStatus.ACTIVE,
        )
    ) or 0

    if active_seats >= license.activation_limit:
        raise ActivationLimitError(
            "This license has reached its activation limit."
        )

    # Reactivate an existing deactivated activation where possible, preserving
    # activation history instead of creating unnecessary rows.
    previous_activation = db.scalar(
        select(LicenseActivation)
        .where(
            LicenseActivation.license_id == license.id,
            LicenseActivation.device_id == device.id,
            LicenseActivation.status == ActivationStatus.DEACTIVATED,
        )
        .order_by(LicenseActivation.deactivated_at.desc())
    )

    if previous_activation is not None:
        activation = previous_activation
        activation.status = ActivationStatus.ACTIVE
        activation.activated_at = now
        activation.deactivated_at = None
    else:
        activation = LicenseActivation(
            license_id=license.id,
            device_id=device.id,
            status=ActivationStatus.ACTIVE,
            activated_at=now,
        )
        db.add(activation)
        db.flush()

    device.status = ActivationStatus.ACTIVE
    device.last_seen_at = now
    license.last_validated_at = now

    record(
        db,
        action="LICENSE_ACTIVATED",
        actor_user_id=user_id,
        actor_ip=actor_ip,
        target_type="license",
        target_id=str(license.id),
        metadata={
            "device_id": str(device.id),
            "plan": license.plan,
            "activation_limit": license.activation_limit,
        },
    )

    return ActivationResult(
        license=license,
        device=device,
        activation=activation,
        reused=False,
    )


def validate_license(
    db: Session,
    *,
    user_id: uuid.UUID,
    key: str,
    fingerprint: str,
    platform: str | None = None,
    app_version: str | None = None,
    actor_ip: str | None = None,
) -> ValidationResult:
    """
    Validate an existing activation.

    Validation requires both:
    - an authenticated owner
    - an active license/device activation
    """
    license = find_license(db, key)

    if license.user_id != user_id:
        raise LicenseOwnershipError(
            "This license does not belong to the authenticated account."
        )

    now = _utcnow()
    _require_valid_license(license, now=now)

    fingerprint = (fingerprint or "").strip()

    if not fingerprint:
        raise DeviceActivationError("A device fingerprint is required.")

    device = db.scalar(
        select(Device).where(
            Device.user_id == user_id,
            Device.fingerprint == fingerprint,
        )
    )

    if device is None:
        raise DeviceActivationError(
            "This device is not activated for the license."
        )

    if device.status == ActivationStatus.REVOKED:
        raise DeviceActivationError("This device has been revoked.")

    activation = db.scalar(
        select(LicenseActivation).where(
            LicenseActivation.license_id == license.id,
            LicenseActivation.device_id == device.id,
            LicenseActivation.status == ActivationStatus.ACTIVE,
        )
    )

    if activation is None:
        raise DeviceActivationError(
            "This device is not activated for the license."
        )

    device.last_seen_at = now

    if platform:
        device.platform = platform

    if app_version:
        device.app_version = app_version

    license.last_validated_at = now

    record(
        db,
        action="LICENSE_VALIDATED",
        actor_user_id=user_id,
        actor_ip=actor_ip,
        target_type="license",
        target_id=str(license.id),
        metadata={
            "device_id": str(device.id),
            "plan": license.plan,
        },
    )

    return ValidationResult(
        license=license,
        device=device,
        activation=activation,
    )


def deactivate_license(
    db: Session,
    *,
    user_id: uuid.UUID,
    license_id: uuid.UUID,
    fingerprint: str,
    actor_ip: str | None = None,
) -> None:
    """Release a license seat for one authenticated user's device."""
    now = _utcnow()

    license = db.scalar(
        select(License).where(License.id == license_id)
    )

    if license is None:
        raise LicenseNotFoundError("License not found.")

    if license.user_id != user_id:
        raise LicenseOwnershipError(
            "This license does not belong to the authenticated account."
        )

    device = db.scalar(
        select(Device).where(
            Device.user_id == user_id,
            Device.fingerprint == (fingerprint or "").strip(),
        )
    )

    if device is None:
        raise DeviceActivationError("Device not found.")

    activation = db.scalar(
        select(LicenseActivation).where(
            LicenseActivation.license_id == license.id,
            LicenseActivation.device_id == device.id,
            LicenseActivation.status == ActivationStatus.ACTIVE,
        )
    )

    if activation is None:
        return

    activation.status = ActivationStatus.DEACTIVATED
    activation.deactivated_at = now

    device.last_seen_at = now

    record(
        db,
        action="LICENSE_DEACTIVATED",
        actor_user_id=user_id,
        actor_ip=actor_ip,
        target_type="license",
        target_id=str(license.id),
        metadata={
            "device_id": str(device.id),
        },
    )


def create_license_key(prefix: str = "PF") -> str:
    """
    Generate a high-entropy human-enterable license key.

    This helper generates the raw key. The caller is responsible for storing
    only its hash in the database.
    """
    import secrets

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = [
        "".join(secrets.choice(alphabet) for _ in range(5))
        for _ in range(4)
    ]

    return f"{prefix.upper()}-" + "-".join(groups)


def build_license(
    *,
    user_id: uuid.UUID,
    key: str,
    plan: str,
    activation_limit: int = 1,
    starts_at: datetime | None = None,
    expires_at: datetime | None = None,
    status: LicenseStatus = LicenseStatus.ACTIVE,
    product: str = "photoflow",
    notes: str | None = None,
) -> License:
    """
    Build a License ORM object without adding or committing it.

    Only the key hash is stored.
    """
    normalized = normalize_license_key(key)

    if not normalized:
        raise LicenseNotFoundError("A license key is required.")

    if activation_limit < 1:
        raise ValueError("activation_limit must be at least 1.")

    return License(
        user_id=user_id,
        key_hash=hash_license_key(normalized),
        key_last4=license_last4(normalized),
        product=product,
        plan=plan,
        status=status,
        activation_limit=activation_limit,
        starts_at=starts_at,
        expires_at=expires_at,
        notes=notes,
    )
