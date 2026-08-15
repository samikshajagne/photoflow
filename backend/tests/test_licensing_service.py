import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import ActivationStatus, LicenseStatus
from app.models.license import LicenseActivation
from app.services.licensing import (
    ActivationLimitError,
    DeviceActivationError,
    LicenseInvalidError,
    LicenseNotFoundError,
    LicenseOwnershipError,
    activate_license,
    build_license,
    create_license_key,
    deactivate_license,
    hash_license_key,
    normalize_license_key,
    validate_license,
)


class TestLicenseKeyHelpers:
    def test_normalization_is_case_and_separator_insensitive(self):
        assert normalize_license_key("pf-ab12-cd34") == "PFAB12CD34"
        assert normalize_license_key(" PF AB12 CD34 ") == "PFAB12CD34"

    def test_hash_is_deterministic(self):
        key = "PF-AB12-CD34"
        assert hash_license_key(key) == hash_license_key("pfab12cd34")

    def test_generated_keys_have_expected_shape(self):
        key = create_license_key()
        parts = key.split("-")
        assert parts[0] == "PF"
        assert len(parts) == 5
        assert all(len(part) == 5 for part in parts[1:])


class TestLicenseActivation:
    def test_valid_license_activates(self, db, make_user, make_license):
        user = make_user()
        key = "PF-ACTIVATE-1234"
        license = make_license(user, key=key)

        result = activate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-001",
            platform="Windows 11",
            app_version="0.9.2",
        )

        assert result.license.id == license.id
        assert result.device.fingerprint == "machine-001"
        assert result.activation.status == ActivationStatus.ACTIVE
        assert result.reused is False

    def test_wrong_user_cannot_activate(self, db, make_user, make_license):
        owner = make_user()
        attacker = make_user()
        key = "PF-OWNER-1234"
        make_license(owner, key=key)

        with pytest.raises(LicenseOwnershipError):
            activate_license(
                db,
                user_id=attacker.id,
                key=key,
                fingerprint="machine-001",
            )

    @pytest.mark.parametrize(
        "status",
        [
            LicenseStatus.PENDING,
            LicenseStatus.SUSPENDED,
            LicenseStatus.REVOKED,
        ],
    )
    def test_invalid_administrative_status_is_rejected(
        self, db, make_user, make_license, status
    ):
        user = make_user()
        key = f"PF-STATUS-{status.value}"
        make_license(user, key=key, status=status)

        with pytest.raises(LicenseInvalidError):
            activate_license(
                db,
                user_id=user.id,
                key=key,
                fingerprint="machine-001",
            )

    def test_expired_license_is_rejected(self, db, make_user, make_license):
        user = make_user()
        key = "PF-EXPIRED-1234"
        make_license(
            user,
            key=key,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        with pytest.raises(LicenseInvalidError):
            activate_license(
                db,
                user_id=user.id,
                key=key,
                fingerprint="machine-001",
            )

    def test_not_started_license_is_rejected(self, db, make_user, make_license):
        user = make_user()
        key = "PF-FUTURE-1234"
        make_license(
            user,
            key=key,
            starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        )

        with pytest.raises(LicenseInvalidError):
            activate_license(
                db,
                user_id=user.id,
                key=key,
                fingerprint="machine-001",
            )

    def test_same_device_activation_is_idempotent(
        self, db, make_user, make_license
    ):
        user = make_user()
        key = "PF-IDEMPOTENT-1234"
        make_license(user, key=key)

        first = activate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-001",
        )
        second = activate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-001",
        )

        assert first.activation.id == second.activation.id
        assert second.reused is True

        active_count = (
            db.query(LicenseActivation)
            .filter(
                LicenseActivation.license_id == first.license.id,
                LicenseActivation.status == ActivationStatus.ACTIVE,
            )
            .count()
        )
        assert active_count == 1

    def test_activation_limit_is_enforced(
        self, db, make_user, make_license
    ):
        user = make_user()
        key = "PF-SEATLIMIT-1234"
        make_license(user, key=key, activation_limit=1)

        activate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-001",
        )

        with pytest.raises(ActivationLimitError):
            activate_license(
                db,
                user_id=user.id,
                key=key,
                fingerprint="machine-002",
            )

    def test_second_device_works_with_two_seats(
        self, db, make_user, make_license
    ):
        user = make_user()
        key = "PF-TWOSEATS-1234"
        make_license(user, key=key, activation_limit=2)

        first = activate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-001",
        )
        second = activate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-002",
        )

        assert first.device.id != second.device.id
        assert first.activation.status == ActivationStatus.ACTIVE
        assert second.activation.status == ActivationStatus.ACTIVE

    def test_deactivation_releases_seat(
        self, db, make_user, make_license
    ):
        user = make_user()
        key = "PF-DEACTIVATE-1234"
        license = make_license(user, key=key, activation_limit=1)

        activate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-001",
        )

        deactivate_license(
            db,
            user_id=user.id,
            license_id=license.id,
            fingerprint="machine-001",
        )

        result = activate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-002",
        )

        assert result.activation.status == ActivationStatus.ACTIVE

    def test_validation_requires_active_activation(
        self, db, make_user, make_license
    ):
        user = make_user()
        key = "PF-VALIDATE-1234"
        make_license(user, key=key)

        with pytest.raises(DeviceActivationError):
            validate_license(
                db,
                user_id=user.id,
                key=key,
                fingerprint="machine-001",
            )

    def test_validation_succeeds_after_activation(
        self, db, make_user, make_license
    ):
        user = make_user()
        key = "PF-VALIDATEOK-1234"
        make_license(user, key=key)

        activated = activate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-001",
        )

        result = validate_license(
            db,
            user_id=user.id,
            key=key,
            fingerprint="machine-001",
            platform="Windows 11",
            app_version="0.9.2",
        )

        assert result.license.id == activated.license.id
        assert result.device.id == activated.device.id
        assert result.activation.id == activated.activation.id

    def test_unknown_key_is_rejected(self, db, make_user):
        user = make_user()

        with pytest.raises(LicenseNotFoundError):
            activate_license(
                db,
                user_id=user.id,
                key="PF-DOES-NOT-EXIST",
                fingerprint="machine-001",
            )

    def test_raw_key_is_not_stored_as_hash(
        self, db, make_user, make_license
    ):
        user = make_user()
        key = "PF-SECRET-1234"
        license = make_license(user, key=key)

        assert license.key_hash != key
        assert license.key_hash == hash_license_key(key)


class TestLicenseConstruction:
    def test_build_license_stores_only_hash(self, make_user):
        user = make_user()
        key = "PF-BUILD-1234"

        license = build_license(
            user_id=user.id,
            key=key,
            plan="studio",
            activation_limit=3,
        )

        assert license.user_id == user.id
        assert license.plan == "studio"
        assert license.activation_limit == 3
        assert license.key_hash == hashlib.sha256(
            "PFBUILD1234".encode()
        ).hexdigest()
        assert license.key_last4 == "1234"
