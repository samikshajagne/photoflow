# PhotoFlow Backend

The server side of PhotoFlow: identity, licences, device activations, credits,
release metadata and administration. Built in Phase 2 as a foundation — it
starts, connects to PostgreSQL, and has the schema and security boundaries the
later phases build on. It does **not** yet have login, licence or credit
endpoints; those are Phase 3 and beyond.

---

## Where this sits

```
   PhotoFlow Desktop (Windows)          Local Admin Dashboard
   photos stay on this machine          localhost:8787, your PC only
              │                                    │
              │  HTTPS                             │  HTTPS + admin token
              └──────────────┬─────────────────────┘
                             ▼
                  FastAPI Backend  (this)
                  holds every secret
                             │
                             ▼
                  PostgreSQL / Neon
```

**The desktop application never connects to PostgreSQL.** It talks HTTPS to this
API and nothing else. It has no `DATABASE_URL`, no `POSTGRES_PASSWORD`, no
`ADMIN_SECRET`, no `PRIVATE_SIGNING_KEY`. That boundary is the whole point of
this service existing: a connection string shipped inside a Windows installer is
a connection string in the hands of every customer.

Client photographs never reach this backend. It manages identity, licences,
devices, credits, versions and administration — not photos.

---

## Layout

```
backend/
├── alembic.ini                  no URL in it, on purpose
├── requirements.txt             runtime deps (separate from the desktop app's)
├── requirements-dev.txt         + pytest, ruff, mypy, httpx
├── .env.example                 copy to .env; .env is git-ignored
├── app/
│   ├── main.py                  create_app(), middleware, router mounting
│   ├── config.py                pydantic-settings; refuses unsafe production
│   ├── version.py               API + backend version (not the desktop version)
│   ├── errors.py                handlers that never leak a stack trace
│   ├── logging_config.py        structured logs, per-request id
│   ├── api/
│   │   ├── health.py            /health, /health/ready       (infrastructure)
│   │   └── v1/
│   │       ├── router.py        everything under /api/v1
│   │       └── health.py        /api/v1/health               (API contract)
│   ├── auth/
│   │   ├── dependencies.py      get_current_user, require_admin
│   │   └── service.py           password auth, session issuance
│   ├── database/
│   │   ├── base.py              DeclarativeBase, naming convention, mixins
│   │   └── session.py           engine, get_db, check_database
│   ├── models/                  users, licenses, devices, credits, releases,
│   │                            refresh_tokens, audit_logs
│   ├── schemas/                 pydantic response models
│   ├── security/
│   │   ├── passwords.py         Argon2id
│   │   └── tokens.py            JWT access, opaque refresh
│   └── services/
│       └── audit.py             audit writes, with metadata scrubbing
├── migrations/                  Alembic; 0001_initial_schema.py
└── tests/                       111 tests
```

---

## Getting it running

Everything below is from the **repository root**, on Windows PowerShell.

### 1. Virtual environment

Use a separate one from the desktop app's `.venv`. The two dependency sets have
no reason to share a resolver, and keeping them apart means a backend upgrade
can never break a build.

```powershell
python -m venv .venv-backend
.\.venv-backend\Scripts\Activate.ps1
```

### 2. Dependencies

```powershell
pip install -r backend\requirements-dev.txt
```

### 3. Configuration

```powershell
copy backend\.env.example backend\.env
```

Then edit `backend\.env`. It is git-ignored (the root `.gitignore` has a
`backend/.env` rule alongside the existing `.env` one). Never commit it.

### 4. A development database

Either a local PostgreSQL:

```powershell
createdb photoflow_dev
createdb photoflow_test
```

…or a Neon **development branch** (see *Neon* below). Put the URL in
`PHOTOFLOW_DATABASE_URL` and a throwaway one in `PHOTOFLOW_TEST_DATABASE_URL`.

### 5. Migrations

```powershell
cd backend
alembic upgrade head
```

Every run prints its target first, e.g.
`[alembic] environment=development target=localhost/photoflow_dev`. Read that
line before pressing enter.

### 6. Start the server

```powershell
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

Then:

- <http://localhost:8000/health> → `{"status":"ok", ...}`
- <http://localhost:8000/health/ready> → `{"status":"ok","database":"ok"}`
- <http://localhost:8000/api/v1/health>
- <http://localhost:8000/docs> (development only; disabled in production)

### 7. Tests

```powershell
cd backend
pytest
```

The database-backed tests **skip** unless `PHOTOFLOW_TEST_DATABASE_URL` is set.
That is deliberate: they drop and rebuild the schema, and a fallback to
`PHOTOFLOW_DATABASE_URL` is how a test run destroys a database someone was
using — or, with a stale shell variable, a Neon production branch.

---

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `PHOTOFLOW_ENVIRONMENT` | no | `development` | `development` / `test` / `production` |
| `PHOTOFLOW_DEBUG` | no | `false` | must be false in production |
| `PHOTOFLOW_DATABASE_URL` | **yes** | local dev URL | **backend only**, never in the desktop app |
| `PHOTOFLOW_TEST_DATABASE_URL` | tests only | — | throwaway database; unset ⇒ DB tests skip |
| `PHOTOFLOW_API_BASE_URL` | no | `http://localhost:8000` | must be `https://` in production |
| `PHOTOFLOW_CORS_ORIGINS` | no | empty | comma-separated; `*` refused in production |
| `PHOTOFLOW_JWT_SECRET` | **yes in prod** | placeholder | ≥32 chars; placeholder refused in production |
| `PHOTOFLOW_ACCESS_TOKEN_TTL_MINUTES` | no | `30` | |
| `PHOTOFLOW_REFRESH_TOKEN_TTL_DAYS` | no | `30` | |
| `PHOTOFLOW_CREDITS_ENABLED` | no | `false` | schema exists, feature off |
| `PHOTOFLOW_LOG_LEVEL` | no | `INFO` | |
| `PHOTOFLOW_LOG_JSON` | no | `false` | set true in production |
| `PHOTOFLOW_MIGRATION_CONFIRM` | prod migrations | — | must equal `production` to migrate prod |
| `PHOTOFLOW_DB_POOL_SIZE` | no | `5` | Neon's connection cap is the constraint |

Generate a real JWT secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Development vs production

Selection is entirely by environment variable — there is no config file that
changes between deployments, because a file is something you can forget to swap.

| | Development | Production |
|---|---|---|
| `PHOTOFLOW_ENVIRONMENT` | `development` | `production` |
| Database | local PostgreSQL or Neon **dev branch** | Neon **prod branch** |
| `.env` file | `backend/.env` on your laptop | **none** — variables come from the host's secret store |
| JWT secret | placeholder tolerated | placeholder refused; ≥32 chars required |
| `/docs`, `/openapi.json` | enabled | disabled |
| CORS | `http://localhost:8787` | explicit HTTPS origins; `*` refused |
| Logs | human-readable | JSON, `PHOTOFLOW_LOG_JSON=true` |
| HSTS header | off | on |

Setting `PHOTOFLOW_ENVIRONMENT=production` with any development default still in
place makes the process **refuse to start**. A backend that boots happily on the
placeholder signing key is a backend where anyone who has read this repository
can mint an admin token.

### Neon

Neon's branching is what keeps the two environments genuinely separate: create a
`dev` branch off `main` and point your laptop at that. Two rules:

1. **The production URL never lands on your laptop.** It belongs in the hosting
   provider's secret store. Nothing in this repository should be able to reach
   production from a development machine.
2. **`alembic` refuses to touch production without being told twice.** With
   `PHOTOFLOW_ENVIRONMENT=production`, `migrations/env.py` aborts unless
   `PHOTOFLOW_MIGRATION_CONFIRM=production` is also set:

   ```bash
   PHOTOFLOW_MIGRATION_CONFIRM=production alembic upgrade head
   ```

   The failure message names the target host and database (never the password),
   so a wrong target is visible before anything runs.

Keep `?sslmode=require` on Neon URLs. If it is missing, `app/database/session.py`
adds `sslmode=require` for any non-local host rather than connecting in the
clear.

---

## Migrations

```powershell
alembic upgrade head            # apply
alembic downgrade -1            # roll back one
alembic revision --autogenerate -m "add something"
alembic check                   # do the models and migrations agree?
alembic history                 # what exists
```

Both directions are verified: `upgrade head` → `downgrade -1` → `upgrade head`
from an empty database, and `tests/test_migrations.py` asserts autogenerate finds
nothing outstanding, so a model change that never got a migration fails the test
run rather than production.

`downgrade()` drops the PostgreSQL ENUM types explicitly. Alembic's autogenerate
does not, and without it a downgrade followed by an upgrade fails with
*"type user_role already exists"*.

**Never change the schema by hand.** A column added in psql exists on exactly
one database and is invisible to every other environment.

---

## The schema

```
User ─┬─< License ─< LicenseActivation >─ Device >─ User
      ├─< CreditTransaction     (append-only ledger)
      ├─< CreditReservation     (reserve → local work → commit)
      └─< RefreshToken
Release    (standalone)
AuditLog   (references anything by loose type+id — deliberately no FKs)
```

Nine tables: `users`, `licenses`, `license_activations`, `devices`,
`credit_transactions`, `credit_reservations`, `releases`, `refresh_tokens`,
`audit_logs`.

Decisions worth knowing before you change anything:

- **UUID primary keys**, generated in Python. Non-sequential, because licence and
  user ids appear in URLs and support emails, and a sequential id both leaks how
  many customers exist and lets someone walk the range.
- **Licence keys are stored hashed** (`key_hash` + `key_last4` for display). A
  leaked backup does not hand over a set of working keys.
- **Licence expiry is derived, not read from `status`.** `License.is_valid_at()`
  checks the date window as well as the administrative status, so a batch job
  that never ran cannot accidentally entitle an expired licence.
- **One ACTIVE seat per (licence, device)**, enforced by a *partial* unique
  index. Partial, so the full deactivate/reactivate history is kept.
- **The credit ledger is append-only.** Balance is `SUM(amount)`; a correction is
  a new `ADJUSTMENT` row, never an edit. That is what makes a billing dispute
  answerable.
- **Idempotency is a database constraint**, not application logic: a partial
  unique index on `(user_id, reference_id)` means a retried payment webhook
  cannot credit twice even if the retry logic above it has a bug.
- **`audit_logs` has no foreign keys, on purpose.** Deleting a user must not
  delete the record that you deleted them.
- **Device fingerprints are not MAC addresses.** They are the opaque digest from
  `core.licensing.machine_fingerprint()` — stable across reboots, revealing
  nothing about the customer, and scoped unique per user so a shared studio
  machine can serve two accounts.

---

## Routes

| Route | Purpose | Why it is where it is |
|---|---|---|
| `GET /health` | liveness | Belongs to the **host**. Does not touch the database — a database blip must not make the platform kill a healthy process. Its shape must never change. |
| `GET /health/ready` | readiness | 503 when the database is unreachable, so a load balancer stops routing here. |
| `GET /api/v1/health` | API health | Belongs to the **API contract**. The desktop client calls this; it may gain fields under normal versioning. |

Application endpoints live under `/api/v1`. The two health families are separate
because merging them would mean either freezing a useful endpoint forever or
breaking the platform's probe with a routine API change.

Nothing any of them return includes a hostname, database name, driver, region,
credential or stack trace.

---

## Security

Implemented in Phase 2:

- **Argon2id** password hashing (`argon2-cffi` directly — passlib is effectively
  unmaintained and its bcrypt backend is broken against current releases). No
  code path writes a plaintext password anywhere; a test asserts it.
- **Access tokens**: short-lived HS256 JWTs with `iss`, `jti`, pinned algorithm
  (so `alg: none` forgery fails) and a type claim (so a refresh token cannot be
  presented as a bearer credential).
- **Refresh tokens**: opaque random bytes, stored as SHA-256 digests, revocable.
- **The user is re-read from the database on every request.** A token minted
  before an account was disabled stops working immediately, not in 30 minutes.
- **The role comes from the database, not the token.** A forged `role: ADMIN`
  claim buys nothing.
- **Uniform 401s.** No-such-user, wrong-token and disabled-account are
  indistinguishable; anything else is an account-enumeration oracle.
- **Errors never leak.** Unhandled exceptions return a generic message plus a
  request id; the trace goes to the log.
- **Audit metadata is scrubbed** before writing — substring matching, so
  `customer_api_key` is caught as well as `api_key`.
- **CORS is an explicit allow-list**; `*` is refused in production.
- **Security headers**: `nosniff`, `DENY`, `no-referrer`, plus HSTS in production.

### Where each secret lives

| Secret | Lives | Never |
|---|---|---|
| `PHOTOFLOW_DATABASE_URL` | backend host env / `backend/.env` locally | desktop app, admin dashboard, git |
| `PHOTOFLOW_JWT_SECRET` | backend host env | anywhere else |
| Ed25519 **private** key (Phase 3+) | backend host env / secret manager | installer, repo, unencrypted laptop |
| Ed25519 **public** key | compiled into the desktop app — correct and safe | — |
| Admin API token (Phase 3+) | your machine, admin dashboard env | committed, emailed, desktop app |
| `OPENAI_API_KEY` | the **client's own machine** — BYO key model | this backend, ever |

The customer's OpenAI key stays local by design. Storing other people's API keys
makes you responsible for their bills and their breaches.

### The threat model, plainly

The desktop app is an untrusted client on hardware the customer owns. Everything
it says about itself can be forged. So: the app *asserts* a device fingerprint,
the backend *decides*; the app *displays* a credit balance, the backend *owns*
it; the app *verifies* an entitlement token, it cannot *mint* one.

What this does not defend against — and pretending otherwise would be dishonest —
is someone patching the binary to skip the checks. That is unsolvable for desktop
software, as `core/licensing.py` already says. The defence is that credits gate
*server-issued* value: if the expensive operation needs a server response,
patching the client gains nothing.

---

## What Phase 2 deliberately does not do

- No login, signup, or password-reset endpoints (Phase 3).
- No licence issue/activate/validate endpoints (Phase 4).
- No credit endpoints — the schema exists, `PHOTOFLOW_CREDITS_ENABLED=false`.
- No admin dashboard (Phase 6).
- No deployment. Neon production, hosting, TLS, domain and production secrets are
  configured separately, after the local backend is proven.
- No desktop integration. `core/licensing.py`'s `HttpBackend` already speaks a
  compatible shape; wiring it up is Phase 4.

## The future desktop boundary

```
PhotoFlow Desktop
        ├── Authentication API   /api/v1/auth/*
        ├── License API          /api/v1/licenses/*
        ├── Device API           /api/v1/devices/*
        ├── Credits API          /api/v1/credits/*
        └── Update API           /api/v1/updates/*
```

Installer binaries are **not** served from here — they live on GitHub Releases,
whose bandwidth is free and whose `releases/latest/download/...` URL is
permanent. This backend serves the metadata and the signature, which is the part
that has to be trustworthy.
