"""
Operator CLI tests.

``create-admin`` is the one privileged operation in the system with no HTTP
surface at all, which makes it both the safest way to bootstrap and the least
exercised code path. It gets tested properly.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.models.enums import UserRole, UserStatus
from app.models.user import User
from tests.conftest import requires_database

PASSWORD = "a-perfectly-fine-password"


@pytest.fixture
def cli_db(db, monkeypatch):
    """
    Point the CLI's session factory at the test transaction.

    The CLI opens its own session via ``get_sessionmaker``; overriding it here
    is the equivalent of the ``get_db`` dependency override the API tests use,
    and keeps everything the command writes inside the rolled-back transaction.
    """
    from contextlib import contextmanager

    from app.database import session as session_module

    @contextmanager
    def _factory():
        yield db

    def _get_sessionmaker(_settings=None):
        return _factory

    monkeypatch.setattr(session_module, "get_sessionmaker", _get_sessionmaker)
    # cli.py imports the symbol inside the function body, so patching the module
    # attribute is enough -- but be explicit about why this works.
    return db


@pytest.fixture
def answers(monkeypatch):
    """Feed scripted responses to getpass and input."""

    def _set(password: str, confirm: str | None = None, inputs: list[str] | None = None):
        passwords = iter([password, confirm if confirm is not None else password])
        monkeypatch.setattr("getpass.getpass", lambda *_args, **_kw: next(passwords))
        queue = iter(inputs or [])
        monkeypatch.setattr("builtins.input", lambda *_args, **_kw: next(queue, ""))

    return _set


@requires_database
class TestCreateAdmin:
    def test_creates_an_administrator(self, cli_db, answers, capsys):
        from app.cli import main

        answers(PASSWORD)
        code = main(["create-admin", "--email", "owner@example.com", "--name", "Owner"])
        assert code == 0

        user = cli_db.execute(
            select(User).where(User.email == "owner@example.com")
        ).scalar_one()
        assert user.role is UserRole.ADMIN
        assert user.status is UserStatus.ACTIVE
        assert user.email_verified is True

    def test_password_is_argon2_hashed(self, cli_db, answers):
        from app.cli import main

        answers(PASSWORD)
        main(["create-admin", "--email", "hashed@example.com", "--name", "X"])

        user = cli_db.execute(
            select(User).where(User.email == "hashed@example.com")
        ).scalar_one()
        assert user.password_hash.startswith("$argon2id$")
        assert PASSWORD not in user.password_hash

    def test_the_password_is_never_printed(self, cli_db, answers, capsys):
        """
        It is not echoed, not in argv, and must not be in any output that could
        reach a terminal recording or a CI log.
        """
        from app.cli import main

        answers(PASSWORD)
        main(["create-admin", "--email", "quiet@example.com", "--name", "Q"])

        captured = capsys.readouterr()
        assert PASSWORD not in captured.out
        assert PASSWORD not in captured.err

    def test_there_is_no_password_flag(self):
        """
        Offering one guarantees somebody eventually uses it in a script that
        gets committed, or leaves it in their shell history.
        """
        from app.cli import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["create-admin", "--password", PASSWORD])

    def test_mismatched_confirmation_is_refused(self, cli_db, answers):
        from app.cli import main

        answers(PASSWORD, confirm="something-else-entirely")
        code = main(["create-admin", "--email", "nope@example.com", "--name", "N"])
        assert code == 2
        assert cli_db.execute(
            select(User).where(User.email == "nope@example.com")
        ).scalar_one_or_none() is None

    def test_weak_password_is_refused(self, cli_db, answers):
        from app.cli import main

        answers("short")
        code = main(["create-admin", "--email", "weak@example.com", "--name", "W"])
        assert code == 2
        assert cli_db.execute(
            select(User).where(User.email == "weak@example.com")
        ).scalar_one_or_none() is None

    def test_invalid_email_is_refused(self, cli_db, answers):
        from app.cli import main

        answers(PASSWORD)
        assert main(["create-admin", "--email", "not-an-email", "--name", "X"]) == 2

    def test_duplicate_email_is_refused(self, cli_db, answers, make_user):
        """
        Refuse rather than silently promote: turning a paying customer's account
        into an administrator because someone mistyped is not a recoverable
        accident.
        """
        from app.cli import main

        existing = make_user(email="taken@example.com", role=UserRole.CLIENT)
        answers(PASSWORD)
        code = main(["create-admin", "--email", "taken@example.com", "--name", "X"])
        assert code == 3

        cli_db.refresh(existing)
        assert existing.role is UserRole.CLIENT  # untouched

    def test_creation_is_audited(self, cli_db, answers):
        from app.cli import main
        from app.models.audit import AuditLog
        from app.services.audit import AuditAction

        answers(PASSWORD)
        main(["create-admin", "--email", "audited@example.com", "--name", "A"])

        entries = cli_db.execute(select(AuditLog)).scalars().all()
        assert any(e.action == AuditAction.ADMIN_CREATED for e in entries)
        for entry in entries:
            assert PASSWORD not in str(entry.metadata_json or {})

    def test_the_created_admin_can_log_in(self, cli_db, answers, client):
        from app.cli import main

        answers(PASSWORD)
        main(["create-admin", "--email", "canlogin@example.com", "--name", "L"])

        response = client.post(
            "/api/v1/auth/login",
            json={"email": "canlogin@example.com", "password": PASSWORD},
        )
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "ADMIN"


class TestGenerateSigningKey:
    def test_prints_a_usable_pair(self, capsys):
        from app.cli import main
        from app.security.signing import SigningService

        assert main(["generate-signing-key"]) == 0
        out = capsys.readouterr().out

        private = _value(out, "PHOTOFLOW_SIGNING_PRIVATE_KEY")
        public = _value(out, "PHOTOFLOW_SIGNING_PUBLIC_KEY")
        assert len(base64.b64decode(private)) == 32
        assert len(base64.b64decode(public)) == 32

        service = SigningService(private, public)
        assert service.verify({"x": 1}, service.sign({"x": 1}))

    def test_each_invocation_differs(self, capsys):
        from app.cli import main

        main(["generate-signing-key"])
        first = capsys.readouterr().out
        main(["generate-signing-key"])
        second = capsys.readouterr().out
        assert first != second

    def test_writes_files_when_asked(self, tmp_path, capsys):
        from app.cli import main

        assert main(["generate-signing-key", "--out-dir", str(tmp_path)]) == 0
        private_path = tmp_path / "photoflow_signing_key"
        public_path = tmp_path / "photoflow_signing_key.pub"
        assert private_path.is_file() and public_path.is_file()
        assert len(base64.b64decode(private_path.read_text().strip())) == 32

    def test_refuses_to_overwrite_an_existing_key(self, tmp_path, capsys):
        """
        Overwriting invalidates every entitlement signed with the old key. That
        has to be deliberate.
        """
        from app.cli import main

        main(["generate-signing-key", "--out-dir", str(tmp_path)])
        original = (tmp_path / "photoflow_signing_key").read_text()

        assert main(["generate-signing-key", "--out-dir", str(tmp_path)]) == 3
        assert (tmp_path / "photoflow_signing_key").read_text() == original

    def test_force_overwrites(self, tmp_path):
        from app.cli import main

        main(["generate-signing-key", "--out-dir", str(tmp_path)])
        original = (tmp_path / "photoflow_signing_key").read_text()
        assert main(["generate-signing-key", "--out-dir", str(tmp_path), "--force"]) == 0
        assert (tmp_path / "photoflow_signing_key").read_text() != original

    def test_warns_about_the_private_key(self, capsys):
        from app.cli import main

        main(["generate-signing-key"])
        warning = capsys.readouterr().err.lower()
        assert "secret" in warning


@requires_database
class TestShowConfig:
    def test_redacts_the_jwt_secret(self, capsys):
        from app.cli import main

        assert main(["show-config"]) == 0
        out = capsys.readouterr().out
        assert "[redacted" in out
        assert "test-secret-that-is-long-enough-to-pass" not in out

    def test_shows_the_database_without_credentials(self, capsys):
        from app.cli import main

        main(["show-config"])
        out = capsys.readouterr().out
        assert "photoflow_test" in out
        assert "postgresql+psycopg://" not in out


def _value(output: str, key: str) -> str:
    for line in output.splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"{key} not found in CLI output")
