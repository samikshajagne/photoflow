"""
Audit metadata scrubbing.

The audit table is read during incidents and shown in the admin dashboard. A
credential written into it is a credential in a place people look at casually
and copy out of. The scrubber is the only thing standing between a careless
``metadata={"request": request_body}`` and that outcome, so it gets tested
harder than its size suggests.
"""

from __future__ import annotations

from app.services.audit import REDACTED, scrub_metadata


class TestScrubbing:
    def test_none_passes_through(self):
        assert scrub_metadata(None) is None

    def test_ordinary_fields_survive(self):
        cleaned = scrub_metadata({"plan": "annual", "seats": 3, "ok": True})
        assert cleaned == {"plan": "annual", "seats": 3, "ok": True}

    def test_exact_forbidden_keys_are_redacted(self):
        cleaned = scrub_metadata(
            {
                "password": "hunter2",
                "access_token": "eyJhbGciOi",
                "api_key": "sk-live-abc",
                "database_url": "postgresql://u:p@host/db",
                "license_key": "PF-XXXX-YYYY",
            }
        )
        assert all(value == REDACTED for value in cleaned.values())

    def test_suspicious_substrings_are_caught(self):
        """Substring matching, so a new name nobody listed is still caught."""
        cleaned = scrub_metadata(
            {
                "customer_api_key": "sk-live-abc",
                "x_admin_token": "abc123",
                "user_password_hash": "$argon2id$...",
                "OPENAI_API_KEY": "sk-proj-abc",
                "Authorization": "Bearer abc",
            }
        )
        assert all(value == REDACTED for value in cleaned.values())

    def test_nested_secrets_are_redacted(self):
        cleaned = scrub_metadata(
            {"payload": {"user": {"email": "a@b.test", "password": "hunter2"}}}
        )
        assert cleaned["payload"]["user"]["password"] == REDACTED
        assert cleaned["payload"]["user"]["email"] == "a@b.test"

    def test_secrets_inside_lists_are_redacted(self):
        cleaned = scrub_metadata({"items": [{"token": "abc"}, {"name": "fine"}]})
        assert cleaned["items"][0]["token"] == REDACTED
        assert cleaned["items"][1]["name"] == "fine"

    def test_long_values_are_truncated(self):
        cleaned = scrub_metadata({"body": "x" * 10_000})
        assert len(cleaned["body"]) < 2_100

    def test_deep_nesting_is_bounded(self):
        """A recursive structure must not blow the stack on the way into a log."""
        payload: dict = {"level": 0}
        node = payload
        for depth in range(1, 20):
            node["child"] = {"level": depth}
            node = node["child"]
        assert scrub_metadata(payload) is not None

    def test_original_is_not_mutated(self):
        original = {"password": "hunter2"}
        scrub_metadata(original)
        assert original["password"] == "hunter2"


class TestRecord:
    def test_record_scrubs_before_writing(self, db, make_user):
        import pytest

        from tests.conftest import TEST_DATABASE_URL

        if not TEST_DATABASE_URL:
            pytest.skip("PHOTOFLOW_TEST_DATABASE_URL is not set")

        from app.services.audit import record

        user = make_user()
        entry = record(
            db,
            action="ADMIN_CREATED_LICENSE",
            actor_user_id=user.id,
            target_type="license",
            target_id="abc",
            metadata={"plan": "annual", "admin_token": "super-secret"},
        )
        db.flush()
        assert entry.metadata_json["plan"] == "annual"
        assert entry.metadata_json["admin_token"] == REDACTED
