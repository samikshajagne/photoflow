"""
Rate limiting for authentication endpoints.

An endpoint that checks a password is an oracle: given unlimited attempts, any
password falls eventually. Rate limiting is what turns "eventually" into "not
within the lifetime of this business", and it is the one control here that
cannot be replaced by writing the rest of the code more carefully.

Design, and the trade-off being made
------------------------------------
:class:`RateLimitBackend` is the seam. Two implementations are anticipated:

* :class:`InMemoryRateLimitBackend` -- a fixed-window counter in a process-local
  dict. Correct, fast, zero dependencies, and **per process**. With two backend
  instances behind a load balancer, the effective limit doubles, silently.
* A Redis backend -- the same interface over a shared store, so the limit is
  global regardless of instance count.

Redis is deliberately *not* added today. PhotoFlow has no customers yet and will
launch on a single instance; adding a second network dependency now buys nothing
and adds an outage mode (Redis down ⇒ can anyone log in?) that would have to be
designed around. What matters is that the decision stays visible: production
refuses to start on the memory backend unless
``PHOTOFLOW_ALLOW_SINGLE_INSTANCE_RATE_LIMIT=true`` says someone knows. When the
second instance appears, implement ``RedisRateLimitBackend`` against this
interface and change one setting.

A fixed window rather than a sliding one, also deliberately: a fixed window lets
through at most 2× the limit across a window boundary, which for "5 login
attempts per 5 minutes" means a worst case of 10 -- irrelevant against Argon2id,
and much simpler to reason about than a sliding log.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RateLimitVerdict:
    """The outcome of one limiter consultation."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int

    @property
    def exceeded(self) -> bool:
        return not self.allowed


class RateLimitBackend(Protocol):
    """
    Storage for attempt counters.

    ``hit`` records an attempt against ``key`` and reports whether it is within
    ``limit`` for ``window_seconds``. ``reset`` clears a key, which is what a
    successful login does so a legitimate user who mistyped their password four
    times is not left near the ceiling.
    """

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitVerdict: ...

    def reset(self, key: str) -> None: ...


class InMemoryRateLimitBackend:
    """
    Process-local fixed-window counters.

    Thread-safe because FastAPI runs synchronous endpoints in a threadpool, so
    two login requests genuinely can land on this dict at the same moment.

    Expired entries are swept opportunistically rather than by a background
    task: the sweep is O(n) over a dict that only holds keys seen within the
    last window, and a background thread in a web process is a lifecycle problem
    nobody wants to own.
    """

    _SWEEP_EVERY_SECONDS = 60

    def __init__(self) -> None:
        self._counters: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitVerdict:
        now = time.monotonic()
        with self._lock:
            self._maybe_sweep(now)
            count, window_start = self._counters.get(key, (0, now))
            if now - window_start >= window_seconds:
                count, window_start = 0, now
            count += 1
            self._counters[key] = (count, window_start)

        elapsed = now - window_start
        retry_after = max(1, int(window_seconds - elapsed))
        return RateLimitVerdict(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after_seconds=retry_after,
        )

    def reset(self, key: str) -> None:
        with self._lock:
            self._counters.pop(key, None)

    def clear(self) -> None:
        """Drop every counter. Tests only."""
        with self._lock:
            self._counters.clear()

    def _maybe_sweep(self, now: float) -> None:
        """Discard entries older than an hour. Caller holds the lock."""
        if now - self._last_sweep < self._SWEEP_EVERY_SECONDS:
            return
        self._last_sweep = now
        cutoff = now - 3600
        stale = [key for key, (_, start) in self._counters.items() if start < cutoff]
        for key in stale:
            del self._counters[key]


class NullRateLimitBackend:
    """
    Allows everything.

    Used when ``PHOTOFLOW_RATE_LIMIT_ENABLED=false`` -- which is a legitimate
    thing to want while iterating on a laptop, and which production configuration
    validation refuses.
    """

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitVerdict:
        return RateLimitVerdict(True, limit, limit, 0)

    def reset(self, key: str) -> None:
        return None


class RateLimiter:
    """
    The application-facing limiter.

    Keys are namespaced by scope (``login``, ``refresh``) so a burst of refreshes
    cannot exhaust a user's login budget, and are hashed identity strings rather
    than raw email addresses -- the counter store should not become a list of
    which email addresses exist.
    """

    def __init__(self, backend: RateLimitBackend) -> None:
        self._backend = backend

    @staticmethod
    def key(scope: str, identifier: str) -> str:
        import hashlib

        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
        return f"{scope}:{digest}"

    def check(
        self, scope: str, identifier: str, *, limit: int, window_seconds: int
    ) -> RateLimitVerdict:
        return self._backend.hit(self.key(scope, identifier), limit, window_seconds)

    def reset(self, scope: str, identifier: str) -> None:
        self._backend.reset(self.key(scope, identifier))


def build_backend(settings) -> RateLimitBackend:
    """Construct the configured backend."""
    if not settings.rate_limit_enabled:
        return NullRateLimitBackend()
    if settings.rate_limit_backend == "redis":
        # Not implemented yet, and failing loudly is the right behaviour: a
        # silent fall back to the in-memory backend would mean a deployment that
        # believes it has a shared limiter and does not.
        raise NotImplementedError(
            "The Redis rate-limit backend is not implemented yet. Implement "
            "RedisRateLimitBackend against RateLimitBackend before setting "
            "PHOTOFLOW_RATE_LIMIT_BACKEND=redis."
        )
    return InMemoryRateLimitBackend()
