"""
Licence state for PhotoFlow: trial, activation, offline grace.

What this module is honest about
-------------------------------
**Client-side licensing cannot be made unbreakable.** Anything the application
can check, a determined person can patch out, because they control the machine
the code runs on. This module is therefore designed as a *fair-use mechanism*,
not a copy-protection scheme:

* it stops casual sharing of one key across a studio chain,
* it gives you an accurate picture of who is actively using the product,
* it never punishes a paying customer for being offline.

If you need stronger enforcement, the only real answer is server-side: keep
something the customer needs (their account, their cloud proofing) behind
authentication. Piracy of a desktop tool is better treated as a pricing and
convenience problem than an engineering one — see the pricing notes in
``docs/PRODUCT_IDEA_CATALOGUE.md``.

The local state file is signed with an HMAC so it cannot be edited by hand
(changing the expiry date in a text editor won't work). The signing key lives in
the binary, so a motivated attacker can extract it; that is a deliberate,
accepted trade-off and not a bug to be "fixed" with obfuscation.

Design decisions that matter for real studios
---------------------------------------------
* **Offline grace period.** A wedding photographer's laptop may be off the
  internet for days. A licence check that fails hard when offline would break a
  customer's business at the worst possible moment. Once activated, PhotoFlow
  keeps working for :data:`GRACE_DAYS` without contact, and only then asks to
  re-validate.
* **Failure is never fatal.** Network errors, a down server, a corrupt state
  file: all degrade to "keep working, try again later", never to "refuse to
  start". The worst outcome of a bug here should be a missed revalidation, not a
  studio unable to deliver an album.
* **The backend is pluggable.** :class:`LicenseBackend` is a small protocol, so
  the same client works against Keygen, Cryptlex, a Paddle licence endpoint, or
  your own server, without touching this file.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import platform
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

from utils.logger import get_logger
from utils.paths import user_data_dir
from utils.version import __version__

logger = get_logger(__name__)

# Free trial length from first launch.
TRIAL_DAYS = 14
# How long an activated licence keeps working without reaching the server.
GRACE_DAYS = 21
# How often to attempt a background revalidation.
RECHECK_DAYS = 7

# Placeholder signing key. The real one must NEVER live in the repository --
# this repo has a public remote, and a secret committed even once stays in git
# history forever. See _signing_key() for how the real value is supplied.
_PLACEHOLDER_SIGNING_KEY = b"photoflow-local-state-v1-replace-me"


def _signing_key() -> bytes:
    """
    The HMAC key protecting the local licence state file, in priority order:

    1. ``$PHOTOFLOW_STATE_KEY`` — for development and CI.
    2. ``utils._secrets.STATE_SIGNING_KEY`` — a gitignored module written at
       build time by ``packaging/make_secrets.py``, so the real key is baked into
       the frozen executable without ever being committed.
    3. The placeholder — so a fresh clone runs without any setup.

    Using the placeholder is fine for development and for the free beta. Before
    charging money, generate a real key (``python packaging/make_secrets.py``);
    ``packaging/preflight.py`` warns when a build would ship the placeholder.

    Reminder from the module docstring: this only deters *hand-editing* of the
    state file. Since the key ships inside the binary, someone determined can
    extract it. That is an accepted trade-off, not a defect.
    """
    from os import environ

    from_env = environ.get("PHOTOFLOW_STATE_KEY")
    if from_env:
        return from_env.encode("utf-8")
    try:
        from utils._secrets import STATE_SIGNING_KEY  # type: ignore[attr-defined]

        if STATE_SIGNING_KEY:
            return (
                STATE_SIGNING_KEY.encode("utf-8")
                if isinstance(STATE_SIGNING_KEY, str)
                else STATE_SIGNING_KEY
            )
    except Exception:  # noqa: BLE001 - absent in a normal checkout, which is fine
        pass
    return _PLACEHOLDER_SIGNING_KEY


def using_placeholder_key() -> bool:
    """True when no real signing key has been supplied (used by preflight)."""
    return _signing_key() == _PLACEHOLDER_SIGNING_KEY

STATE_UNLICENSED = "unlicensed"
STATE_TRIAL = "trial"
STATE_TRIAL_EXPIRED = "trial_expired"
STATE_ACTIVE = "active"
STATE_GRACE = "grace"
STATE_EXPIRED = "expired"


class LicenseError(Exception):
    """Raised for unrecoverable licence problems (bad input, refused activation)."""


# --------------------------------------------------------------------------- #
# Machine identity
# --------------------------------------------------------------------------- #
def machine_fingerprint() -> str:
    """
    A stable, non-identifying id for this computer.

    Built from the hostname, MAC address and platform, then hashed -- so what
    leaves the machine is an opaque digest rather than anything that describes
    the customer. It's stable across reboots and app updates, which is what
    seat-counting needs, and it changes if the machine is replaced, which is
    also correct.

    Deliberately *not* used: anything resembling a personal identifier. Under
    India's DPDP Act and the GDPR, the less personal data you collect the
    smaller your obligations, and a hash is enough to count seats.
    """
    parts = [
        platform.node() or "",
        str(uuid.getnode()),
        platform.machine() or "",
        platform.system() or "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Local state
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class LicenseState:
    """
    What we know locally about this installation's licence.

    Dates are ISO ``YYYY-MM-DD`` strings so the file stays human-readable and
    JSON-safe.
    """

    first_run: str = ""
    key: str = ""
    activated_on: str = ""
    last_validated: str = ""
    expires_on: str = ""          # "" means a perpetual licence
    machine: str = ""
    seats: int = 0
    customer: str = ""
    # The customer's plan/tier, if the backend's response includes one. The
    # production activate/validate response does not today, so this stays ""
    # until it does -- the Account & Licence UI shows that as "unknown" rather
    # than a made-up default.
    plan: str = ""
    # The backend's id for this licence. Only needed to call /deactivate, which
    # (unlike activate/validate) is keyed by id rather than by the licence key.
    license_id: str = ""
    telemetry_consent: Optional[bool] = None  # None = not yet asked

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LicenseState":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


def _sign(payload: str) -> str:
    return hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def state_path() -> Path:
    """Where the licence state file lives (per-user, writable)."""
    return user_data_dir() / "license.json"


def load_state(path: Optional[Path] = None) -> LicenseState:
    """
    Read the licence state, or return a fresh one.

    A missing, unreadable, malformed or **tampered** file all produce a fresh
    state rather than an exception: refusing to start because a state file got
    corrupted would be a far worse failure than falling back to trial.
    """
    file = path or state_path()
    if not file.exists():
        return LicenseState()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
        payload = raw.get("state", {})
        signature = raw.get("signature", "")
        encoded = json.dumps(payload, sort_keys=True)
        if not hmac.compare_digest(signature, _sign(encoded)):
            logger.warning("Licence state signature did not match; ignoring the file.")
            return LicenseState()
        return LicenseState.from_dict(payload)
    except Exception as exc:  # noqa: BLE001 - never fail to start over this
        logger.warning("Could not read licence state (%s); starting fresh.", exc)
        return LicenseState()


def save_state(state: LicenseState, path: Optional[Path] = None) -> Path:
    """Write the licence state, signed, via a temp file so it can't be truncated."""
    file = path or state_path()
    payload = state.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    body = {"state": payload, "signature": _sign(encoded), "app_version": __version__}
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
        tmp.replace(file)
    except OSError as exc:
        raise LicenseError(f"Could not save licence state to '{file}': {exc}") from exc
    return file


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class ActivationResult:
    """Outcome of an activation or validation attempt."""

    ok: bool
    message: str = ""
    expires_on: str = ""
    seats: int = 0
    customer: str = ""
    # Wired from the backend response's "plan" field if/when it sends one
    # (see LicenseState.plan). Never fabricated client-side.
    plan: str = ""
    # True when the server was unreachable (as opposed to refusing the key).
    offline: bool = False
    # The backend's id for this licence (empty for OfflineBackend and for
    # deactivate, which doesn't return one). Persisted so a later deactivation
    # can identify the licence to the server.
    license_id: str = ""


class LicenseBackend(Protocol):
    """
    The server side of licensing, as far as the app is concerned.

    Implement this against Keygen, Cryptlex, Paddle or your own API. Keeping the
    surface this small is what lets you switch provider later without touching
    the rest of the application.
    """

    def activate(self, key: str, machine: str) -> ActivationResult: ...

    def validate(self, key: str, machine: str) -> ActivationResult: ...

    def deactivate(self, license_id: str, machine: str) -> ActivationResult: ...


class OfflineBackend:
    """
    A backend for development and for builds with no licence server yet.

    Accepts any key of a plausible shape and reports it as a perpetual licence.
    Useful so the rest of the app can be built and tested before the server
    exists -- **not** something to ship as your real enforcement.
    """

    MIN_KEY_LENGTH = 8

    def activate(self, key: str, machine: str) -> ActivationResult:
        key = (key or "").strip()
        if len(key) < self.MIN_KEY_LENGTH:
            return ActivationResult(ok=False, message="That licence key looks too short.")
        return ActivationResult(ok=True, message="Activated.", seats=1, customer="")

    def validate(self, key: str, machine: str) -> ActivationResult:
        return self.activate(key, machine)

    def deactivate(self, license_id: str, machine: str) -> ActivationResult:
        # There is no server to tell; freeing the seat locally (handled by
        # LicenseManager.deactivate) is the whole operation in offline mode.
        return ActivationResult(ok=True, message="Deactivated.")


class HttpBackend:
    """
    Talks to the production PhotoFlow licence API
    (``backend/app/api/v1/licenses.py``).

    Expects ``POST {base_url}/activate``, ``/validate`` and ``/deactivate``.
    Every request carries the signed-in customer's access token, obtained by
    calling ``token_provider()`` -- a zero-argument callable that should be
    ``AuthManager.ensure_access_token`` so a merely-expired token is refreshed
    transparently and no authentication logic is duplicated here. When a
    ``token_provider`` is configured but returns no token (nobody is signed
    in), the request is skipped entirely and a plain, non-offline refusal is
    returned -- there is nothing wrong with the licence, so it must not be
    reported as "offline" or crash the caller.

    Network failure returns ``offline=True`` rather than ``ok=False``: the
    caller must be able to tell "your key is invalid" from "I couldn't ask",
    because those deserve very different behaviour. A request the server
    *did* receive and refuse (bad key, wrong owner, seat limit, not signed
    in) is a normal, reachable refusal -- not "offline" -- so it is reported
    the same way.
    """

    def __init__(
        self,
        base_url: str,
        token_provider: Optional[Callable[[], Optional[str]]] = None,
        timeout: float = 8.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_provider = token_provider
        self.timeout = timeout

    def _post(self, endpoint: str, payload: dict) -> ActivationResult:
        import urllib.error
        import urllib.request

        token: Optional[str] = None
        if self.token_provider is not None:
            token = self.token_provider()
            if not token:
                return ActivationResult(
                    ok=False,
                    message="You need to be signed in to manage your licence. "
                            "Please log in and try again.",
                )

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{endpoint}",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The server was reached; it refused the request. This is an
            # HTTPError (a URLError subclass) but must NOT fall into the
            # offline branch below, or a bad key/seat limit/ownership refusal
            # would be misreported to the user as "couldn't reach the server".
            try:
                error_body = json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001 - malformed or empty error body
                error_body = {}
            return ActivationResult(
                ok=False,
                message=str(
                    error_body.get("detail")
                    or error_body.get("message")
                    or "The licence server rejected this request."
                ),
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.info("Licence server unreachable (%s).", exc)
            return ActivationResult(ok=False, offline=True,
                                    message="Could not reach the licence server.")
        except Exception as exc:  # noqa: BLE001 - malformed response
            logger.warning("Unexpected licence server response (%s).", exc)
            return ActivationResult(ok=False, offline=True,
                                    message="Unexpected response from the licence server.")

        return ActivationResult(
            ok=bool(data.get("ok")),
            message=str(data.get("message", "")),
            expires_on=str(data.get("expires_on", "")),
            seats=int(data.get("seats", 0) or 0),
            customer=str(data.get("customer", "")),
            plan=str(data.get("plan", "")),
            license_id=str(data.get("license_id", "")),
        )

    def activate(self, key: str, machine: str) -> ActivationResult:
        return self._post("activate", {
            "key": key, "machine": machine, "product": "photoflow", "version": __version__,
        })

    def validate(self, key: str, machine: str) -> ActivationResult:
        return self._post("validate", {
            "key": key, "machine": machine, "product": "photoflow", "version": __version__,
        })

    def deactivate(self, license_id: str, machine: str) -> ActivationResult:
        return self._post("deactivate", {"license_id": license_id, "machine": machine})


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class LicenseStatus:
    """A plain-language answer to "can this person use the app right now?"."""

    state: str
    days_left: int = 0
    message: str = ""
    key: str = ""
    customer: str = ""

    @property
    def usable(self) -> bool:
        """
        Whether the app should let the user work.

        Note that even ``trial_expired`` and ``expired`` are *not* enforced by
        this module -- it reports; the UI decides. That separation keeps the
        "never break a studio mid-job" policy in one visible place.
        """
        return self.state in (STATE_TRIAL, STATE_ACTIVE, STATE_GRACE)

    @property
    def should_nag(self) -> bool:
        """True when the UI should prompt about the licence."""
        return self.state in (STATE_TRIAL_EXPIRED, STATE_EXPIRED) or (
            self.state == STATE_TRIAL and self.days_left <= 3
        )


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _parse(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class LicenseManager:
    """
    Ties the state file, the backend and the trial/grace rules together.

    Typical use at startup::

        manager = LicenseManager()
        status = manager.status()       # starts the trial on first ever run
        if status.should_nag:
            show_license_dialog(manager)
    """

    def __init__(
        self,
        backend: Optional[LicenseBackend] = None,
        path: Optional[Path] = None,
    ) -> None:
        self.backend: LicenseBackend = backend or OfflineBackend()
        self._path = path
        self.state = load_state(path)

    # -- trial ------------------------------------------------------------- #
    def ensure_first_run(self) -> None:
        """Record the first-run date, which is what the trial counts from."""
        if not self.state.first_run:
            self.state.first_run = _today().isoformat()
            try:
                save_state(self.state, self._path)
            except LicenseError as exc:
                # A read-only disk shouldn't stop the app; the trial just
                # restarts next launch, which errs in the customer's favour.
                logger.warning("Could not record first run (%s).", exc)

    def trial_days_left(self) -> int:
        started = _parse(self.state.first_run)
        if started is None:
            return TRIAL_DAYS
        used = (_today() - started).days
        return max(0, TRIAL_DAYS - used)

    # -- status ------------------------------------------------------------ #
    def status(self) -> LicenseStatus:
        """Current licence status, starting the trial clock if needed."""
        self.ensure_first_run()

        if self.state.key and self.state.activated_on:
            expiry = _parse(self.state.expires_on)
            if expiry is not None and expiry < _today():
                return LicenseStatus(
                    state=STATE_EXPIRED, key=self.state.key,
                    customer=self.state.customer,
                    message="Your licence has expired.",
                )

            last = _parse(self.state.last_validated) or _parse(self.state.activated_on)
            days_since = (_today() - last).days if last else 0
            if days_since > GRACE_DAYS:
                return LicenseStatus(
                    state=STATE_EXPIRED, key=self.state.key,
                    customer=self.state.customer,
                    message=(
                        f"PhotoFlow hasn't been able to check your licence for "
                        f"{days_since} days. Please connect to the internet once."
                    ),
                )
            if days_since > RECHECK_DAYS:
                return LicenseStatus(
                    state=STATE_GRACE, days_left=GRACE_DAYS - days_since,
                    key=self.state.key, customer=self.state.customer,
                    message="Working offline — your licence will re-check when you're online.",
                )
            return LicenseStatus(
                state=STATE_ACTIVE, key=self.state.key,
                customer=self.state.customer, message="Licence active.",
            )

        left = self.trial_days_left()
        if left > 0:
            return LicenseStatus(
                state=STATE_TRIAL, days_left=left,
                message=f"Trial — {left} day{'s' if left != 1 else ''} left.",
            )
        return LicenseStatus(
            state=STATE_TRIAL_EXPIRED,
            message="Your free trial has ended. Enter a licence key to continue.",
        )

    # -- activation -------------------------------------------------------- #
    def activate(self, key: str) -> ActivationResult:
        """
        Activate ``key`` for this machine and persist the result.

        On success the key, date and machine are stored so later launches work
        offline. A refusal is returned unchanged for the UI to show; an
        unreachable server is reported with ``offline=True`` so the UI can say
        "check your connection" rather than "your key is wrong".
        """
        key = (key or "").strip()
        if not key:
            return ActivationResult(ok=False, message="Enter a licence key.")

        result = self.backend.activate(key, machine_fingerprint())
        if not result.ok:
            return result

        today = _today().isoformat()
        self.state.key = key
        self.state.activated_on = today
        self.state.last_validated = today
        self.state.expires_on = result.expires_on
        self.state.machine = machine_fingerprint()
        self.state.seats = result.seats
        self.state.customer = result.customer
        self.state.plan = result.plan
        self.state.license_id = result.license_id
        try:
            save_state(self.state, self._path)
        except LicenseError as exc:
            logger.warning("Activated but could not save licence state (%s).", exc)
        return result

    def revalidate(self, force: bool = False) -> Optional[ActivationResult]:
        """
        Re-check an activated licence, at most every :data:`RECHECK_DAYS`.

        Returns ``None`` when no check was due or there's nothing to check.
        **A failed check does not deactivate anything** — the grace period in
        :meth:`status` handles that, so a server outage on your side can never
        lock out a paying customer.
        """
        if not self.state.key:
            return None
        last = _parse(self.state.last_validated)
        if not force and last and (_today() - last).days < RECHECK_DAYS:
            return None

        result = self.backend.validate(self.state.key, machine_fingerprint())
        if result.ok:
            self.state.last_validated = _today().isoformat()
            if result.expires_on:
                self.state.expires_on = result.expires_on
            try:
                save_state(self.state, self._path)
            except LicenseError as exc:
                logger.warning("Could not save licence state after revalidation (%s).", exc)
        elif result.offline:
            logger.info("Licence revalidation skipped: offline.")
        else:
            # An explicit refusal (key revoked, seat limit) is worth recording,
            # but we still don't hard-fail here -- status() decides.
            logger.warning("Licence revalidation refused: %s", result.message)
        return result

    def deactivate(self) -> None:
        """
        Clear the stored licence (for moving a seat to another machine).

        Tells the backend first, so the server frees the seat immediately
        rather than waiting for the activation to simply go stale. A backend
        that can't or won't do this -- ``OfflineBackend``, a server that's
        unreachable, a test double with no ``deactivate`` method -- is not
        treated as an error: local state is cleared unconditionally, so the
        UI is never left stuck because of a network problem.
        """
        backend_deactivate = getattr(self.backend, "deactivate", None)
        if callable(backend_deactivate) and self.state.key:
            try:
                backend_deactivate(self.state.license_id, machine_fingerprint())
            except Exception as exc:  # noqa: BLE001 - local deactivation must proceed regardless
                logger.info("Backend deactivation skipped/failed (%s).", exc)

        self.state.key = ""
        self.state.activated_on = ""
        self.state.last_validated = ""
        self.state.expires_on = ""
        self.state.seats = 0
        self.state.customer = ""
        self.state.plan = ""
        self.state.license_id = ""
        try:
            save_state(self.state, self._path)
        except LicenseError as exc:
            logger.warning("Could not save licence state after deactivation (%s).", exc)

    # -- telemetry consent -------------------------------------------------- #
    def telemetry_consent(self) -> Optional[bool]:
        """``True``/``False`` once the user has chosen, else ``None``."""
        return self.state.telemetry_consent

    def set_telemetry_consent(self, allowed: bool) -> None:
        self.state.telemetry_consent = bool(allowed)
        try:
            save_state(self.state, self._path)
        except LicenseError as exc:
            logger.warning("Could not save telemetry consent (%s).", exc)
