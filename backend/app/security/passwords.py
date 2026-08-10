"""
Password hashing.

Argon2id, via ``argon2-cffi`` directly. Two deliberate choices:

* **Argon2id rather than bcrypt.** It is the Password Hashing Competition winner
  and the OWASP first recommendation, it has no 72-byte input truncation, and its
  memory cost makes GPU cracking expensive rather than merely slow.
* **argon2-cffi directly rather than passlib.** Passlib has been effectively
  unmaintained since 2020 and its bcrypt backend breaks against modern releases
  of the ``bcrypt`` package. One less abstraction over one algorithm we have
  already chosen is the simpler dependency.

The parameters below are the argon2-cffi defaults, which follow the RFC 9106
low-memory recommendation (64 MiB, t=3, p=4). They are exposed as module
constants so that a future increase is a one-line change plus a rehash-on-login,
which :func:`needs_rehash` already supports.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# RFC 9106 second recommended option, as shipped by argon2-cffi.
TIME_COST = 3
MEMORY_COST_KIB = 65536  # 64 MiB
PARALLELISM = 4
HASH_LENGTH = 32
SALT_LENGTH = 16

# A password longer than this is rejected rather than hashed: Argon2 has no
# length limit, so an unbounded input is a cheap denial-of-service against a
# deliberately expensive function.
MAX_PASSWORD_LENGTH = 1024
MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher(
    time_cost=TIME_COST,
    memory_cost=MEMORY_COST_KIB,
    parallelism=PARALLELISM,
    hash_len=HASH_LENGTH,
    salt_len=SALT_LENGTH,
)


class PasswordPolicyError(ValueError):
    """Raised when a candidate password fails the length policy."""


def validate_password(password: str) -> None:
    """
    Check length bounds only.

    Length is the requirement with actual evidence behind it. Composition rules
    ("one uppercase, one symbol") measurably push people towards ``Password1!``
    and are no longer recommended by NIST SP 800-63B. Breach-corpus checking is
    the useful addition, and belongs in Phase 3 alongside the real signup flow.
    """
    if password is None:
        raise PasswordPolicyError("A password is required.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
        )


def hash_password(password: str) -> str:
    """Return an Argon2id encoded hash. Never returns the password."""
    validate_password(password)
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Constant-time-ish verification that never raises on a wrong password.

    Returns ``False`` for a mismatch, a malformed stored hash, or a user with no
    password set (``password_hash`` empty) -- the caller should not have to tell
    those apart, and distinguishing them in a response is exactly how account
    enumeration happens.
    """
    if not password_hash or password is None:
        return False
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """
    True when the stored hash used weaker parameters than the current policy.

    Call this after a successful login: it is the only moment the plaintext is
    available to re-hash with stronger settings.
    """
    if not password_hash:
        return False
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
