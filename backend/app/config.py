"""
Environment-based configuration for the PhotoFlow backend.

Everything the backend needs to know that differs between a developer's laptop
and the production host is read from the environment (or a local ``.env`` file)
and validated here, once, at import time. Nothing in this package reads
``os.environ`` directly -- if a setting matters, it belongs in :class:`Settings`
so that a missing or nonsensical value fails immediately and loudly at startup
rather than at 2am inside a request handler.

Two rules this module enforces that are worth stating plainly:

1. **A production process may not start on development defaults.** The
   placeholder JWT secret, a wildcard CORS origin, and ``DEBUG`` are all
   rejected when ``ENVIRONMENT=production``. A backend that silently accepts a
   known signing key is worse than one that refuses to boot.
2. **The database URL is a backend-only secret.** It is never returned by any
   endpoint, never logged, and never shipped to the desktop client. The
   :meth:`Settings.safe_database_target` helper exists so diagnostics can say
   *which* database is in use without revealing credentials.

See ``backend/README.md`` for the full list of variables and
``backend/.env.example`` for a template.
"""

from __future__ import annotations

import enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# The repository's backend directory, so a developer's .env is found regardless
# of the working directory uvicorn happens to be launched from.
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Environment(str, enum.Enum):
    """Which deployment this process is. Drives every safety check below."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


# The value shipped in .env.example. Refused in production; see _check_production.
# Padded past 32 bytes so PyJWT does not emit an InsecureKeyLengthWarning on
# every development request -- a warning developers learn to ignore is a warning
# that will not be noticed when it matters.
PLACEHOLDER_JWT_SECRET = "dev-only-insecure-change-me-0000000000"


class ConfigurationError(RuntimeError):
    """Raised when configuration is missing or unsafe for the target environment."""


class Settings(BaseSettings):
    """Validated runtime configuration. Construct via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="PHOTOFLOW_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Identity ----------------------------------------------------------- #
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    app_name: str = "PhotoFlow API"

    # --- Database ----------------------------------------------------------- #
    # Backend-only. The desktop client must never receive this value.
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/photoflow_dev",
    )
    # Small pools by default: a licensing API is low-throughput, and Neon's
    # connection limits are the binding constraint, not our CPU.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_pre_ping: bool = True
    db_echo: bool = False

    # --- HTTP --------------------------------------------------------------- #
    api_base_url: str = "http://localhost:8000"
    api_v1_prefix: str = "/api/v1"
    # Comma-separated in the environment, e.g. "http://localhost:8787".
    # NoDecode stops pydantic-settings trying to JSON-parse it first, so the
    # variable stays a plain comma-separated string rather than a JSON array
    # that has to be quoted correctly inside a shell or a hosting dashboard.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Tokens ------------------------------------------------------------- #
    # HS256 is correct for *session* tokens the backend both mints and verifies.
    # Entitlement tokens the desktop app verifies offline will use Ed25519 with
    # a public key compiled into the client -- that is Phase 3+, not this.
    jwt_secret: str = PLACEHOLDER_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "photoflow-api"
    # The audience a token is minted for. Today there is one; when the admin
    # dashboard gets its own token class, an access token minted for the desktop
    # app must not be accepted by an admin endpoint, and `aud` is how that is
    # enforced without a second signing key.
    jwt_audience: str = "photoflow-desktop"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 30

    # --- Ed25519 signing ------------------------------------------------------ #
    # Entitlement tokens and release manifests are signed with Ed25519, NOT with
    # jwt_secret. The desktop app must be able to *verify* offline without being
    # able to *mint* -- an HS256 secret compiled into a Windows binary is a
    # shared secret with every customer who owns a hex editor.
    #
    # Both are base64 (standard, with padding) of the raw 32-byte key.
    # The private key belongs in the hosting provider's secret store and must
    # never appear in the repository, in .env.example, or in any API response.
    signing_private_key: str = ""
    signing_public_key: str = ""
    # Optional: read the private key from a file instead of an env var, for
    # hosts that mount secrets as files rather than injecting them.
    signing_private_key_file: str = ""

    # --- Rate limiting -------------------------------------------------------- #
    rate_limit_enabled: bool = True
    # "memory" today; "redis" once there is more than one backend instance.
    rate_limit_backend: str = "memory"
    rate_limit_redis_url: str = ""
    # Login: attempts per identity (and per IP) inside the window.
    rate_limit_login_attempts: int = 5
    rate_limit_login_window_seconds: int = 300
    # Refresh is called routinely by every running client, so it gets a much
    # higher ceiling -- it is a runaway-client guard, not an anti-guessing one.
    rate_limit_refresh_attempts: int = 30
    rate_limit_refresh_window_seconds: int = 300
    # Explicit acknowledgement that this deployment runs exactly one instance and
    # a per-process limiter is therefore accurate. Required to run production on
    # the memory backend, so scaling out cannot silently weaken the limit.
    allow_single_instance_rate_limit: bool = False

    # --- Request hardening ---------------------------------------------------- #
    # Hosts this API will answer to. Empty means "any", which is correct on a
    # laptop and refused in production (see _check_production).
    trusted_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # 1 MiB. No Phase 3 endpoint accepts anything close to this; the limit exists
    # so an unauthenticated caller cannot stream gigabytes into the process.
    max_request_body_bytes: int = 1_048_576

    # --- Feature flags ------------------------------------------------------- #
    # Credits ship as schema only until pricing is validated. The tables exist so
    # migrations stay linear; the behaviour stays off.
    credits_enabled: bool = False

    # --- Logging ------------------------------------------------------------- #
    log_level: str = "INFO"
    log_json: bool = False

    # ----------------------------------------------------------------------- #
    # Validators
    # ----------------------------------------------------------------------- #
    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept ``a,b`` from the environment as well as a real list."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("rate_limit_backend")
    @classmethod
    def _check_rate_limit_backend(cls, value: str) -> str:
        allowed = {"memory", "redis"}
        lowered = value.strip().lower()
        if lowered not in allowed:
            raise ValueError(f"PHOTOFLOW_RATE_LIMIT_BACKEND must be one of {sorted(allowed)}")
        return lowered

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("PHOTOFLOW_DATABASE_URL must not be empty")
        if not value.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError(
                "PHOTOFLOW_DATABASE_URL must be a PostgreSQL URL "
                "(postgresql:// or postgresql+psycopg://); "
                "PhotoFlow targets PostgreSQL/Neon and nothing else"
            )
        # Normalise to the psycopg 3 driver so a copy-pasted Neon URL works.
        if value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"PHOTOFLOW_LOG_LEVEL must be one of {sorted(allowed)}")
        return upper

    @model_validator(mode="after")
    def _check_production(self) -> Settings:
        """Refuse to run production on development defaults."""
        if self.environment is not Environment.PRODUCTION:
            return self

        problems: list[str] = []
        if self.jwt_secret == PLACEHOLDER_JWT_SECRET:
            problems.append(
                "PHOTOFLOW_JWT_SECRET is still the development placeholder"
            )
        if len(self.jwt_secret) < 32:
            problems.append("PHOTOFLOW_JWT_SECRET must be at least 32 characters")
        if self.debug:
            problems.append("PHOTOFLOW_DEBUG must be false in production")
        if "*" in self.cors_origins:
            problems.append(
                "PHOTOFLOW_CORS_ORIGINS must list explicit origins, never '*'"
            )
        if not self.api_base_url.startswith("https://"):
            problems.append("PHOTOFLOW_API_BASE_URL must use https:// in production")
        if not self.trusted_hosts:
            problems.append(
                "PHOTOFLOW_TRUSTED_HOSTS must list the hostnames this API answers to"
            )
        if "*" in self.trusted_hosts:
            problems.append("PHOTOFLOW_TRUSTED_HOSTS must not contain '*' in production")
        if not self.rate_limit_enabled:
            problems.append(
                "PHOTOFLOW_RATE_LIMIT_ENABLED must be true in production -- "
                "unlimited password guessing against real accounts is not a "
                "configuration option"
            )
        if self.rate_limit_backend == "memory" and not self.allow_single_instance_rate_limit:
            # A single instance is a legitimate way to start, so this is opt-out
            # rather than forbidden. But an in-memory counter multiplies the real
            # limit by the instance count the moment you scale out, silently and
            # with no error -- so running it in production has to be a decision
            # someone made, not a default nobody revisited.
            problems.append(
                "PHOTOFLOW_RATE_LIMIT_BACKEND=memory is per-process: with more "
                "than one instance the effective limit multiplies. Set 'redis' "
                "and PHOTOFLOW_RATE_LIMIT_REDIS_URL, or set "
                "PHOTOFLOW_ALLOW_SINGLE_INSTANCE_RATE_LIMIT=true to accept it"
            )
        if self.rate_limit_backend == "redis" and not self.rate_limit_redis_url:
            problems.append(
                "PHOTOFLOW_RATE_LIMIT_REDIS_URL is required when the backend is 'redis'"
            )
        if problems:
            raise ValueError(
                "Unsafe production configuration: " + "; ".join(problems)
            )
        return self

    # ----------------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------------- #
    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def is_test(self) -> bool:
        return self.environment is Environment.TEST

    def resolve_signing_private_key(self) -> str:
        """
        The base64 Ed25519 private key, or ``""`` when none is configured.

        Two sources, in priority order: ``PHOTOFLOW_SIGNING_PRIVATE_KEY_FILE``
        (for hosts that mount secrets as files) and then
        ``PHOTOFLOW_SIGNING_PRIVATE_KEY``. The file wins because a host that
        mounts a secret file is expressing a deliberate choice, and because a
        stale environment variable left over from an earlier deploy is the more
        likely accident of the two.

        Returns a value, never logs one. Nothing else in the codebase reads
        these fields directly.
        """
        if self.signing_private_key_file:
            path = Path(self.signing_private_key_file)
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        return self.signing_private_key.strip()

    @property
    def signing_configured(self) -> bool:
        """Whether entitlement signing is available in this process."""
        return bool(self.resolve_signing_private_key())

    def safe_database_target(self) -> str:
        """
        ``host/database`` with credentials stripped -- safe for startup logs and
        for the migration confirmation prompt. Never returned over HTTP.
        """
        from urllib.parse import urlsplit

        parts = urlsplit(self.database_url)
        host = parts.hostname or "?"
        database = (parts.path or "/?").lstrip("/") or "?"
        return f"{host}/{database}"


class _CachedSettings:
    """Tiny holder so tests can reset the cache without touching lru_cache guts."""

    value: Settings | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    The process-wide settings object.

    Cached so that validation happens exactly once. Tests that need different
    configuration should call :func:`reset_settings_cache` after changing the
    environment.
    """
    try:
        return Settings()
    except Exception as exc:  # pydantic ValidationError, or our ValueErrors
        raise ConfigurationError(str(exc)) from exc


def reset_settings_cache() -> None:
    """Clear the cached settings (tests only)."""
    get_settings.cache_clear()
