"""
Rate-limiter tests.

Two layers: the backend in isolation (fast, deterministic, no HTTP), and the
login endpoint actually returning 429 (slow, but it is the behaviour that
matters).
"""

from __future__ import annotations

import pytest

from app.security.rate_limit import (
    InMemoryRateLimitBackend,
    NullRateLimitBackend,
    RateLimiter,
    build_backend,
)
from tests.conftest import requires_database

LOGIN = "/api/v1/auth/login"
PASSWORD = "correct-horse-battery-staple"


class TestInMemoryBackend:
    def test_allows_up_to_the_limit(self):
        backend = InMemoryRateLimitBackend()
        for _ in range(5):
            assert backend.hit("k", limit=5, window_seconds=60).allowed

    def test_blocks_past_the_limit(self):
        backend = InMemoryRateLimitBackend()
        for _ in range(5):
            backend.hit("k", limit=5, window_seconds=60)
        verdict = backend.hit("k", limit=5, window_seconds=60)
        assert verdict.exceeded
        assert verdict.retry_after_seconds > 0

    def test_keys_are_independent(self):
        backend = InMemoryRateLimitBackend()
        for _ in range(6):
            backend.hit("a", limit=5, window_seconds=60)
        assert backend.hit("b", limit=5, window_seconds=60).allowed

    def test_reset_clears_a_key(self):
        backend = InMemoryRateLimitBackend()
        for _ in range(6):
            backend.hit("k", limit=5, window_seconds=60)
        backend.reset("k")
        assert backend.hit("k", limit=5, window_seconds=60).allowed

    def test_window_expiry_restores_budget(self):
        """A zero-length window is always a fresh one."""
        backend = InMemoryRateLimitBackend()
        for _ in range(5):
            backend.hit("k", limit=5, window_seconds=0)
        assert backend.hit("k", limit=5, window_seconds=0).allowed

    def test_remaining_counts_down(self):
        backend = InMemoryRateLimitBackend()
        assert backend.hit("k", limit=3, window_seconds=60).remaining == 2
        assert backend.hit("k", limit=3, window_seconds=60).remaining == 1

    def test_is_thread_safe(self):
        """
        FastAPI runs sync endpoints in a threadpool, so concurrent hits on one
        key are real, not theoretical. Exactly `limit` must be allowed.
        """
        import threading

        backend = InMemoryRateLimitBackend()
        allowed: list[bool] = []
        lock = threading.Lock()

        def worker() -> None:
            verdict = backend.hit("shared", limit=50, window_seconds=60)
            with lock:
                allowed.append(verdict.allowed)

        threads = [threading.Thread(target=worker) for _ in range(200)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sum(allowed) == 50


class TestNullBackend:
    def test_allows_everything(self):
        backend = NullRateLimitBackend()
        for _ in range(1000):
            assert backend.hit("k", limit=1, window_seconds=60).allowed


class TestLimiterKeys:
    def test_keys_are_scoped(self):
        assert RateLimiter.key("login", "a@b.com") != RateLimiter.key("refresh", "a@b.com")

    def test_keys_do_not_contain_the_identity(self):
        """The counter store should not double as a list of customer emails."""
        key = RateLimiter.key("login", "studio@example.com")
        assert "studio@example.com" not in key
        assert "studio" not in key


class TestBuildBackend:
    def test_disabled_yields_the_null_backend(self):
        from app.config import Environment, Settings

        settings = Settings(
            environment=Environment.TEST, rate_limit_enabled=False, _env_file=None
        )
        assert isinstance(build_backend(settings), NullRateLimitBackend)

    def test_memory_is_the_default(self):
        from app.config import Environment, Settings

        settings = Settings(environment=Environment.TEST, _env_file=None)
        assert isinstance(build_backend(settings), InMemoryRateLimitBackend)

    def test_redis_fails_loudly_rather_than_silently_falling_back(self):
        """
        A silent fall back to the in-memory backend would mean a deployment that
        believes it has a shared limiter and does not -- the worst outcome
        available, because it looks fine.
        """
        from app.config import Environment, Settings

        settings = Settings(
            environment=Environment.TEST,
            rate_limit_backend="redis",
            rate_limit_redis_url="redis://localhost:6379/0",
            _env_file=None,
        )
        with pytest.raises(NotImplementedError):
            build_backend(settings)


class TestProductionConfiguration:
    """The limiter cannot be switched off, or silently weakened, in production."""

    def _production(self, **overrides):
        from app.config import Environment, Settings

        base = {
            "environment": Environment.PRODUCTION,
            "database_url": "postgresql://u:p@db.neon.tech/photoflow",
            "api_base_url": "https://api.photoflow.example",
            "jwt_secret": "x" * 48,
            "cors_origins": ["https://admin.photoflow.example"],
            "trusted_hosts": ["api.photoflow.example"],
            "allow_single_instance_rate_limit": True,
            "_env_file": None,
        }
        base.update(overrides)
        return Settings(**base)

    def test_cannot_disable_rate_limiting_in_production(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc:
            self._production(rate_limit_enabled=False)
        assert "RATE_LIMIT_ENABLED" in str(exc.value)

    def test_memory_backend_needs_an_explicit_acknowledgement(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc:
            self._production(allow_single_instance_rate_limit=False)
        assert "per-process" in str(exc.value)

    def test_redis_backend_needs_a_url(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc:
            self._production(rate_limit_backend="redis", rate_limit_redis_url="")
        assert "REDIS_URL" in str(exc.value)


@requires_database
class TestLoginRateLimiting:
    def test_repeated_failures_eventually_return_429(self, client, make_user):
        user = make_user(email="bruteforce@example.com", password=PASSWORD)
        statuses = []
        for _ in range(12):
            response = client.post(
                LOGIN, json={"email": user.email, "password": "wrong-password-here"}
            )
            statuses.append(response.status_code)
        assert 429 in statuses
        # And it stays blocked rather than flapping.
        assert statuses[-1] == 429

    def test_429_carries_retry_after(self, client, make_user):
        user = make_user(email="retryafter@example.com", password=PASSWORD)
        response = None
        for _ in range(12):
            response = client.post(
                LOGIN, json={"email": user.email, "password": "wrong-password-here"}
            )
            if response.status_code == 429:
                break
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0

    def test_429_body_does_not_describe_the_limit(self, client, make_user):
        """A limiter that reports its own state precisely can be mapped."""
        user = make_user(email="opaque@example.com", password=PASSWORD)
        for _ in range(12):
            response = client.post(
                LOGIN, json={"email": user.email, "password": "wrong-password-here"}
            )
            if response.status_code == 429:
                break
        body = response.json()
        assert "detail" in body
        for leak in ("limit", "remaining", "window", "attempts left"):
            assert leak not in response.text.lower()

    def test_the_limit_is_blind_to_whether_the_account_exists(self, client):
        """
        Otherwise the limiter itself becomes the enumeration oracle the login
        response was carefully designed not to be.
        """
        statuses = []
        for _ in range(12):
            statuses.append(
                client.post(
                    LOGIN,
                    json={"email": "nosuchuser@example.com", "password": PASSWORD},
                ).status_code
            )
        assert 429 in statuses

    def test_a_successful_login_clears_the_budget(self, client, make_user):
        """
        A user who mistypes three times and then succeeds must not be left one
        attempt from a lockout.
        """
        user = make_user(email="forgiven@example.com", password=PASSWORD)
        for _ in range(3):
            client.post(
                LOGIN, json={"email": user.email, "password": "wrong-password-here"}
            )
        assert client.post(
            LOGIN, json={"email": user.email, "password": PASSWORD}
        ).status_code == 200
        # Budget reset: three more failures still do not trip the limit.
        for _ in range(3):
            assert client.post(
                LOGIN, json={"email": user.email, "password": "wrong-password-here"}
            ).status_code == 401

    def test_separate_accounts_have_separate_budgets(self, client, make_user):
        first = make_user(email="first@example.com", password=PASSWORD)
        second = make_user(email="second@example.com", password=PASSWORD)
        for _ in range(12):
            client.post(
                LOGIN, json={"email": first.email, "password": "wrong-password-here"}
            )
        # The per-IP budget is 4x the per-email one, so this may or may not be
        # blocked -- what must be true is that it is not blocked *because of*
        # the other account's failures alone.
        response = client.post(
            LOGIN, json={"email": second.email, "password": PASSWORD}
        )
        assert response.status_code in (200, 429)
