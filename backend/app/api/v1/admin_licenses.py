"""
Administrative license management.

These endpoints are used by the private PhotoFlow admin dashboard.
Every endpoint requires an ADMIN account.

Client licensing operations remain in ``licenses.py``. This module is
deliberately separate because administrators manage entitlements while
customers activate their own seats.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ClientIp
from app.auth.dependencies import AdminUser
from app.database.session import get_db
from app.models.device import Device
from app.models.enums import ActivationStatus, LicenseStatus
from app.models.license import License, LicenseActivation
from app.models.user import User
from app.services import audit
from app.services.audit import AuditAction
from app.services.licensing import (
    LicenseNotFoundError,
    build_license,
    create_license_key,
    hash_license_key,
    license_last4,
)

router = APIRouter(prefix="/licenses", tags=["admin-licenses"])

DbSession = Annotated[Session, Depends(get_db)]


class CreateLicenseRequest(BaseModel):
    user_id: uuid.UUID
    plan: str = Field(min_length=1, max_length=50)
    activation_limit: int = Field(default=1, ge=1, le=100)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class LicenseSummary(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    user_email: str
    key_last4: str
    plan: str
    product: str
    status: LicenseStatus
    activation_limit: int
    active_devices: int
    starts_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class CreatedLicenseResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    user_email: str
    key: str
    key_last4: str
    plan: str
    product: str
    status: LicenseStatus
    activation_limit: int
    starts_at: datetime | None
    expires_at: datetime | None
    notes: str | None


class LicenseDeviceResponse(BaseModel):
    device_id: uuid.UUID
    fingerprint: str
    name: str | None
    platform: str | None
    app_version: str | None
    status: ActivationStatus
    activated_at: datetime | None
    last_seen_at: datetime | None


def _require_license(db: Session, license_id: uuid.UUID) -> License:
    license = db.get(License, license_id)
    if license is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="License not found.",
        )
    return license


def _require_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return user


@router.post(
    "",
    response_model=CreatedLicenseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a license",
)
def create_license(
    payload: CreateLicenseRequest,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> CreatedLicenseResponse:
    """Create a new license for an existing customer."""

    user = _require_user(db, payload.user_id)

    if user.status.value != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot create a license for a disabled account.",
        )

    if payload.expires_at is not None and payload.starts_at is not None:
        if payload.expires_at <= payload.starts_at:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="expires_at must be later than starts_at.",
            )

    key = create_license_key()

    license = build_license(
        user_id=user.id,
        key=key,
        plan=payload.plan,
        activation_limit=payload.activation_limit,
        starts_at=payload.starts_at,
        expires_at=payload.expires_at,
        status=LicenseStatus.ACTIVE,
        product="photoflow",
        notes=payload.notes,
    )

    db.add(license)
    db.flush()

    audit.record(
        db,
        action="LICENSE_CREATED",
        actor_user_id=admin.id,
        actor_ip=ip,
        target_type="license",
        target_id=str(license.id),
        metadata={
            "user_id": str(user.id),
            "plan": license.plan,
            "activation_limit": license.activation_limit,
            "key_last4": license.key_last4,
        },
    )

    db.commit()

    return CreatedLicenseResponse(
        id=license.id,
        user_id=user.id,
        user_name=user.name,
        user_email=user.email,
        key=key,
        key_last4=license.key_last4,
        plan=license.plan,
        product=license.product,
        status=license.status,
        activation_limit=license.activation_limit,
        starts_at=license.starts_at,
        expires_at=license.expires_at,
        notes=license.notes,
    )


@router.get(
    "",
    response_model=list[LicenseSummary],
    summary="List licenses",
)
def list_licenses(
    db: DbSession,
    admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    user_id: uuid.UUID | None = None,
    license_status: LicenseStatus | None = Query(
        default=None,
        alias="status",
    ),
) -> list[LicenseSummary]:
    """List licenses for the admin dashboard."""

    conditions = []

    if user_id is not None:
        conditions.append(License.user_id == user_id)

    if license_status is not None:
        conditions.append(License.status == license_status)

    rows = (
        db.execute(
            select(License, User)
            .join(User, User.id == License.user_id)
            .where(*conditions)
            .order_by(License.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .all()
    )

    results: list[LicenseSummary] = []

    for license, user in rows:
        active_devices = db.execute(
            select(func.count())
            .select_from(LicenseActivation)
            .where(
                LicenseActivation.license_id == license.id,
                LicenseActivation.status == ActivationStatus.ACTIVE,
            )
        ).scalar_one()

        results.append(
            LicenseSummary(
                id=license.id,
                user_id=user.id,
                user_name=user.name,
                user_email=user.email,
                key_last4=license.key_last4,
                plan=license.plan,
                product=license.product,
                status=license.status,
                activation_limit=license.activation_limit,
                active_devices=int(active_devices),
                starts_at=license.starts_at,
                expires_at=license.expires_at,
                created_at=license.created_at,
            )
        )

    return results


@router.get(
    "/{license_id}",
    response_model=LicenseSummary,
    summary="Get one license",
)
def get_license(
    license_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
) -> LicenseSummary:
    license = _require_license(db, license_id)
    user = _require_user(db, license.user_id)

    active_devices = db.execute(
        select(func.count())
        .select_from(LicenseActivation)
        .where(
            LicenseActivation.license_id == license.id,
            LicenseActivation.status == ActivationStatus.ACTIVE,
        )
    ).scalar_one()

    return LicenseSummary(
        id=license.id,
        user_id=user.id,
        user_name=user.name,
        user_email=user.email,
        key_last4=license.key_last4,
        plan=license.plan,
        product=license.product,
        status=license.status,
        activation_limit=license.activation_limit,
        active_devices=int(active_devices),
        starts_at=license.starts_at,
        expires_at=license.expires_at,
        created_at=license.created_at,
    )


@router.post(
    "/{license_id}/suspend",
    response_model=LicenseSummary,
    summary="Suspend a license",
)
def suspend_license(
    license_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> LicenseSummary:
    license = _require_license(db, license_id)

    license.status = LicenseStatus.SUSPENDED

    audit.record(
        db,
        action="LICENSE_SUSPENDED",
        actor_user_id=admin.id,
        actor_ip=ip,
        target_type="license",
        target_id=str(license.id),
    )

    db.commit()

    return get_license(license.id, db, admin)


@router.post(
    "/{license_id}/revoke",
    response_model=LicenseSummary,
    summary="Revoke a license",
)
def revoke_license(
    license_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> LicenseSummary:
    license = _require_license(db, license_id)

    license.status = LicenseStatus.REVOKED
    license.revoked_at = datetime.now(timezone.utc)

    db.execute(
        LicenseActivation.__table__.update()
        .where(
            LicenseActivation.license_id == license.id,
            LicenseActivation.status == ActivationStatus.ACTIVE,
        )
        .values(
            status=ActivationStatus.DEACTIVATED,
            deactivated_at=datetime.now(timezone.utc),
        )
    )

    audit.record(
        db,
        action="LICENSE_REVOKED",
        actor_user_id=admin.id,
        actor_ip=ip,
        target_type="license",
        target_id=str(license.id),
    )

    db.commit()

    return get_license(license.id, db, admin)


@router.get(
    "/{license_id}/devices",
    response_model=list[LicenseDeviceResponse],
    summary="List license devices",
)
def list_license_devices(
    license_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
) -> list[LicenseDeviceResponse]:
    license = _require_license(db, license_id)

    rows = (
        db.execute(
            select(LicenseActivation, Device)
            .join(Device, Device.id == LicenseActivation.device_id)
            .where(LicenseActivation.license_id == license.id)
            .order_by(LicenseActivation.created_at.desc())
        )
        .all()
    )

    return [
        LicenseDeviceResponse(
            device_id=device.id,
            fingerprint=device.fingerprint,
            name=device.name,
            platform=device.platform,
            app_version=device.app_version,
            status=activation.status,
            activated_at=activation.activated_at,
            last_seen_at=device.last_seen_at,
        )
        for activation, device in rows
    ]


@router.post(
    "/{license_id}/devices/{device_id}/deactivate",
    response_model=dict,
    summary="Deactivate a license device",
)
def deactivate_device(
    license_id: uuid.UUID,
    device_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> dict:
    license = _require_license(db, license_id)

    activation = db.execute(
        select(LicenseActivation).where(
            LicenseActivation.license_id == license.id,
            LicenseActivation.device_id == device_id,
            LicenseActivation.status == ActivationStatus.ACTIVE,
        )
    ).scalar_one_or_none()

    if activation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active device activation not found.",
        )

    now = datetime.now(timezone.utc)

    activation.status = ActivationStatus.DEACTIVATED
    activation.deactivated_at = now

    device = db.get(Device, device_id)
    if device is not None:
        device.status = ActivationStatus.DEACTIVATED
        device.deactivated_at = now

    audit.record(
        db,
        action="LICENSE_DEVICE_DEACTIVATED",
        actor_user_id=admin.id,
        actor_ip=ip,
        target_type="license_activation",
        target_id=str(activation.id),
        metadata={"license_id": str(license.id), "device_id": str(device_id)},
    )

    db.commit()

    return {"ok": True, "message": "Device deactivated."}