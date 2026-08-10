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
PLACEHOLDER_JWT_SECRET = "dev-only-insecure-change-me"


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
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 30

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
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept ``a,b`` from the environment as well as a real list."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

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
