"""
Password hashing and token tests.

The assertions that matter here are negative ones: that the plaintext password
is not recoverable from what we store, that a token cannot be forged, and that
a refresh token cannot be used as a bearer credential.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
import pytest

from app.config import Environment, Settings
from app.security.passwords import (
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from app.security.tokens import (
    TokenError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_token,
)

PASSWORD = "correct-horse-battery-staple"


def _settings(**overrides) -> Settings:
    base = {
        "environment": Environment.TEST,
        "jwt_secret": "unit-test-secret-long-enough-for-hs256",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


class TestPasswordHashing:
    def test_hash_is_argon2id(self):
        assert hash_password(PASSWORD).startswith("$argon2id$")

    def test_plaintext_never_appears_in_the_hash(self):
        """The single most important property of the whole module."""
        digest = hash_password(PASSWORD)
        assert PASSWORD not in digest
        for fragment in ("correct", "horse", "battery", "staple"):
            assert fragment not in digest

    def test_same_password_hashes_differently_each_time(self):
        """A per-hash salt: identical passwords must not have identical rows."""
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_correct_password_verifies(self):
        assert verify_password(PASSWORD, hash_password(PASSWORD))

    def test_wrong_password_does_not_verify(self):
        assert not verify_password("wrong-password-entirely", hash_password(PASSWORD))

    def test_verification_returns_false_rather_than_raising(self):
        """A malformed or absent hash must not 500 the login endpoint."""
        assert not verify_password(PASSWORD, "not-a-hash")
        assert not verify_password(PASSWORD, "")

    def test_current_parameters_do_not_need_rehash(self):
        assert not needs_rehash(hash_password(PASSWORD))

    def test_garbage_hash_needs_rehash(self):
        assert needs_rehash("$2b$12$something-from-a-previous-life")


class TestPasswordPolicy:
    def test_short_password_is_rejected(self):
        with pytest.raises(PasswordPolicyError):
            validate_password("x" * (MIN_PASSWORD_LENGTH - 1))

    def test_absurdly_long_password_is_rejected(self):
        """Unbounded input into a deliberately slow function is a DoS."""
        with pytest.raises(PasswordPolicyError):
            validate_password("x" * 5000)

    def test_long_password_at_verification_is_false_not_slow(self):
        assert not verify_password("x" * 5000, hash_password(PASSWORD))


class TestAccessTokens:
    def test_round_trip(self):
        settings = _settings()
        user_id = uuid.uuid4()
        token = create_access_token(
            user_id=user_id, role="ADMIN", settings=settings
        )
        claims = decode_access_token(token, settings)
        assert claims.subject == user_id
        assert claims.role == "ADMIN"

    def test_token_signed_with_another_secret_is_rejected(self):
        token = create_access_token(
            user_id=uuid.uuid4(), role="CLIENT", settings=_settings()
        )
        other = _settings(jwt_secret="a-completely-different-secret-value-here")
        with pytest.raises(TokenError):
            decode_access_token(token, other)

    def test_expired_token_is_rejected(self):
        settings = _settings()
        token = create_access_token(
            user_id=uuid.uuid4(),
            role="CLIENT",
            settings=settings,
            expires_delta=timedelta(seconds=-60),
        )
        with pytest.raises(TokenError):
            decode_access_token(token, settings)

    def test_unsigned_alg_none_token_is_rejected(self):
        """The classic JWT forgery: strip the signature, claim alg=none."""
        settings = _settings()
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "role": "ADMIN",
                "type": "access",
                "iss": settings.jwt_issuer,
                "exp": 9999999999,
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(TokenError):
            decode_access_token(forged, settings)

    def test_token_from_another_issuer_is_rejected(self):
        settings = _settings()
        foreign = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "role": "ADMIN",
                "type": "access",
                "iss": "some-other-service",
                "exp": 9999999999,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(TokenError):
            decode_access_token(foreign, settings)

    def test_refresh_typed_token_is_not_accepted_as_access(self):
        settings = _settings()
        wrong_type = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "role": "CLIENT",
                "type": "refresh",
                "iss": settings.jwt_issuer,
                "exp": 9999999999,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(TokenError):
            decode_access_token(wrong_type, settings)

    def test_caller_cannot_override_the_role_claim(self):
        settings = _settings()
        token = create_access_token(
            user_id=uuid.uuid4(),
            role="CLIENT",
            settings=settings,
            extra_claims={"role": "ADMIN", "sub": str(uuid.uuid4())},
        )
        assert decode_access_token(token, settings).role == "CLIENT"


class TestRefreshTokens:
    def test_tokens_are_unique_and_long(self):
        tokens = {generate_refresh_token() for _ in range(50)}
        assert len(tokens) == 50
        assert all(len(token) >= 40 for token in tokens)

    def test_stored_hash_does_not_reveal_the_token(self):
        token = generate_refresh_token()
        digest = hash_token(token)
        assert token not in digest
        assert len(digest) == 64
        assert hash_token(token) == digest  # deterministic, so lookup works
