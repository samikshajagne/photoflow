"""
Administrative release management.

Used by the private PhotoFlow admin dashboard to register and publish builds.
Every endpoint requires an ADMIN account -- see ``app/auth/dependencies.py``.

The installer binary itself is never handled here or by this backend; it lives
on GitHub Releases (see ``app/models/release.py``). This module only ever
reads and writes the metadata row, which is what makes ``download_url``
trustworthy enough for the public endpoint in ``releases.py`` to hand out.

Deleting a release is intentionally not exposed. A published build is a fact
about what customers may have already downloaded and installed; erasing the
row would erase that history. "Remove it from public view" is what ``yank``
is for -- it is the delete this API offers, and it keeps the audit trail
intact.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ClientIp
from app.auth.dependencies import AdminUser
from app.database.session import get_db
from app.models.enums import ReleaseStatus
from app.models.release import Release
from app.schemas.release import (
    ReleaseCreateRequest,
    ReleaseSummary,
    ReleaseUpdateRequest,
)
from app.services import audit
from app.services.audit import AuditAction

router = APIRouter(prefix="/releases", tags=["admin-releases"])

DbSession = Annotated[Session, Depends(get_db)]


def _require_release(db: Session, release_id: uuid.UUID) -> Release:
    release = db.get(Release, release_id)
    if release is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Release not found.",
        )
    return release


def _summary(release: Release) -> ReleaseSummary:
    return ReleaseSummary(
        id=release.id,
        version=release.version,
        product=release.product,
        platform=release.platform,
        channel=release.channel,
        status=release.status,
        installer_filename=release.installer_filename,
        size_bytes=release.size_bytes,
        download_url=release.download_url,
        sha256=release.sha256,
        release_notes=release.release_notes,
        release_notes_url=release.release_notes_url,
        minimum_supported_version=release.minimum_supported_version,
        critical=release.critical,
        published_at=release.published_at,
        created_at=release.created_at,
        updated_at=release.updated_at,
    )


@router.post(
    "",
    response_model=ReleaseSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new release",
)
def create_release(
    payload: ReleaseCreateRequest,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> ReleaseSummary:
    """Register a build's metadata. Always created as DRAFT."""

    existing = db.execute(
        select(Release).where(
            Release.version == payload.version,
            Release.channel == payload.channel,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A release with this version and channel already exists.",
        )

    release = Release(
        version=payload.version,
        product=payload.product,
        platform=payload.platform,
        channel=payload.channel,
        status=ReleaseStatus.DRAFT,
        installer_filename=payload.installer_filename,
        size_bytes=payload.size_bytes,
        download_url=payload.download_url,
        sha256=payload.sha256,
        release_notes=payload.release_notes,
        release_notes_url=payload.release_notes_url,
        minimum_supported_version=payload.minimum_supported_version,
        critical=payload.critical,
    )
    db.add(release)
    db.flush()

    audit.record(
        db,
        action=AuditAction.RELEASE_CREATED,
        actor_user_id=admin.id,
        actor_ip=ip,
        target_type="release",
        target_id=str(release.id),
        metadata={
            "version": release.version,
            "product": release.product,
            "platform": release.platform,
            "channel": release.channel,
        },
    )

    db.commit()

    return _summary(release)


@router.get(
    "",
    response_model=list[ReleaseSummary],
    summary="List releases",
)
def list_releases(
    db: DbSession,
    admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    product: str | None = None,
    platform: str | None = None,
    channel: str | None = None,
    release_status: ReleaseStatus | None = Query(default=None, alias="status"),
) -> list[ReleaseSummary]:
    """List releases for the admin dashboard, newest first."""

    conditions = []
    if product is not None:
        conditions.append(Release.product == product)
    if platform is not None:
        conditions.append(Release.platform == platform)
    if channel is not None:
        conditions.append(Release.channel == channel)
    if release_status is not None:
        conditions.append(Release.status == release_status)

    rows = (
        db.execute(
            select(Release)
            .where(*conditions)
            .order_by(Release.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    return [_summary(release) for release in rows]


@router.get(
    "/{release_id}",
    response_model=ReleaseSummary,
    summary="Get one release",
)
def get_release(
    release_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
) -> ReleaseSummary:
    release = _require_release(db, release_id)
    return _summary(release)


@router.patch(
    "/{release_id}",
    response_model=ReleaseSummary,
    summary="Update release metadata",
)
def update_release(
    release_id: uuid.UUID,
    payload: ReleaseUpdateRequest,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> ReleaseSummary:
    release = _require_release(db, release_id)

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(release, field, value)

    if changes:
        audit.record(
            db,
            action=AuditAction.RELEASE_UPDATED,
            actor_user_id=admin.id,
            actor_ip=ip,
            target_type="release",
            target_id=str(release.id),
            metadata={"fields": sorted(changes.keys())},
        )

    db.commit()

    return _summary(release)


@router.post(
    "/{release_id}/publish",
    response_model=ReleaseSummary,
    summary="Publish a release",
)
def publish_release(
    release_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> ReleaseSummary:
    """Make this the release the public endpoint and website hand out."""
    release = _require_release(db, release_id)

    release.status = ReleaseStatus.PUBLISHED
    release.published_at = datetime.now(timezone.utc)

    audit.record(
        db,
        action=AuditAction.RELEASE_PUBLISHED,
        actor_user_id=admin.id,
        actor_ip=ip,
        target_type="release",
        target_id=str(release.id),
        metadata={"version": release.version, "channel": release.channel},
    )

    db.commit()

    return _summary(release)


@router.post(
    "/{release_id}/yank",
    response_model=ReleaseSummary,
    summary="Yank a release from public view",
)
def yank_release(
    release_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
    ip: ClientIp,
) -> ReleaseSummary:
    """
    Remove a release from public visibility without deleting its history.

    The public "current release" endpoint only ever considers PUBLISHED rows,
    so a YANKED release simply stops being offered -- existing installs are
    unaffected, and the row (and its audit trail) remains.
    """
    release = _require_release(db, release_id)

    release.status = ReleaseStatus.YANKED

    audit.record(
        db,
        action=AuditAction.RELEASE_YANKED,
        actor_user_id=admin.id,
        actor_ip=ip,
        target_type="release",
        target_id=str(release.id),
        metadata={"version": release.version, "channel": release.channel},
    )

    db.commit()

    return _summary(release)
