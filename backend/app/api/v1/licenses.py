"""License activation and validation endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import ClientIp
from app.auth.dependencies import CurrentUser
from app.database.session import get_db
from app.services.licensing import (
    ActivationLimitError,
    DeviceActivationError,
    LicenseInvalidError,
    LicenseNotFoundError,
    LicenseOwnershipError,
    activate_license,
    deactivate_license,
    validate_license,
)

router = APIRouter(prefix="/licenses", tags=["licenses"])

DbSession = Annotated[Session, Depends(get_db)]


class LicenseRequest(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    machine: str = Field(min_length=1, max_length=128)
    product: str = Field(default="photoflow", max_length=50)
    version: str = Field(default="", max_length=50)
    platform: str | None = Field(default=None, max_length=100)
    device_name: str | None = Field(default=None, max_length=200)


class DeactivateRequest(BaseModel):
    license_id: str
    machine: str = Field(min_length=1, max_length=128)


class LicenseResponse(BaseModel):
    ok: bool
    message: str
    expires_on: str = ""
    seats: int = 0
    customer: str = ""
    license_id: str
    device_id: str


def _error_response(exc: Exception) -> HTTPException:
    if isinstance(exc, LicenseNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="License not found.",
        )

    if isinstance(exc, LicenseOwnershipError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This license is not available for this account.",
        )

    if isinstance(exc, LicenseInvalidError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )

    if isinstance(exc, ActivationLimitError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This license has reached its activation limit.",
        )

    if isinstance(exc, DeviceActivationError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="License operation failed.",
    )


@router.post(
    "/activate",
    response_model=LicenseResponse,
    summary="Activate a license on this device",
)
def activate(
    payload: LicenseRequest,
    db: DbSession,
    user: CurrentUser,
    ip: ClientIp,
) -> LicenseResponse:
    """Activate a license for the authenticated user's machine."""
    if payload.product.lower() != "photoflow":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported product.",
        )

    try:
        result = activate_license(
            db,
            user_id=user.id,
            key=payload.key,
            fingerprint=payload.machine,
            platform=payload.platform,
            app_version=payload.version,
            device_name=payload.device_name,
            actor_ip=ip,
        )
        db.commit()
    except (
        LicenseNotFoundError,
        LicenseOwnershipError,
        LicenseInvalidError,
        ActivationLimitError,
        DeviceActivationError,
    ) as exc:
        db.rollback()
        raise _error_response(exc) from None

    return LicenseResponse(
        ok=True,
        message="Activated.",
        expires_on=(
            result.license.expires_at.isoformat()
            if result.license.expires_at
            else ""
        ),
        seats=result.license.activation_limit,
        customer=user.name,
        license_id=str(result.license.id),
        device_id=str(result.device.id),
    )


@router.post(
    "/validate",
    response_model=LicenseResponse,
    summary="Validate an active license on this device",
)
def validate(
    payload: LicenseRequest,
    db: DbSession,
    user: CurrentUser,
    ip: ClientIp,
) -> LicenseResponse:
    """Validate an existing license/device activation."""
    if payload.product.lower() != "photoflow":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported product.",
        )

    try:
        result = validate_license(
            db,
            user_id=user.id,
            key=payload.key,
            fingerprint=payload.machine,
            platform=payload.platform,
            app_version=payload.version,
            actor_ip=ip,
        )
        db.commit()
    except (
        LicenseNotFoundError,
        LicenseOwnershipError,
        LicenseInvalidError,
        DeviceActivationError,
    ) as exc:
        db.rollback()
        raise _error_response(exc) from None

    return LicenseResponse(
        ok=True,
        message="License active.",
        expires_on=(
            result.license.expires_at.isoformat()
            if result.license.expires_at
            else ""
        ),
        seats=result.license.activation_limit,
        customer=user.name,
        license_id=str(result.license.id),
        device_id=str(result.device.id),
    )


@router.post(
    "/deactivate",
    response_model=dict,
    summary="Deactivate a license on this device",
)
def deactivate(
    payload: DeactivateRequest,
    db: DbSession,
    user: CurrentUser,
    ip: ClientIp,
) -> dict:
    """Release one activation seat."""
    import uuid

    try:
        license_id = uuid.UUID(payload.license_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid license ID.",
        ) from None

    try:
        deactivate_license(
            db,
            user_id=user.id,
            license_id=license_id,
            fingerprint=payload.machine,
            actor_ip=ip,
        )
        db.commit()
    except (
        LicenseNotFoundError,
        LicenseOwnershipError,
        DeviceActivationError,
    ) as exc:
        db.rollback()
        raise _error_response(exc) from None

    return {"ok": True, "message": "Deactivated."}