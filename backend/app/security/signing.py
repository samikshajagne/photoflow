"""
Ed25519 signing for entitlements and release manifests.

Why this exists, and why it is not the JWT secret
-------------------------------------------------
``jwt_secret`` is HS256: symmetric. Whoever can verify a token can also mint
one. That is exactly right for session tokens, which only this backend ever
verifies — and exactly wrong for an entitlement the *desktop application* has to
check while offline. Shipping an HS256 secret inside a Windows binary means
shipping the ability to forge licences to every customer who owns a hex editor.

So entitlements use an asymmetric signature:

    backend  ── private key ──►  signs the entitlement
                                        │
    desktop  ── public key ───►  verifies it, cannot mint one

Ed25519 rather than RSA: 32-byte keys, 64-byte signatures, fast verification on
a laptop, no parameter choices to get wrong, and no padding-mode footguns.

Relationship to ``core/licensing.py`` (read before changing either)
-------------------------------------------------------------------
The desktop app today has **no signature verification at all**. Its HMAC is
something different, and confusing the two would be a mistake:

* ``core/licensing.py`` HMACs the *local state file* — the trial start date, the
  cached expiry — with ``PHOTOFLOW_STATE_KEY``. That stops a customer editing
  their trial expiry in Notepad. Its own docstring is honest that the key ships
  inside the binary and can be extracted, and calls that an accepted trade-off.
* This module signs *server-issued entitlements*. The private key never leaves
  the backend, so an extracted client cannot forge one no matter how thoroughly
  it is disassembled.

They solve different problems and both should exist. The migration path is
additive, and nothing in Phase 3 changes the desktop app:

1. (Phase 3, done here) The backend can generate a keypair, sign, and verify.
2. (Phase 4) ``/api/v1/licenses/validate`` returns a signed entitlement token.
   The desktop app gains an ``core/entitlements`` module holding the **public**
   key and verifying what it receives, cached to disk for offline grace.
3. (Phase 4) ``core/licensing.py``'s ``HttpBackend`` points at the real API. The
   local HMAC stays exactly where it is — it still protects the cached state
   file, which is still worth protecting.
4. (Phase 5) Release manifests get signed with the same key, and the updater
   verifies before executing an installer.

Nothing is ripped out at any step, and a build of the desktop app that predates
all of this keeps working.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.config import Settings, get_settings


class SigningError(RuntimeError):
    """Raised when signing is unavailable or a key is malformed."""


class SignatureInvalid(Exception):
    """Raised when a signature does not verify. Never carries key material."""


@dataclass(frozen=True)
class Keypair:
    """A generated Ed25519 keypair, base64-encoded for transport in env vars."""

    private_key_b64: str
    public_key_b64: str

    def __repr__(self) -> str:  # pragma: no cover - defensive
        # A repr containing a private key would end up in a traceback, a log
        # line, or a debugger session someone screenshots.
        return "<Keypair private=[redacted] public=...>"


def generate_keypair() -> Keypair:
    """
    Create a new Ed25519 keypair.

    Called by ``python -m app.cli generate-signing-key``, never at application
    startup. A key generated on boot would change on every restart and every
    scale-out event, instantly invalidating every entitlement already issued.
    """
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return Keypair(
        private_key_b64=base64.b64encode(private_raw).decode("ascii"),
        public_key_b64=base64.b64encode(public_raw).decode("ascii"),
    )


def load_private_key(encoded: str) -> Ed25519PrivateKey:
    """Decode a base64 private key, or raise :class:`SigningError`."""
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
    except Exception as exc:  # noqa: BLE001 - any decode failure is the same
        raise SigningError("The signing private key is not valid base64.") from exc
    if len(raw) != 32:
        raise SigningError(
            "The signing private key must decode to exactly 32 bytes "
            f"(got {len(raw)})."
        )
    return Ed25519PrivateKey.from_private_bytes(raw)


def load_public_key(encoded: str) -> Ed25519PublicKey:
    """Decode a base64 public key, or raise :class:`SigningError`."""
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SigningError("The signing public key is not valid base64.") from exc
    if len(raw) != 32:
        raise SigningError(
            f"The signing public key must decode to exactly 32 bytes (got {len(raw)})."
        )
    return Ed25519PublicKey.from_public_bytes(raw)


def canonical_payload(payload: dict[str, Any]) -> bytes:
    """
    The exact bytes that get signed.

    Canonical because a signature covers bytes, not meaning: if the server signs
    ``{"a":1,"b":2}`` and the client re-serialises to ``{"b":2,"a":1}`` before
    verifying, a perfectly valid signature fails. Sorted keys, no incidental
    whitespace, UTF-8 — and both sides must use this function or its documented
    equivalent.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


class SigningService:
    """
    Signs payloads with the configured private key.

    Constructed once at startup and held on ``app.state``. When no private key
    is configured — the normal state on a fresh clone — :attr:`available` is
    ``False`` and :meth:`sign` raises rather than silently producing something
    unverifiable. Phase 3 has no endpoint that signs, so an unconfigured
    development machine is fine; Phase 4 will make it a startup requirement.
    """

    def __init__(
        self,
        private_key_b64: str = "",
        public_key_b64: str = "",
    ) -> None:
        self._private = load_private_key(private_key_b64) if private_key_b64 else None
        if public_key_b64:
            self._public = load_public_key(public_key_b64)
        elif self._private is not None:
            self._public = self._private.public_key()
        else:
            self._public = None

        if self._private is not None and public_key_b64:
            # A mismatched pair is a configuration error that would otherwise
            # surface as "every client rejects every entitlement", days later.
            derived = self._private.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            configured = self._public.public_bytes(  # type: ignore[union-attr]
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            if derived != configured:
                raise SigningError(
                    "PHOTOFLOW_SIGNING_PUBLIC_KEY does not match the private key. "
                    "Entitlements signed by this server would be rejected by every "
                    "client holding that public key."
                )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> SigningService:
        settings = settings or get_settings()
        return cls(
            private_key_b64=settings.resolve_signing_private_key(),
            public_key_b64=settings.signing_public_key.strip(),
        )

    @property
    def available(self) -> bool:
        """Whether this process can sign."""
        return self._private is not None

    @property
    def public_key_b64(self) -> str:
        """
        The public key, base64. Safe to publish — this is what gets compiled
        into the desktop application.
        """
        if self._public is None:
            return ""
        raw = self._public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii")

    def sign(self, payload: dict[str, Any]) -> str:
        """Sign a payload, returning a base64 signature."""
        if self._private is None:
            raise SigningError(
                "No Ed25519 signing key is configured. Generate one with "
                "`python -m app.cli generate-signing-key` and set "
                "PHOTOFLOW_SIGNING_PRIVATE_KEY."
            )
        signature = self._private.sign(canonical_payload(payload))
        return base64.b64encode(signature).decode("ascii")

    def verify(self, payload: dict[str, Any], signature_b64: str) -> bool:
        """
        Verify a signature against a payload.

        The backend does not *need* to verify its own signatures in production —
        that is the client's job — but having it here means the round trip is
        testable, and the same code documents exactly what the desktop app must
        implement.
        """
        if self._public is None:
            raise SigningError("No Ed25519 public key is configured.")
        try:
            signature = base64.b64decode(signature_b64.strip(), validate=True)
        except Exception:  # noqa: BLE001 - malformed input is simply invalid
            return False
        try:
            self._public.verify(signature, canonical_payload(payload))
        except InvalidSignature:
            return False
        return True

    def sign_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        A payload plus its signature, in the shape the desktop app will consume.

        ``{"payload": {...}, "signature": "base64", "alg": "Ed25519"}``

        ``alg`` is recorded so a future key rotation or algorithm change is
        detectable by an old client rather than being silently misread — but the
        verifier must treat it as a label to *check*, never as an instruction
        about which algorithm to use. Trusting an algorithm field supplied by the
        data is how ``alg: none`` happened to JWT.
        """
        return {
            "payload": payload,
            "signature": self.sign(payload),
            "alg": "Ed25519",
        }

    def verify_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """
        Check an envelope and return its payload, or raise
        :class:`SignatureInvalid`.
        """
        if not isinstance(envelope, dict):
            raise SignatureInvalid("Malformed envelope.")
        if envelope.get("alg") != "Ed25519":
            raise SignatureInvalid("Unexpected signature algorithm.")
        payload = envelope.get("payload")
        signature = envelope.get("signature")
        if not isinstance(payload, dict) or not isinstance(signature, str):
            raise SignatureInvalid("Malformed envelope.")
        if not self.verify(payload, signature):
            raise SignatureInvalid("Signature does not verify.")
        return payload
