# PhotoFlow Backend

The server side of PhotoFlow: identity, licences, device activations, credits,
release metadata and administration. Phase 2 built the foundation; Phase 3 added
authentication, admin-only account management, rate limiting and the Ed25519
signing infrastructure. It does **not** yet have licence or credit endpoints,
and it has not been deployed.

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
│   ├── cli.py                   create-admin, generate-signing-key, show-config
│   ├── api/
│   │   ├── deps.py              client IP, rate limiter, 429 helper
│   │   ├── health.py            /health, /health/ready       (infrastructure)
│   │   └── v1/
│   │       ├── router.py        everything under /api/v1
│   │       ├── health.py        /api/v1/health               (API contract)
│   │       ├── auth.py          login, refresh, logout, me
│   │       └── admin_users.py   admin-only account management
│   ├── auth/
│   │   ├── dependencies.py      get_current_user, require_admin
│   │   └── service.py           password auth, session issue + rotation
│   ├── database/
│   │   ├── base.py              DeclarativeBase, naming convention, mixins
│   │   └── session.py           engine, get_db, check_database
│   ├── models/                  users, licenses, devices, credits, releases,
│   │                            refresh_tokens, audit_logs
│   ├── schemas/                 pydantic response models
│   ├── security/
│   │   ├── passwords.py         Argon2id
│   │   ├── tokens.py            JWT access, opaque refresh
│   │   ├── rate_limit.py        pluggable limiter (memory now, Redis later)
│   │   └── signing.py           Ed25519 entitlement / release signing
│   └── services/
│       └── audit.py             audit writes, with metadata scrubbing
├── migrations/                  Alembic; 0001_initial, 0002_refresh_rotation
└── tests/                       250 tests
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
python -m uvicorn app.main:app --reload --port 8000 --no-server-header
```

`--no-server-header` suppresses uvicorn's `Server: uvicorn/0.x.y` banner, which
otherwise tells anyone scanning exactly which version to look up CVEs for. It
cannot be done from middleware — uvicorn writes that header after middleware
runs, so setting one there produces two `Server` headers rather than replacing
the first.

Then:

- <http://localhost:8000/health> → `{"status":"ok", ...}`
- <http://localhost:8000/health/ready> → `{"status":"ok","database":"ok"}`
- <http://localhost:8000/api/v1/health>
- <http://localhost:8000/docs> (development only; disabled in production)

### 6b. Create the first administrator

There is no signup endpoint and no default account. Exactly one command brings
the first administrator into existence:

```powershell
cd backend
python -m app.cli create-admin
```

It prompts for email, name and password. The password is read with `getpass`, so
it is not echoed, not in your shell history, and not in `ps` output — and there
is deliberately no `--password` flag, because offering one guarantees it ends up
in a script somebody commits.

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
| `PHOTOFLOW_TRUSTED_HOSTS` | **yes in prod** | empty (any) | comma-separated hostnames |
| `PHOTOFLOW_JWT_SECRET` | **yes in prod** | placeholder | ≥32 chars; placeholder refused in production |
| `PHOTOFLOW_JWT_ISSUER` | no | `photoflow-api` | verified on every token |
| `PHOTOFLOW_JWT_AUDIENCE` | no | `photoflow-desktop` | verified on every token |
| `PHOTOFLOW_ACCESS_TOKEN_TTL_MINUTES` | no | `30` | keep short — access tokens are not revocable |
| `PHOTOFLOW_REFRESH_TOKEN_TTL_DAYS` | no | `30` | rotation does not extend this |
| `PHOTOFLOW_SIGNING_PRIVATE_KEY` | Phase 4 | empty | base64 Ed25519. **Secret.** Never committed or shipped |
| `PHOTOFLOW_SIGNING_PRIVATE_KEY_FILE` | Phase 4 | empty | alternative to the above, for file-mounted secrets |
| `PHOTOFLOW_SIGNING_PUBLIC_KEY` | Phase 4 | empty | base64 Ed25519. Safe to publish |
| `PHOTOFLOW_RATE_LIMIT_ENABLED` | no | `true` | cannot be false in production |
| `PHOTOFLOW_RATE_LIMIT_BACKEND` | no | `memory` | `memory` or `redis` |
| `PHOTOFLOW_RATE_LIMIT_REDIS_URL` | if redis | empty | required when the backend is `redis` |
| `PHOTOFLOW_RATE_LIMIT_LOGIN_ATTEMPTS` | no | `5` | per email, per window |
| `PHOTOFLOW_RATE_LIMIT_LOGIN_WINDOW_SECONDS` | no | `300` | |
| `PHOTOFLOW_RATE_LIMIT_REFRESH_ATTEMPTS` | no | `30` | per IP, per window |
| `PHOTOFLOW_ALLOW_SINGLE_INSTANCE_RATE_LIMIT` | no | `false` | required to run production on the memory backend |
| `PHOTOFLOW_MAX_REQUEST_BODY_BYTES` | no | `1048576` | 1 MiB |
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

## Authentication

```
POST /api/v1/auth/login     email + password  ->  access + refresh + user
POST /api/v1/auth/refresh   refresh           ->  new access + new refresh
POST /api/v1/auth/logout    refresh           ->  session revoked
GET  /api/v1/auth/me        access            ->  the caller's own account
```

### The session model

```
login  ──►  access token   30 min, signed, stateless, NOT revocable
            refresh token  30 days, opaque, stored hashed, revocable, rotating

refresh ──► the presented refresh token is SPENT and replaced.
            Its successor shares the same session_id (the "family").
            The expiry is carried forward, never extended.

logout  ──► the whole family is revoked.
```

Two properties are worth stating plainly because they are what the design buys:

**Rotation makes theft detectable.** A refresh token is single-use. If one that
has already been rotated is presented again, either the legitimate client
replayed a request or somebody is using a stolen copy — the server cannot tell
which, so it revokes the entire family. The real user is logged out and signs in
again; the thief is logged out too. An interruption the user notices beats a
compromise nobody does. The event is recorded as `REFRESH_REUSE_DETECTED`.

**Logout cannot recall an access token.** A signed, stateless token is valid
until it expires; that is what stateless means, and claiming otherwise would be
dishonest. Three things bound the damage: the 30-minute lifetime, the fact that
no *new* access token can be minted once the family is revoked, and the fact
that `get_current_user` re-reads the user row on every single request — so a
disabled account loses access immediately, mid-token-lifetime.

### Access token claims

`sub`, `role`, `type`, `iss`, `aud`, `iat`, `nbf`, `exp`, `jti`, `sid`. There is
no email, no name and no licence state: a JWT is signed, not encrypted, so
everything in it is readable by anyone holding it, and tokens end up in logs,
proxies and crash reports. `role` is present only because it is already visible
to the user it describes — and it is still re-read from the database rather than
trusted, so a forged `role: ADMIN` claim buys nothing.

Verification pins the algorithm (so `alg: none` fails), and checks the issuer,
the audience, the expiry and the token type (so a refresh token cannot be
presented as a bearer credential).

### Why there is no signup endpoint

PhotoFlow is a controlled commercial product: an account exists because it was
sold. Self-service registration would let anyone create rows in the table the
licensing model treats as the customer list. Accounts come from exactly two
places — `python -m app.cli create-admin`, and `POST /api/v1/admin/users`.

If a self-service trial is wanted later, it is a *new* endpoint with its own
rate limits, email verification and abuse handling, not a flag on an existing
one. The schema already supports it: `users.status` has `PENDING`, and
`email_verified` is there and unused.

### Failures are uniform

Wrong password, unknown email, disabled account, revoked token, expired token
and replayed token all return the same `401` with the same body. The server
records precisely which it was in the audit log and says none of it to the
caller. Anything else is an account-enumeration oracle — the thing that tells a
credential-stuffing run which of a million leaked addresses are worth attacking.

---

## Admin account management

All ADMIN-only, enforced server-side. A CLIENT constructing the request by hand
gets `403`, exactly like one who never saw a button.

```
POST /api/v1/admin/users               create a client (or another admin)
GET  /api/v1/admin/users               list, with ?role= and ?status= filters
GET  /api/v1/admin/users/{id}          one account
POST /api/v1/admin/users/{id}/disable  suspend, and revoke every live session
POST /api/v1/admin/users/{id}/enable   restore (old sessions stay revoked)
```

Creating a client, from the shell:

```powershell
curl -X POST http://localhost:8000/api/v1/admin/users `
  -H "Authorization: Bearer $ACCESS_TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"email":"studio@example.com","name":"A Studio","password":"..."}'
```

The administrator sets the initial password and communicates it out of band.
That is a deliberate simplification with a real cost: for a while, two people
know the password. The proper fix is an invitation flow — create the account
with no password, email a single-use token, let the customer choose their own —
which needs outbound email that does not exist yet. `PASSWORD_CHANGED` is
already in the audit vocabulary waiting for it.

Disabling does two things, and both are needed: it sets the status *and* revokes
every live refresh token. Status alone would leave sessions able to mint access
tokens; revocation alone would let the user log back in.

---

## Rate limiting

Protects `POST /auth/login` (keyed on both the email address and the caller's
IP) and `POST /auth/refresh` (keyed on IP). Exceeding a limit returns `429` with
a `Retry-After` header and a body that deliberately says nothing about which
limit was hit or how much budget remains. A successful login clears the email
budget, so someone who mistypes three times and then succeeds is not left one
attempt from a lockout.

Defaults: 5 login attempts per email per 5 minutes, 20 per IP (4×, because a
studio behind one NAT may have several legitimate users), 30 refreshes per IP.

### The trade-off, stated

The limiter is a fixed-window counter behind a `RateLimitBackend` interface.
Today the only implementation is in-memory, which is **per process**: with two
backend instances behind a load balancer the effective limit doubles, silently
and with no error anywhere.

Redis is deliberately not added yet. PhotoFlow has no customers and will launch
on a single instance; a second network dependency now buys nothing and adds an
outage mode (Redis down ⇒ can anyone log in?) that would have to be designed
around. What matters is that the decision cannot rot: **production refuses to
start on the memory backend** unless `PHOTOFLOW_ALLOW_SINGLE_INSTANCE_RATE_LIMIT=true`
records that somebody knows. When a second instance appears, implement
`RedisRateLimitBackend` against the existing interface and change one setting.

Setting `PHOTOFLOW_RATE_LIMIT_BACKEND=redis` today raises `NotImplementedError`
at startup rather than falling back — a deployment that believes it has a shared
limiter and does not is the worst available outcome, because it looks fine.

---

## Ed25519 signing keys

### Three different secrets, three different jobs

This is the distinction that matters most in the whole document, and conflating
any two of them is the mistake to avoid:

| | What it is | Who holds it | What it protects |
|---|---|---|---|
| **`PHOTOFLOW_JWT_SECRET`** | HS256 symmetric | backend only | session access tokens, which only this backend verifies |
| **Ed25519 private key** | asymmetric, server side | backend only | signs entitlements and release manifests |
| **Ed25519 public key** | asymmetric, client side | compiled into the desktop app | lets the app *verify* without being able to *mint* |
| **`PHOTOFLOW_STATE_KEY`** | HMAC, client side | inside the installer | stops a customer editing their own cached trial expiry |

`jwt_secret` is symmetric on purpose — whoever verifies can also mint, which is
correct when the verifier is the issuer. Entitlements cannot work that way: the
desktop app must verify offline, and an HS256 secret compiled into a Windows
binary is a shared secret with every customer who owns a hex editor. Hence
Ed25519: 32-byte keys, 64-byte signatures, fast verification, no parameter
choices to get wrong.

`PHOTOFLOW_STATE_KEY` is a fourth thing entirely and is *not* being replaced —
see the migration note below.

### Generating a key

```powershell
cd backend
python -m app.cli generate-signing-key
```

Prints two lines to stdout and nothing to disk, so the private key can go
straight into a secret manager without ever touching the filesystem of the
machine that generated it. For local development:

```powershell
python -m app.cli generate-signing-key --out-dir C:\keys\photoflow
```

writes `photoflow_signing_key` (0600) and `photoflow_signing_key.pub`, and
refuses to overwrite an existing pair unless `--force` — regenerating
invalidates every entitlement ever signed with the old key, so it has to be
deliberate.

**Keep the key directory outside the repository.** The private key must never be
committed, never packaged into the installer, never returned by an API, and
never placed in `.env.example` — which is why there is no example value there,
only a comment. A placeholder that looks like a real key is a placeholder
somebody eventually ships.

In production the private key belongs in the hosting provider's secret store,
injected as `PHOTOFLOW_SIGNING_PRIVATE_KEY` or mounted as a file and pointed at
with `PHOTOFLOW_SIGNING_PRIVATE_KEY_FILE`.

### What is signed

`SigningService.sign_envelope()` produces `{"payload": {...}, "signature":
"base64", "alg": "Ed25519"}` over a canonical JSON serialisation (sorted keys,
no incidental whitespace) — canonical because a signature covers bytes, not
meaning, so both sides must agree on the exact bytes. The verifier treats `alg`
as a label to *check*, never as an instruction about which algorithm to use;
trusting a data-supplied algorithm field is how `alg: none` happened to JWT.

### Migration path, and what is *not* changing

`core/licensing.py` in the desktop app is untouched by Phase 3, and its HMAC is
not what the Ed25519 key replaces. They solve different problems:

- The **client HMAC** signs the *local state file* — trial start date, cached
  expiry — so a customer cannot edit it in Notepad. Its own docstring is honest
  that the key ships in the binary and can be extracted, and calls that an
  accepted trade-off. It stays exactly where it is, because the cached state
  file is still worth protecting.
- The **server Ed25519 key** signs *server-issued entitlements*. The private key
  never leaves the backend, so no amount of disassembling the client produces a
  forgery.

The steps, all additive:

1. **Phase 3 (done).** The backend can generate a keypair, sign and verify.
2. **Phase 4.** `/api/v1/licenses/validate` returns a signed entitlement. The
   desktop app gains `core/entitlements`, holding the **public** key and
   verifying what it receives, cached to disk for offline grace.
3. **Phase 4.** `core/licensing.py`'s existing `HttpBackend` is pointed at the
   real API. Its `LicenseBackend` protocol already has the right shape.
4. **Phase 5.** Release manifests are signed with the same key; the updater
   verifies before executing an installer.

Nothing is removed at any step, and a desktop build predating all of it keeps
working.

---

## Audit events

Recorded to `audit_logs`, with metadata passed through a scrubber that redacts
anything credential-shaped by substring match (so `customer_api_key` is caught
as well as `api_key`):

`LOGIN_SUCCESS`, `LOGIN_FAILURE`, `LOGOUT`, `REFRESH_SUCCESS`,
`REFRESH_REUSE_DETECTED`, `ADMIN_CREATED`, `CLIENT_CREATED`, `USER_DISABLED`,
`USER_ENABLED`, `PASSWORD_CHANGED` (reserved), `RATE_LIMIT_EXCEEDED`,
`SIGNING_KEY_GENERATED`.

Passwords, access tokens, refresh tokens, private keys and API keys are never
written. `actor_ip` is an `INET` column, and a forged `X-Forwarded-For` that is
not an address becomes NULL rather than aborting the transaction that was
recording the failed login.

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

Implemented:

- **Argon2id** password hashing (`argon2-cffi` directly — passlib is effectively
  unmaintained and its bcrypt backend is broken against current releases). No
  code path writes a plaintext password anywhere; a test asserts it.
- **Access tokens**: short-lived HS256 JWTs with `iss`, `aud`, `jti`, `sid`, a
  pinned algorithm (so `alg: none` forgery fails) and a type claim (so a refresh
  token cannot be presented as a bearer credential). No personal data in the
  payload.
- **Refresh tokens**: opaque random bytes, stored as SHA-256 digests, revocable,
  single-use, with family revocation on reuse.
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
- **Trusted hosts**: an explicit allow-list in production, so a forged `Host`
  header cannot be reflected into a link or a cache entry.
- **Rate limiting** on login and refresh; cannot be switched off in production.
- **Request bodies are capped** at 1 MiB, returning `413`.
- **Security headers**: `nosniff`, `DENY`, `no-referrer`, a `default-src 'none'`
  CSP, a `Permissions-Policy`, plus HSTS in production. Run uvicorn with
  `--no-server-header` so it does not advertise its version.
- **No admin creation over HTTP.** The first administrator comes from a CLI
  command that requires shell access to the host — a far stronger boundary than
  any setup token reachable over HTTPS.

### Where each secret lives

| Secret | Lives | Never |
|---|---|---|
| `PHOTOFLOW_DATABASE_URL` | backend host env / `backend/.env` locally | desktop app, admin dashboard, git |
| `PHOTOFLOW_JWT_SECRET` | backend host env | anywhere else |
| `PHOTOFLOW_SIGNING_PRIVATE_KEY` | backend host env / secret manager | installer, repo, `.env.example`, any API response |
| Ed25519 **public** key | compiled into the desktop app — correct and safe | — |
| `PHOTOFLOW_STATE_KEY` | `utils/_secrets.py`, gitignored, baked into the build | committed. A *client* secret, unrelated to the two above |
| Admin API token (Phase 6) | your machine, admin dashboard env | committed, emailed, desktop app |
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

## What Phase 3 deliberately does not do

- **No public signup** — see *Why there is no signup endpoint* above.
- No password-reset or change-password endpoint. Both need outbound email, which
  does not exist yet; `PASSWORD_CHANGED` is reserved in the audit vocabulary.
- No licence issue/activate/validate endpoints (Phase 4).
- No credit endpoints — the schema exists, `PHOTOFLOW_CREDITS_ENABLED=false`.
- No admin dashboard (Phase 6).
- No deployment. Neon production, hosting, TLS, domain and production secrets are
  configured separately, after the local backend is proven.
- No desktop integration. `core/licensing.py` is untouched: its `HttpBackend`
  already speaks a compatible shape, and its local state HMAC is a separate
  mechanism that stays. Wiring both up is Phase 4.
- No entitlement-issuing endpoint. The signing infrastructure exists and is
  tested; nothing calls it yet, so a development machine with no key configured
  is fine.
- No Redis rate-limit backend — see the trade-off above.

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
