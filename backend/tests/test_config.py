"""
Configuration tests -- specifically, that bad configuration fails *safely*.

The interesting cases are not "does a valid .env load". They are: does a
production process refuse to boot on a development secret, and does it refuse
loudly enough that nobody deploys it by accident. A backend that silently
accepts the placeholder JWT secret would let anyone who has read the repository
mint an admin token.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import (
    PLACEHOLDER_JWT_SECRET,
    ConfigurationError,
    Environment,
    Settings,
)

# A secret of realistic length, used where the *other* setting is under test.
GOOD_SECRET = "x" * 48


def _settings(**overrides) -> Settings:
    """Build settings from explicit kwargs, ignoring any .env on disk."""
    base = {
        "environment": Environment.PRODUCTION,
        "database_url": "postgresql://u:p@db.neon.tech/photoflow",
        "api_base_url": "https://api.photoflow.example",
        "jwt_secret": GOOD_SECRET,
        "cors_origins": ["https://admin.photoflow.example"],
        # Phase 3 added two more production requirements; supplying them here
        # keeps each test focused on the one setting it is actually about.
        "trusted_hosts": ["api.photoflow.example"],
        "allow_single_instance_rate_limit": True,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


class TestDatabaseUrl:
    def test_rejects_non_postgres_url(self):
        with pytest.raises(ValidationError) as exc:
            _settings(database_url="sqlite:///photoflow.db")
        assert "PostgreSQL" in str(exc.value)

    def test_rejects_empty_url(self):
        with pytest.raises(ValidationError):
            _settings(database_url="   ")

    def test_normalises_bare_postgres_scheme_to_psycopg(self):
        """A Neon dashboard gives you postgresql://; it must just work."""
        settings = _settings(database_url="postgresql://u:p@db.neon.tech/photoflow")
        assert settings.database_url.startswith("postgresql+psycopg://")


class TestProductionSafety:
    def test_placeholder_secret_is_refused_in_production(self):
        with pytest.raises(ValidationError) as exc:
            _settings(jwt_secret=PLACEHOLDER_JWT_SECRET)
        assert "placeholder" in str(exc.value).lower()

    def test_short_secret_is_refused_in_production(self):
        with pytest.raises(ValidationError) as exc:
            _settings(jwt_secret="tooshort")
        assert "32 characters" in str(exc.value)

    def test_debug_is_refused_in_production(self):
        with pytest.raises(ValidationError) as exc:
            _settings(debug=True)
        assert "DEBUG" in str(exc.value)

    def test_wildcard_cors_is_refused_in_production(self):
        with pytest.raises(ValidationError) as exc:
            _settings(cors_origins=["*"])
        assert "CORS" in str(exc.value)

    def test_plain_http_api_base_url_is_refused_in_production(self):
        with pytest.raises(ValidationError) as exc:
            _settings(api_base_url="http://api.photoflow.example")
        assert "https" in str(exc.value)

    def test_valid_production_configuration_is_accepted(self):
        settings = _settings()
        assert settings.is_production
        assert settings.environment is Environment.PRODUCTION

    def test_development_tolerates_the_placeholder(self):
        """Local development must not need a generated secret to run."""
        settings = _settings(
            environment=Environment.DEVELOPMENT,
            jwt_secret=PLACEHOLDER_JWT_SECRET,
            api_base_url="http://localhost:8000",
            debug=True,
        )
        assert not settings.is_production


class TestSecretLeakage:
    def test_safe_database_target_omits_credentials(self):
        settings = _settings(
            database_url="postgresql://someuser:hunter2@ep-cool-1.neon.tech/photoflow"
        )
        target = settings.safe_database_target()
        assert target == "ep-cool-1.neon.tech/photoflow"
        assert "hunter2" not in target
        assert "someuser" not in target

    def test_credits_are_off_by_default(self):
        """Pricing is not settled; the feature must not default to on."""
        assert _settings().credits_enabled is False


class TestCorsParsing:
    def test_comma_separated_string_becomes_a_list(self):
        settings = _settings(cors_origins="https://a.example, https://b.example")
        assert settings.cors_origins == ["https://a.example", "https://b.example"]


class TestConfigurationError:
    def test_get_settings_raises_configuration_error(self, monkeypatch):
        """A bad environment surfaces as our error type, not a pydantic one."""
        from app import config as config_module

        monkeypatch.setenv("PHOTOFLOW_DATABASE_URL", "mysql://nope/photoflow")
        config_module.reset_settings_cache()
        try:
            with pytest.raises(ConfigurationError):
                config_module.get_settings()
        finally:
            config_module.reset_settings_cache()
