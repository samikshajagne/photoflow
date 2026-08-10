"""
Opt-in, aggregate-only usage reporting.

PhotoFlow's core promise to customers is that their clients' photos stay on
their machine. This module exists to give the business useful information
*without* breaking that promise, so its boundaries are deliberately narrow and
enforced in code rather than left to good intentions:

**What it may send**
    - counts of actions ("14 albums built", "40 ID sheets exported")
    - which of the three modes get used
    - app version, OS name and version
    - an opaque machine hash (the same one licensing uses)

**What it must never send** — and :func:`record` actively rejects:
    - file names, folder names or any path
    - image data, thumbnails or crops
    - people's names, face data or embeddings
    - anything typed by the user (titles, captions, client names)

Three rules this module follows
-------------------------------
1. **Off unless the customer says yes.** No consent, no collection. Not
   "opt-out", not "on by default for beta" -- off.
2. **Counts, never content.** Events carry a name and a number. There is no
   free-text field to leak into, by design.
3. **Never in the way.** All network work is best-effort and silent; a failure to
   report usage must never surface to the customer or slow the app down.

Legal note (not legal advice): under India's DPDP Act 2023 all personal data
needs notice and consent, and the GDPR treats an online identifier as personal
data. Aggregate counters plus a hashed machine id keep this as close to
non-personal as is practical -- but the consent prompt is still required, which
is why :class:`Telemetry` refuses to work without one.
"""

from __future__ import annotations

import dataclasses
import json
import platform
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.logger import get_logger
from utils.paths import user_data_dir
from utils.version import __version__

logger = get_logger(__name__)

# Only these event names may be recorded. A closed vocabulary is the mechanism
# that keeps "we only send counts" true as the app grows: adding a new metric
# is a deliberate edit here, not something that happens by accident.
ALLOWED_EVENTS: frozenset[str] = frozenset({
    "app_launched",
    "mode_album",
    "mode_passport",
    "mode_collage",
    "album_built",
    "album_exported",
    "passport_sheet_exported",
    "passport_beautify_used",
    "collage_built",
    "collage_exported",
    "collage_auto_build_used",
    "collage_shape_used",
    "preset_saved",
    "crash",
})

# Flushed to the server when the queue reaches this many events, so a busy day
# doesn't turn into one request per click.
_FLUSH_AT = 25


class TelemetryError(Exception):
    """Raised only for programming mistakes, e.g. an unknown event name."""


@dataclasses.dataclass
class TelemetryPayload:
    """Exactly what would be sent. Small enough to show a customer verbatim."""

    machine: str
    app_version: str
    os_name: str
    os_version: str
    counts: dict[str, int]
    generated_at: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def counters_path() -> Path:
    return user_data_dir() / "usage_counters.json"


class Telemetry:
    """
    Counts things, if allowed to.

    Args:
        consent: ``True`` to collect, ``False``/``None`` to do nothing. Pass
            ``LicenseManager.telemetry_consent()`` straight in.
        endpoint: Where to POST aggregates. ``None`` keeps everything local,
            which is a perfectly reasonable way to run this at first -- the
            counters file is still readable if a customer sends it to support.
        machine: Hashed machine id; defaults to the licensing fingerprint.
    """

    def __init__(
        self,
        consent: Optional[bool] = None,
        endpoint: Optional[str] = None,
        machine: Optional[str] = None,
        path: Optional[Path] = None,
    ) -> None:
        self.consent = bool(consent)
        self.endpoint = endpoint
        self._path = path or counters_path()
        self._lock = threading.Lock()
        self._machine = machine
        self.counts: dict[str, int] = {}
        if self.consent:
            self.counts = self._load()

    # -- identity ---------------------------------------------------------- #
    @property
    def machine(self) -> str:
        if self._machine is None:
            from core.licensing import machine_fingerprint

            self._machine = machine_fingerprint()
        return self._machine

    # -- recording --------------------------------------------------------- #
    def record(self, event: str, count: int = 1) -> None:
        """
        Increment a counter.

        Raises:
            TelemetryError: if ``event`` isn't in :data:`ALLOWED_EVENTS`. That's
                a programming error worth surfacing loudly in development --
                it's the guard that keeps arbitrary data out of the payload.

        Does nothing at all without consent, including not touching the disk.
        """
        if event not in ALLOWED_EVENTS:
            raise TelemetryError(
                f"Unknown telemetry event {event!r}. Add it to ALLOWED_EVENTS "
                "deliberately -- this list is what keeps telemetry to counts only."
            )
        if not self.consent:
            return
        with self._lock:
            self.counts[event] = self.counts.get(event, 0) + max(0, int(count))
            total = sum(self.counts.values())
            self._save()
        if self.endpoint and total >= _FLUSH_AT:
            self.flush()

    # -- payload ----------------------------------------------------------- #
    def payload(self) -> TelemetryPayload:
        """The exact aggregate that would be sent."""
        return TelemetryPayload(
            machine=self.machine,
            app_version=__version__,
            os_name=platform.system(),
            os_version=platform.release(),
            counts=dict(self.counts),
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def describe(self) -> str:
        """
        Human-readable summary of what would be sent.

        Worth showing in the consent dialog: "here is literally everything"
        earns more trust than a paragraph of policy, and it stays honest
        automatically because it's generated from the real payload.
        """
        data = self.payload()
        lines = [
            f"App version: {data.app_version}",
            f"System: {data.os_name} {data.os_version}",
            f"Anonymous machine id: {data.machine[:12]}…",
            "Usage counts:",
        ]
        if data.counts:
            lines += [f"  · {name}: {value}" for name, value in sorted(data.counts.items())]
        else:
            lines.append("  · (nothing recorded yet)")
        lines.append("No file names, photos, or personal details are included.")
        return "\n".join(lines)

    # -- sending ----------------------------------------------------------- #
    def flush(self) -> bool:
        """
        Send the aggregate, best effort. Returns whether it was accepted.

        Runs on a daemon thread and swallows every error: usage reporting is
        never worth delaying or interrupting a customer's work for.
        """
        if not self.consent or not self.endpoint or not self.counts:
            return False
        data = self.payload().to_dict()

        def _send() -> None:
            import urllib.error
            import urllib.request

            try:
                request = urllib.request.Request(
                    self.endpoint,
                    data=json.dumps(data).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=6.0):
                    pass
                with self._lock:
                    # Only clear what we actually sent, so anything recorded
                    # mid-flight isn't silently lost.
                    for name, value in data["counts"].items():
                        self.counts[name] = max(0, self.counts.get(name, 0) - value)
                    self._save()
            except Exception as exc:  # noqa: BLE001 - deliberately swallowed
                logger.debug("Telemetry flush failed (%s); will retry later.", exc)

        threading.Thread(target=_send, name="telemetry-flush", daemon=True).start()
        return True

    # -- persistence -------------------------------------------------------- #
    def _load(self) -> dict[str, int]:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return {
                    str(k): int(v)
                    for k, v in raw.get("counts", {}).items()
                    if k in ALLOWED_EVENTS
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read usage counters (%s).", exc)
        return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"counts": self.counts}, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.debug("Could not save usage counters (%s).", exc)

    def clear(self) -> None:
        """Forget everything recorded (used when consent is withdrawn)."""
        with self._lock:
            self.counts = {}
            try:
                self._path.unlink(missing_ok=True)
            except OSError as exc:  # noqa: BLE001
                logger.debug("Could not delete usage counters (%s).", exc)


# A module-level instance the app configures once at startup, so feature code
# can call `telemetry().record(...)` without threading an object everywhere.
_instance: Optional[Telemetry] = None


def configure(consent: Optional[bool], endpoint: Optional[str] = None) -> Telemetry:
    """Create/replace the shared instance. Call once, at startup."""
    global _instance
    _instance = Telemetry(consent=consent, endpoint=endpoint)
    return _instance


def telemetry() -> Telemetry:
    """
    The shared instance, defaulting to **no consent** if never configured.

    Defaulting to off means forgetting to call :func:`configure` results in no
    collection, rather than silent collection without permission.
    """
    global _instance
    if _instance is None:
        _instance = Telemetry(consent=False)
    return _instance
