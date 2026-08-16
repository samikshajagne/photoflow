"""
Writing audit-log entries safely.

Every write goes through :func:`record` so the metadata scrubber cannot be
bypassed by a caller who forgets it exists. The scrubber is deliberately
aggressive: it drops a key on a substring match, so ``customer_api_key`` and
``x_admin_token`` are caught as well as ``api_key``. A false positive costs a
missing debugging field; a false negative puts a live credential in a table
built to be read during an incident.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import FORBIDDEN_METADATA_KEYS, AuditLog

REDACTED = "[redacted]"


class AuditAction:
    """
    The vocabulary of recorded events.

    Constants rather than an enum, because ``audit_logs.action`` is a text
    column on purpose: a new event type should be a new line here, not a
    migration against a table that by then holds years of history.
    """

    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILURE = "LOGIN_FAILURE"
    LOGOUT = "LOGOUT"
    REFRESH_SUCCESS = "REFRESH_SUCCESS"
    REFRESH_REUSE_DETECTED = "REFRESH_REUSE_DETECTED"
    ADMIN_CREATED = "ADMIN_CREATED"
    CLIENT_CREATED = "CLIENT_CREATED"
    USER_DISABLED = "USER_DISABLED"
    USER_ENABLED = "USER_ENABLED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    SIGNING_KEY_GENERATED = "SIGNING_KEY_GENERATED"
    RELEASE_CREATED = "RELEASE_CREATED"
    RELEASE_UPDATED = "RELEASE_UPDATED"
    RELEASE_PUBLISHED = "RELEASE_PUBLISHED"
    RELEASE_YANKED = "RELEASE_YANKED"

# Substrings that make a key suspicious regardless of its full name.
_SUSPICIOUS_FRAGMENTS = ("password", "secret", "token", "key", "credential", "authorization")

_MAX_VALUE_LENGTH = 2000


def scrub_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """
    Return a copy with anything credential-shaped replaced by ``[redacted]``.

    Recurses into nested dicts and lists, because a secret one level down is
    still a secret. Long strings are truncated so a caller cannot accidentally
    dump a whole request body into the table.
    """
    if metadata is None:
        return None
    return _scrub_value(dict(metadata), depth=0)  # type: ignore[return-value]


def _scrub_value(value: Any, depth: int) -> Any:
    if depth > 6:
        return REDACTED
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered in FORBIDDEN_METADATA_KEYS or any(
                fragment in lowered for fragment in _SUSPICIOUS_FRAGMENTS
            ):
                cleaned[key] = REDACTED
            else:
                cleaned[key] = _scrub_value(raw_value, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item, depth + 1) for item in value]
    if isinstance(value, str) and len(value) > _MAX_VALUE_LENGTH:
        return value[:_MAX_VALUE_LENGTH] + "…[truncated]"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_VALUE_LENGTH]


def record(
    db: Session,
    *,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    actor_ip: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AuditLog:
    """
    Append one audit entry. The caller commits.

    Written in the *same transaction* as the change it describes, on purpose: an
    audit row that survives a rolled-back operation is a lie, and one that is
    lost when the operation succeeds is worse.
    """
    entry = AuditLog(
        actor_user_id=actor_user_id,
        actor_ip=actor_ip,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        metadata_json=scrub_metadata(metadata),
    )
    db.add(entry)
    return entry
