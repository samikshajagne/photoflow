"""
Operator commands.

    python -m app.cli create-admin
    python -m app.cli generate-signing-key
    python -m app.cli show-config

Run from ``backend/`` with the virtual environment active. Every command acts on
the database and configuration selected by the current environment — the same
``PHOTOFLOW_*`` variables the server reads, so there is no second place for a
target to be configured and get out of step.

Why these are CLI commands and not HTTP endpoints
-------------------------------------------------
An endpoint that creates administrators is an endpoint that creates
administrators *for whoever finds it*. Any protection bolted onto it — a setup
token, an "only if no admin exists yet" check — is one bug away from being an
unauthenticated privilege-escalation route, and that class of bug is common
enough to have its own CVE genre. Requiring shell access to the backend host is
a much stronger boundary than anything reachable over HTTPS, and it costs one
command, once, ever.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Make `app` importable when run as `python -m app.cli` from backend/.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _stderr(message: str = "") -> None:
    print(message, file=sys.stderr)


# --------------------------------------------------------------------------- #
# create-admin
# --------------------------------------------------------------------------- #
def create_admin(args: argparse.Namespace) -> int:
    """
    Create the first administrator, interactively.

    The password is read with :func:`getpass.getpass`, which means it is not
    echoed to the terminal and — importantly — never becomes a shell history
    entry, never appears in ``ps`` output, and never lands in a CI log. There is
    deliberately **no** ``--password`` flag: offering one guarantees somebody
    eventually uses it in a script that gets committed.
    """
    from sqlalchemy import func, select

    from app.config import get_settings
    from app.database.session import get_sessionmaker
    from app.models.enums import UserRole, UserStatus
    from app.models.user import User
    from app.security.passwords import PasswordPolicyError, hash_password, validate_password
    from app.services import audit
    from app.services.audit import AuditAction

    settings = get_settings()
    _stderr(f"Creating an administrator in: {settings.safe_database_target()}")
    _stderr(f"Environment: {settings.environment.value}")
    _stderr()

    email = (args.email or input("Email: ")).strip().lower()
    if not email or "@" not in email:
        _stderr("That does not look like an email address.")
        return 2

    name = (args.name if args.name is not None else input("Name: ")).strip()

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        _stderr("Passwords do not match.")
        return 2

    try:
        validate_password(password)
    except PasswordPolicyError as exc:
        _stderr(str(exc))
        return 2

    session_factory = get_sessionmaker(settings)
    with session_factory() as db:
        existing = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if existing is not None:
            # Refuse rather than silently promote: turning an existing customer
            # account into an administrator because someone mistyped an address
            # is not a recoverable accident.
            _stderr(f"An account already exists for {email}. Refusing to modify it.")
            return 3

        admin_count = db.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.ADMIN)
        ).scalar_one()

        user = User(
            email=email,
            name=name or None,
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            email_verified=True,
        )
        db.add(user)
        db.flush()

        audit.record(
            db,
            action=AuditAction.ADMIN_CREATED,
            actor_user_id=None,  # null actor = the system / an operator at a shell
            target_type="user",
            target_id=str(user.id),
            metadata={"email": email, "via": "cli", "existing_admins": int(admin_count)},
        )
        db.commit()

        # Note what is printed: an id and an address, never the password.
        _stderr()
        _stderr(f"Administrator created: {email}")
        _stderr(f"  id:   {user.id}")
        _stderr("  role: ADMIN")
        if admin_count:
            _stderr(f"  note: {admin_count} administrator(s) already existed.")
    return 0


# --------------------------------------------------------------------------- #
# generate-signing-key
# --------------------------------------------------------------------------- #
def generate_signing_key(args: argparse.Namespace) -> int:
    """
    Generate an Ed25519 keypair for entitlement and release signing.

    Prints to stdout by default rather than writing a file, so the private key
    can be pasted straight into a secret manager without ever touching the disk
    of the machine that generated it. ``--out-dir`` writes files instead, for
    local development, with 0600 permissions and a loud reminder.
    """
    from app.security.signing import generate_keypair

    keypair = generate_keypair()

    if args.out_dir:
        directory = Path(args.out_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)

        private_path = directory / "photoflow_signing_key"
        public_path = directory / "photoflow_signing_key.pub"

        if (private_path.exists() or public_path.exists()) and not args.force:
            _stderr(
                f"{private_path} already exists. Refusing to overwrite a signing "
                "key -- every entitlement signed with the old one would stop "
                "verifying. Pass --force if that is genuinely what you want."
            )
            return 3

        private_path.write_text(keypair.private_key_b64 + "\n", encoding="utf-8")
        public_path.write_text(keypair.public_key_b64 + "\n", encoding="utf-8")
        try:
            private_path.chmod(0o600)
        except OSError:
            # Windows does not implement POSIX modes; the warning below covers it.
            pass

        _stderr("Ed25519 keypair written:")
        _stderr(f"  private: {private_path}   <-- SECRET")
        _stderr(f"  public:  {public_path}")
        _stderr()
        _stderr("Point the backend at it with:")
        _stderr(f"  PHOTOFLOW_SIGNING_PRIVATE_KEY_FILE={private_path}")
        _stderr(f"  PHOTOFLOW_SIGNING_PUBLIC_KEY={keypair.public_key_b64}")
        _stderr()
        _stderr("The private key file must NEVER be committed, packaged into the")
        _stderr("PhotoFlow installer, or copied anywhere the desktop app can read.")
        _stderr("Keep it outside the repository directory.")
        return 0

    # Default: stdout only, nothing written to disk.
    print("PHOTOFLOW_SIGNING_PRIVATE_KEY=" + keypair.private_key_b64)
    print("PHOTOFLOW_SIGNING_PUBLIC_KEY=" + keypair.public_key_b64)
    _stderr()
    _stderr("The first line is SECRET. Put it in your hosting provider's secret")
    _stderr("store. Do not paste it into .env.example, a commit, or a chat window.")
    _stderr("The second line is the public key -- safe to publish, and eventually")
    _stderr("compiled into the PhotoFlow desktop application.")
    _stderr()
    _stderr("Generating a NEW key invalidates every entitlement signed with the")
    _stderr("old one. Rotating is a planned operation, not a routine one.")
    return 0


# --------------------------------------------------------------------------- #
# show-config
# --------------------------------------------------------------------------- #
def show_config(args: argparse.Namespace) -> int:
    """
    Print the effective configuration, with every secret redacted.

    For answering "which database is this pointed at" without opening a Python
    shell — the question that precedes most migration accidents.
    """
    from app.config import get_settings

    settings = get_settings()
    signing = "configured" if settings.signing_configured else "not configured"

    print(f"environment:      {settings.environment.value}")
    print(f"debug:            {settings.debug}")
    print(f"database:         {settings.safe_database_target()}")
    print(f"api_base_url:     {settings.api_base_url}")
    print(f"cors_origins:     {settings.cors_origins or '(none)'}")
    print(f"trusted_hosts:    {settings.trusted_hosts or '(any)'}")
    print(f"jwt_issuer:       {settings.jwt_issuer}")
    print(f"jwt_audience:     {settings.jwt_audience}")
    print(f"jwt_secret:       [redacted, {len(settings.jwt_secret)} chars]")
    print(f"access_token_ttl: {settings.access_token_ttl_minutes} minutes")
    print(f"refresh_ttl:      {settings.refresh_token_ttl_days} days")
    print(f"rate_limit:       enabled={settings.rate_limit_enabled} "
          f"backend={settings.rate_limit_backend}")
    print(f"signing_key:      {signing}")
    print(f"signing_public:   {settings.signing_public_key or '(none)'}")
    print(f"credits_enabled:  {settings.credits_enabled}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="PhotoFlow backend operator commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    admin = subparsers.add_parser(
        "create-admin",
        help="Create an administrator account (prompts for the password).",
    )
    admin.add_argument("--email", default=None, help="Skip the email prompt.")
    admin.add_argument("--name", default=None, help="Skip the name prompt.")
    admin.set_defaults(func=create_admin)

    keygen = subparsers.add_parser(
        "generate-signing-key",
        help="Generate an Ed25519 entitlement-signing keypair.",
    )
    keygen.add_argument(
        "--out-dir",
        default=None,
        help="Write key files here instead of printing them (local development).",
    )
    keygen.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing key files. Invalidates every issued entitlement.",
    )
    keygen.set_defaults(func=generate_signing_key)

    config = subparsers.add_parser(
        "show-config", help="Print the effective configuration, secrets redacted."
    )
    config.set_defaults(func=show_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
