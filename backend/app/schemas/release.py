"""Request/response models for release management and lookup.

Kept separate from ``app/schemas/auth.py`` rather than folded in, matching
how ``admin_licenses.py`` defines its own request/response models inline --
releases are their own concern, not a variant of user management.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ReleaseStatus


class ReleaseCreateRequest(BaseModel):
    """What an administrator supplies to register a new build.

    ``status`` is deliberately absent: every release is created ``DRAFT`` and
    moves to ``PUBLISHED`` only through the explicit publish action, so "this
    is now the current download" is always a distinct, audited decision rather
    than a side effect of filling in a form.
    """

    version: str = Field(min_length=1, max_length=50)
    product: str = Field(default="photoflow", min_length=1, max_length=50)
    platform: str = Field(default="Windows", min_length=1, max_length=50)
    channel: str = Field(default="stable", min_length=1, max_length=30)
    installer_filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    download_url: str = Field(min_length=1, max_length=500)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    release_notes: str | None = Field(default=None, max_length=20000)
    release_notes_url: str | None = Field(default=None, max_length=500)
    minimum_supported_version: str | None = Field(default=None, max_length=50)
    critical: bool = False

    @field_validator("download_url", "release_notes_url")
    @classmethod
    def _require_https_or_http(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.startswith(("https://", "http://")):
            raise ValueError("must be an absolute http(s) URL")
        return value

    @field_validator("sha256")
    @classmethod
    def _require_hex(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not all(c in "0123456789abcdefABCDEF" for c in value):
            raise ValueError("sha256 must be 64 hex characters")
        return value.lower()


class ReleaseUpdateRequest(BaseModel):
    """Edit release metadata. All fields optional; only status is off-limits
    here -- that stays behind the publish/yank actions (see ``ReleaseCreateRequest``)."""

    version: str | None = Field(default=None, min_length=1, max_length=50)
    product: str | None = Field(default=None, min_length=1, max_length=50)
    platform: str | None = Field(default=None, min_length=1, max_length=50)
    channel: str | None = Field(default=None, min_length=1, max_length=30)
    installer_filename: str | None = Field(default=None, min_length=1, max_length=255)
    size_bytes: int | None = Field(default=None, gt=0)
    download_url: str | None = Field(default=None, min_length=1, max_length=500)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    release_notes: str | None = Field(default=None, max_length=20000)
    release_notes_url: str | None = Field(default=None, max_length=500)
    minimum_supported_version: str | None = Field(default=None, max_length=50)
    critical: bool | None = None

    @field_validator("download_url", "release_notes_url")
    @classmethod
    def _require_https_or_http(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.startswith(("https://", "http://")):
            raise ValueError("must be an absolute http(s) URL")
        return value

    @field_validator("sha256")
    @classmethod
    def _require_hex(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not all(c in "0123456789abcdefABCDEF" for c in value):
            raise ValueError("sha256 must be 64 hex characters")
        return value.lower()


class ReleaseSummary(BaseModel):
    """Full release record, for the admin dashboard."""

    id: uuid.UUID
    version: str
    product: str
    platform: str | None
    channel: str
    status: ReleaseStatus
    installer_filename: str | None
    size_bytes: int | None
    download_url: str | None
    sha256: str | None
    release_notes: str | None
    release_notes_url: str | None
    minimum_supported_version: str | None
    critical: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicReleaseResponse(BaseModel):
    """What an unauthenticated visitor (the website, or the desktop updater)
    may know about the current release. No ``id``, no ``status``, no internal
    timestamps -- nothing here is a filesystem path or an administrative
    detail, on purpose."""

    version: str
    product: str
    platform: str | None
    channel: str
    installer_filename: str | None
    size_bytes: int | None
    download_url: str | None
    sha256: str | None
    release_notes: str | None
    release_notes_url: str | None
    minimum_supported_version: str | None
    critical: bool
    published_at: datetime | None
