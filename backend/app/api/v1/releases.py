"""
Public release lookup.

Unauthenticated on purpose: this is what the public website's download page
and (eventually) the desktop app's own update check call. It answers exactly
one question -- "what is the current release for this product/platform/
channel" -- and returns nothing an administrator would consider internal: no
row id, no status column, no timestamps beyond ``published_at``. See
``app/schemas/release.py::PublicReleaseResponse``.

Administrative operations (create, list everything regardless of status,
publish, yank) live in ``admin_releases.py`` behind ``require_admin``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.enums import ReleaseStatus
from app.models.release import Release
from app.schemas.release import PublicReleaseResponse

router = APIRouter(prefix="/releases", tags=["releases"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get(
    "/current",
    response_model=PublicReleaseResponse,
    summary="Get the current published release",
)
def get_current_release(
    db: DbSession,
    product: str = "photoflow",
    platform: str = "Windows",
    channel: str = "stable",
) -> PublicReleaseResponse:
    """
    The most recently published release matching product/platform/channel.

    Only ``PUBLISHED`` rows are ever considered -- a ``DRAFT`` being prepared
    or a ``YANKED`` build must never appear here, regardless of how recent it
    is. 404 rather than an empty/null body: a caller checking "is there
    anything to download" gets an unambiguous answer either way.
    """
    release = db.execute(
        select(Release)
        .where(
            Release.product == product,
            Release.platform == platform,
            Release.channel == channel,
            Release.status == ReleaseStatus.PUBLISHED,
        )
        .order_by(Release.published_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if release is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published release found.",
        )

    return PublicReleaseResponse(
        version=release.version,
        product=release.product,
        platform=release.platform,
        channel=release.channel,
        installer_filename=release.installer_filename,
        size_bytes=release.size_bytes,
        download_url=release.download_url,
        sha256=release.sha256,
        release_notes=release.release_notes,
        release_notes_url=release.release_notes_url,
        minimum_supported_version=release.minimum_supported_version,
        critical=release.critical,
        published_at=release.published_at,
    )
