"""
Model, relationship and constraint tests.

These run against a real PostgreSQL database with the real migration applied,
because most of what is being tested here -- partial unique indexes, ON DELETE
behaviour, enum types -- does not exist in SQLite and would silently pass
against it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    ActivationStatus,
    AuditLog,
    CreditReservation,
    CreditTransaction,
    CreditTransactionType,
    Device,
    License,
    LicenseActivation,
    LicenseStatus,
    Release,
    ReleaseStatus,
    ReservationStatus,
    User,
    UserRole,
    UserStatus,
)
from tests.conftest import requires_database

pytestmark = requires_database


def _utc(**kwargs) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**kwargs)


class TestUser:
    def test_defaults(self, db, make_user):
        user = make_user()
        db.flush()
        assert user.role is UserRole.CLIENT
        assert user.status is UserStatus.ACTIVE
        assert user.email_verified is False
        assert user.created_at is not None
        assert user.id is not None

    def test_uuid_primary_key_is_not_sequential(self, db, make_user):
        first, second = make_user(), make_user()
        assert isinstance(first.id, uuid.UUID)
        assert abs(first.id.int - second.id.int) > 10**20

    def test_email_is_normalised_to_lowercase(self, db, make_user):
        user = make_user(email="  Studio@Example.TEST  ")
        assert user.email == "studio@example.test"

    def test_email_is_unique_case_insensitively(self, db, make_user):
        make_user(email="dup@example.test")
        db.flush()
        # The factory flushes, so the violation surfaces from this call itself.
        with pytest.raises(IntegrityError):
            make_user(email="DUP@Example.Test")

    def test_password_hash_is_never_the_plaintext(self, db, make_user):
        """Belt and braces against a future refactor writing the raw password."""
        user = make_user(password="a-very-good-password")
        assert user.password_hash != "a-very-good-password"
        assert "a-very-good-password" not in (user.password_hash or "")

    def test_repr_does_not_leak_the_email(self, db, make_user):
        """Reprs end up in logs and tracebacks; email is personal data."""
        user = make_user(email="private@example.test")
        assert "private@example.test" not in repr(user)


class TestLicense:
    def test_belongs_to_a_user(self, db, make_user, make_license):
        user = make_user()
        licence = make_license(user)
        db.refresh(user)
        assert licence in user.licenses
        assert licence.user.id == user.id

    def test_key_hash_is_unique(self, db, make_user, make_license):
        user = make_user()
        make_license(user, key="PF-SAME-KEY-0001")
        db.flush()
        with pytest.raises(IntegrityError):
            make_license(user, key="PF-SAME-KEY-0001")

    def test_plan_is_free_text_so_new_plans_need_no_migration(
        self, db, make_user, make_license
    ):
        user = make_user()
        for plan in ("free_trial", "monthly", "annual", "lifetime", "studio"):
            make_license(user, plan=plan)
        db.flush()
        assert len(db.execute(select(License)).scalars().all()) == 5

    def test_deleting_a_user_with_a_licence_is_restricted(
        self, db, make_user, make_license
    ):
        """Billing history must not vanish because someone deleted an account."""
        user = make_user()
        make_license(user)
        db.flush()
        db.delete(user)
        with pytest.raises(IntegrityError):
            db.flush()

    def test_expiry_is_derived_not_read_from_status(self, db, make_user, make_license):
        """A stale ACTIVE status must not entitle an expired licence."""
        user = make_user()
        expired = make_license(
            user, status=LicenseStatus.ACTIVE, expires_at=_utc(days=-1)
        )
        assert expired.is_valid_at() is False

    def test_null_expiry_means_perpetual(self, db, make_user, make_license):
        user = make_user()
        licence = make_license(user, status=LicenseStatus.ACTIVE, expires_at=None)
        assert licence.is_valid_at() is True

    def test_revoked_licence_is_never_valid(self, db, make_user, make_license):
        user = make_user()
        licence = make_license(
            user, status=LicenseStatus.REVOKED, expires_at=_utc(days=365)
        )
        assert licence.is_valid_at() is False

    def test_licence_not_yet_started_is_invalid(self, db, make_user, make_license):
        user = make_user()
        licence = make_license(
            user, status=LicenseStatus.ACTIVE, starts_at=_utc(days=7)
        )
        assert licence.is_valid_at() is False


class TestDevice:
    def test_fingerprint_is_unique_per_user_not_globally(
        self, db, make_user, make_device
    ):
        """A shared studio machine may legitimately serve two accounts."""
        shared = "a" * 64
        make_device(make_user(), fingerprint=shared)
        make_device(make_user(), fingerprint=shared)
        db.flush()  # must not raise

    def test_same_user_cannot_register_a_device_twice(
        self, db, make_user, make_device
    ):
        user = make_user()
        make_device(user, fingerprint="b" * 64)
        db.flush()
        with pytest.raises(IntegrityError):
            make_device(user, fingerprint="b" * 64)

    def test_deleting_a_user_cascades_to_devices(self, db, make_user, make_device):
        user = make_user()
        make_device(user)
        db.flush()
        user_id = user.id
        db.delete(user)
        db.flush()
        remaining = db.execute(
            select(Device).where(Device.user_id == user_id)
        ).scalars().all()
        assert remaining == []


class TestLicenseActivation:
    def test_one_active_seat_per_device_per_licence(
        self, db, make_user, make_license, make_device
    ):
        """The partial unique index -- the thing that makes seat limits real."""
        user = make_user()
        licence = make_license(user)
        device = make_device(user)
        db.add(
            LicenseActivation(
                license_id=licence.id,
                device_id=device.id,
                status=ActivationStatus.ACTIVE,
            )
        )
        db.flush()
        db.add(
            LicenseActivation(
                license_id=licence.id,
                device_id=device.id,
                status=ActivationStatus.ACTIVE,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()

    def test_deactivation_history_is_allowed(
        self, db, make_user, make_license, make_device
    ):
        """Many DEACTIVATED rows, at most one ACTIVE -- the point of the partial index."""
        user = make_user()
        licence = make_license(user)
        device = make_device(user)
        for _ in range(3):
            db.add(
                LicenseActivation(
                    license_id=licence.id,
                    device_id=device.id,
                    status=ActivationStatus.DEACTIVATED,
                    deactivated_at=_utc(),
                )
            )
        db.add(
            LicenseActivation(
                license_id=licence.id,
                device_id=device.id,
                status=ActivationStatus.ACTIVE,
            )
        )
        db.flush()  # must not raise

        rows = db.execute(
            select(LicenseActivation).where(LicenseActivation.license_id == licence.id)
        ).scalars().all()
        assert len(rows) == 4
        assert sum(1 for r in rows if r.status is ActivationStatus.ACTIVE) == 1

    def test_seat_count_query(self, db, make_user, make_license, make_device):
        user = make_user()
        licence = make_license(user, activation_limit=2)
        for _ in range(2):
            device = make_device(user)
            db.add(
                LicenseActivation(
                    license_id=licence.id,
                    device_id=device.id,
                    status=ActivationStatus.ACTIVE,
                )
            )
        db.flush()
        active = db.execute(
            select(LicenseActivation).where(
                LicenseActivation.license_id == licence.id,
                LicenseActivation.status == ActivationStatus.ACTIVE,
            )
        ).scalars().all()
        assert len(active) == licence.activation_limit


class TestCreditLedger:
    def test_transaction_records_a_signed_amount(self, db, make_user):
        user = make_user()
        db.add(
            CreditTransaction(
                user_id=user.id,
                amount=-25,
                transaction_type=CreditTransactionType.USAGE,
                reason="album export",
                balance_after=75,
            )
        )
        db.flush()
        row = db.execute(select(CreditTransaction)).scalar_one()
        assert row.amount == -25
        assert row.transaction_type is CreditTransactionType.USAGE

    def test_reference_id_is_idempotent_per_user(self, db, make_user):
        """A retried payment webhook must not be able to credit twice."""
        user = make_user()
        for _ in range(2):
            db.add(
                CreditTransaction(
                    user_id=user.id,
                    amount=100,
                    transaction_type=CreditTransactionType.PURCHASE,
                    reference_id="payment-abc-123",
                    balance_after=100,
                )
            )
        with pytest.raises(IntegrityError):
            db.flush()

    def test_null_reference_ids_do_not_collide(self, db, make_user):
        """The index is partial: unreferenced rows must remain unconstrained."""
        user = make_user()
        for _ in range(3):
            db.add(
                CreditTransaction(
                    user_id=user.id,
                    amount=5,
                    transaction_type=CreditTransactionType.BONUS,
                    reference_id=None,
                    balance_after=5,
                )
            )
        db.flush()  # must not raise

    def test_two_users_may_share_a_reference_id(self, db, make_user):
        for _ in range(2):
            db.add(
                CreditTransaction(
                    user_id=make_user().id,
                    amount=10,
                    transaction_type=CreditTransactionType.ADMIN_GRANT,
                    reference_id="grant-2026-08",
                    balance_after=10,
                )
            )
        db.flush()  # must not raise

    def test_reservation_requires_an_expiry(self, db, make_user):
        """A hold with no expiry could strand a customer's credits forever."""
        db.add(
            CreditReservation(
                user_id=make_user().id,
                amount=10,
                status=ReservationStatus.OPEN,
                expires_at=None,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()

    def test_reserve_commit_cycle(self, db, make_user):
        user = make_user()
        reservation = CreditReservation(
            user_id=user.id,
            amount=30,
            status=ReservationStatus.OPEN,
            expires_at=_utc(hours=2),
            context={"job": "album", "photos": 400},
        )
        db.add(reservation)
        db.flush()

        reservation.status = ReservationStatus.COMMITTED
        reservation.settled_at = _utc()
        db.add(
            CreditTransaction(
                user_id=user.id,
                reservation_id=reservation.id,
                amount=-30,
                transaction_type=CreditTransactionType.COMMIT,
                balance_after=70,
            )
        )
        db.flush()
        assert reservation.status is ReservationStatus.COMMITTED
        assert reservation.context["photos"] == 400


class TestRelease:
    def test_same_version_may_exist_on_two_channels(self, db):
        for channel in ("stable", "beta"):
            db.add(
                Release(
                    version="1.2.0",
                    channel=channel,
                    status=ReleaseStatus.PUBLISHED,
                    download_url="https://github.com/example/releases/1.2.0",
                    sha256="0" * 64,
                )
            )
        db.flush()  # must not raise

    def test_version_is_unique_within_a_channel(self, db):
        for _ in range(2):
            db.add(
                Release(
                    version="1.3.0",
                    channel="stable",
                    status=ReleaseStatus.PUBLISHED,
                )
            )
        with pytest.raises(IntegrityError):
            db.flush()


class TestAuditLog:
    def test_survives_deletion_of_its_target(self, db, make_user, make_device):
        """No foreign key, on purpose: deleting a user must not erase the record."""
        user = make_user()
        make_device(user)
        db.flush()
        user_id = user.id

        db.add(
            AuditLog(
                actor_user_id=user_id,
                action="USER_DELETED",
                target_type="user",
                target_id=str(user_id),
                metadata_json={"reason": "customer request"},
            )
        )
        db.flush()

        db.delete(user)
        db.flush()

        entry = db.execute(select(AuditLog)).scalar_one()
        assert entry.target_id == str(user_id)
        assert entry.metadata_json["reason"] == "customer request"
        assert db.get(User, user_id) is None
