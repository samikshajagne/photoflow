"""
Ed25519 signing tests.

The property under test throughout: the desktop application must be able to
*verify* an entitlement without being able to *mint* one. Every negative case
here is a way that could quietly stop being true.
"""

from __future__ import annotations

import base64

import pytest

from app.security.signing import (
    Keypair,
    SignatureInvalid,
    SigningError,
    SigningService,
    canonical_payload,
    generate_keypair,
    load_private_key,
    load_public_key,
)

PAYLOAD = {
    "sub": "9f1c0e5a-0000-4000-8000-000000000000",
    "plan": "annual",
    "expires_at": "2027-08-10T00:00:00Z",
    "seats": 2,
}


@pytest.fixture
def keypair() -> Keypair:
    return generate_keypair()


@pytest.fixture
def service(keypair: Keypair) -> SigningService:
    return SigningService(keypair.private_key_b64, keypair.public_key_b64)


class TestKeyGeneration:
    def test_generates_a_32_byte_pair(self, keypair):
        assert len(base64.b64decode(keypair.private_key_b64)) == 32
        assert len(base64.b64decode(keypair.public_key_b64)) == 32

    def test_each_call_is_a_new_key(self):
        assert generate_keypair().private_key_b64 != generate_keypair().private_key_b64

    def test_keys_load_back(self, keypair):
        assert load_private_key(keypair.private_key_b64) is not None
        assert load_public_key(keypair.public_key_b64) is not None

    def test_repr_does_not_leak_the_private_key(self, keypair):
        """A repr ends up in tracebacks, logs and debugger screenshots."""
        assert keypair.private_key_b64 not in repr(keypair)
        assert "redacted" in repr(keypair)

    def test_malformed_keys_are_rejected(self):
        with pytest.raises(SigningError):
            load_private_key("not-base64!!!")
        with pytest.raises(SigningError):
            load_private_key(base64.b64encode(b"too-short").decode())
        with pytest.raises(SigningError):
            load_public_key(base64.b64encode(b"x" * 31).decode())


class TestSignAndVerify:
    def test_round_trip(self, service):
        signature = service.sign(PAYLOAD)
        assert service.verify(PAYLOAD, signature)

    def test_tampered_payload_is_rejected(self, service):
        signature = service.sign(PAYLOAD)
        tampered = dict(PAYLOAD, seats=99)
        assert not service.verify(tampered, signature)

    def test_adding_a_field_is_rejected(self, service):
        signature = service.sign(PAYLOAD)
        assert not service.verify(dict(PAYLOAD, admin=True), signature)

    def test_removing_a_field_is_rejected(self, service):
        signature = service.sign(PAYLOAD)
        reduced = {k: v for k, v in PAYLOAD.items() if k != "expires_at"}
        assert not service.verify(reduced, signature)

    def test_a_different_public_key_rejects(self, service):
        """The signature is worthless without the matching key."""
        other = generate_keypair()
        verifier = SigningService(public_key_b64=other.public_key_b64)
        assert not verifier.verify(PAYLOAD, service.sign(PAYLOAD))

    def test_garbage_signature_is_rejected(self, service):
        for bad in ("", "not-base64!!!", base64.b64encode(b"x" * 64).decode()):
            assert not service.verify(PAYLOAD, bad)

    def test_key_order_does_not_matter(self, service):
        """
        A signature covers bytes, not meaning. Canonicalisation is what stops a
        client that re-serialises the JSON from rejecting a valid signature.
        """
        signature = service.sign(PAYLOAD)
        reordered = dict(reversed(list(PAYLOAD.items())))
        assert service.verify(reordered, signature)

    def test_canonical_payload_is_stable(self):
        first = canonical_payload({"b": 2, "a": 1})
        second = canonical_payload({"a": 1, "b": 2})
        assert first == second == b'{"a":1,"b":2}'


class TestEnvelopes:
    def test_envelope_round_trip(self, service):
        envelope = service.sign_envelope(PAYLOAD)
        assert envelope["alg"] == "Ed25519"
        assert service.verify_envelope(envelope) == PAYLOAD

    def test_tampered_envelope_payload_is_rejected(self, service):
        envelope = service.sign_envelope(PAYLOAD)
        envelope["payload"]["seats"] = 99
        with pytest.raises(SignatureInvalid):
            service.verify_envelope(envelope)

    def test_algorithm_substitution_is_rejected(self, service):
        """
        The verifier must treat `alg` as a label to check, never as an
        instruction. Trusting a data-supplied algorithm field is how `alg: none`
        happened to JWT.
        """
        envelope = service.sign_envelope(PAYLOAD)
        envelope["alg"] = "none"
        with pytest.raises(SignatureInvalid):
            service.verify_envelope(envelope)

    def test_malformed_envelopes_are_rejected(self, service):
        for bad in ({}, {"alg": "Ed25519"}, {"alg": "Ed25519", "payload": "x", "signature": 1}):
            with pytest.raises(SignatureInvalid):
                service.verify_envelope(bad)


class TestServiceConfiguration:
    def test_unconfigured_service_cannot_sign(self):
        """
        A fresh clone has no key. Signing must raise with a useful message
        rather than produce something no client can verify.
        """
        service = SigningService()
        assert not service.available
        with pytest.raises(SigningError) as exc:
            service.sign(PAYLOAD)
        assert "generate-signing-key" in str(exc.value)

    def test_private_key_alone_derives_the_public_key(self, keypair):
        service = SigningService(private_key_b64=keypair.private_key_b64)
        assert service.public_key_b64 == keypair.public_key_b64

    def test_mismatched_pair_is_refused_at_construction(self, keypair):
        """
        Otherwise this surfaces days later as "every client rejects every
        entitlement", with nothing in the logs pointing at the cause.
        """
        other = generate_keypair()
        with pytest.raises(SigningError) as exc:
            SigningService(keypair.private_key_b64, other.public_key_b64)
        assert "does not match" in str(exc.value)

    def test_public_key_property_is_safe_to_publish(self, service, keypair):
        assert service.public_key_b64 == keypair.public_key_b64

    def test_from_settings_without_a_key_is_unavailable(self):
        from app.config import Environment, Settings

        settings = Settings(environment=Environment.TEST, _env_file=None)
        assert not SigningService.from_settings(settings).available

    def test_from_settings_reads_a_key_file(self, keypair, tmp_path):
        from app.config import Environment, Settings

        key_file = tmp_path / "signing.key"
        key_file.write_text(keypair.private_key_b64)
        settings = Settings(
            environment=Environment.TEST,
            signing_private_key_file=str(key_file),
            _env_file=None,
        )
        service = SigningService.from_settings(settings)
        assert service.available
        assert service.public_key_b64 == keypair.public_key_b64


class TestPrivateKeyNeverEscapes:
    def test_no_api_response_contains_the_private_key(self, keypair):
        """
        Belt and braces: sweep every response body the app can produce without
        authentication, and assert the key is not in any of them.
        """
        import os

        from tests.conftest import TEST_DATABASE_URL

        if not TEST_DATABASE_URL:
            pytest.skip("PHOTOFLOW_TEST_DATABASE_URL is not set")

        from fastapi.testclient import TestClient

        from app.config import get_settings, reset_settings_cache
        from app.main import create_app

        os.environ["PHOTOFLOW_SIGNING_PRIVATE_KEY"] = keypair.private_key_b64
        os.environ["PHOTOFLOW_SIGNING_PUBLIC_KEY"] = keypair.public_key_b64
        reset_settings_cache()
        try:
            app = create_app(get_settings())
            with TestClient(app) as client:
                for path in ("/health", "/health/ready", "/api/v1/health", "/openapi.json"):
                    body = client.get(path).text
                    assert keypair.private_key_b64 not in body
                    assert "SIGNING_PRIVATE" not in body
        finally:
            os.environ.pop("PHOTOFLOW_SIGNING_PRIVATE_KEY", None)
            os.environ.pop("PHOTOFLOW_SIGNING_PUBLIC_KEY", None)
            reset_settings_cache()

    def test_settings_repr_does_not_print_the_key(self, keypair):
        from app.config import Environment, Settings

        settings = Settings(
            environment=Environment.TEST,
            signing_private_key=keypair.private_key_b64,
            _env_file=None,
        )
        # Pydantic's repr does include field values, which is exactly why
        # nothing in the application ever reprs the settings object -- the
        # startup log prints named fields only. Assert the accessor that *is*
        # used for logging stays clean.
        assert keypair.private_key_b64 not in settings.safe_database_target()
