# PhotoFlow Backend — overview and deployment prerequisites

The working documentation for the backend lives next to the code, in
[`backend/README.md`](../backend/README.md) — architecture, environment
variables, database setup, migration workflow, running locally, running tests
and the security model. Keeping it there means it is the file you are already
looking at when you change something, which is the only way documentation stays
true.

This page covers what does *not* belong in the code directory: how the backend
relates to the rest of the product, and what has to be true before it is
deployed.

Related reading: [`docs/PRODUCTION_ARCHITECTURE_AUDIT.md`](PRODUCTION_ARCHITECTURE_AUDIT.md)
(the full architecture review this backend implements) and
[`docs/SHIPPING_PLAN.md`](SHIPPING_PLAN.md).

---

## Status

**Phase 3 complete — authentication and security foundation, not deployed.**

Phase 2 built the structure: environment-based configuration, PostgreSQL, Alembic,
nine models, password hashing, token strategy, health endpoints, structured
logging, safe error handling.

Phase 3 added the authentication API (`login` / `refresh` / `logout` / `me`),
refresh-token rotation with reuse detection, admin-only account management,
rate limiting on the authentication endpoints, the Ed25519 entitlement-signing
infrastructure, an operator CLI for bootstrapping the first administrator and
generating signing keys, security audit events, and request hardening
(trusted hosts, body limits, security headers). 250 passing tests.

Not done, on purpose: licence workflow (Phase 4), credit workflow (Phase 4),
admin dashboard UI (Phase 6), public signup (not planned), any deployment.

---

## How the pieces relate

```
   Company website (separate repo, untracked website/ directory)
              │  download link → GitHub Releases
              ▼
   PhotoFlow-Setup-x.y.z.exe   (Inno Setup, code-signed)
              │
              ▼
   PhotoFlow Desktop — the client's Windows PC
   photos analysed locally and never uploaded
   knows: API base URL + (later) an Ed25519 public key
   knows no database URL and no secrets
              │
              │  HTTPS
              ▼
   FastAPI backend  ──────────────►  PostgreSQL (Neon)
   holds every secret                 the source of truth
              ▲
              │  HTTPS + admin token
   Local admin dashboard — localhost only, never public, no database access
```

Two boundaries that are not negotiable:

1. **The desktop application never connects to PostgreSQL.** A connection string
   inside a Windows installer is a connection string in the hands of every
   customer, and it cannot be rotated out of software already on their disk.
2. **The admin dashboard never connects to PostgreSQL either.** It is an HTTPS
   client of this API like any other, so every administrative action passes
   through the same authorisation and audit-logging path.

---

## Before deploying to production

Nothing here is done yet; this is the checklist for when we do it.

### Operator commands

Run from `backend/` with the virtual environment active. They act on the
database and configuration the current environment selects — the same
`PHOTOFLOW_*` variables the server reads, so there is no second place for a
target to drift.

```powershell
python -m app.cli create-admin            # prompts; no --password flag exists
python -m app.cli generate-signing-key    # prints a keypair; writes nothing
python -m app.cli show-config             # effective config, secrets redacted
```

There is deliberately **no HTTP endpoint** that creates administrators. Anything
reachable over HTTPS that can mint an admin is one bug away from being an
unauthenticated privilege-escalation route; requiring shell access to the host is
a much stronger boundary and costs one command, once.

### Must be true before the first production deploy

- [ ] A Neon **production** branch exists, separate from `dev`, and its URL has
      never been on a development machine.
- [ ] `PHOTOFLOW_JWT_SECRET` is generated (≥32 chars) and stored in the hosting
      provider's secret store — not in a file, not in the repository.
- [ ] `PHOTOFLOW_ENVIRONMENT=production`, `PHOTOFLOW_DEBUG=false`,
      `PHOTOFLOW_LOG_JSON=true`.
- [ ] `PHOTOFLOW_TRUSTED_HOSTS` lists the real hostnames.
- [ ] An Ed25519 keypair has been generated and the **private** key is in the
      secret store — not on a laptop, not in the repository, not in the
      installer. The public key is recorded somewhere it can be compiled into
      the desktop app in Phase 4.
- [ ] Rate limiting: either a Redis backend, or
      `PHOTOFLOW_ALLOW_SINGLE_INSTANCE_RATE_LIMIT=true` recording that this
      deployment is genuinely one instance. The application refuses to start
      otherwise.
- [ ] uvicorn runs with `--no-server-header`.
- [ ] The first administrator has been created with `python -m app.cli
      create-admin` against the production database, once.
- [ ] `PHOTOFLOW_API_BASE_URL` is the real HTTPS URL, and `PHOTOFLOW_CORS_ORIGINS`
      lists explicit origins.
- [ ] TLS terminates in front of the app; plain HTTP is redirected or refused.
- [ ] `PHOTOFLOW_MIGRATION_CONFIRM=production alembic upgrade head` has been run
      once, deliberately, with the printed target read before confirming.
- [ ] A backup and restore has been *tested*, not merely configured. An untested
      backup is a hope.
- [ ] Rate limiting is extended to anything else that grants value as Phase 4
      adds it (licence activation especially).

The application refuses to start if the configuration items above are wrong,
which is the point: a misconfigured production backend should fail visibly at
deploy time rather than quietly issue tokens signed with a key published on
GitHub, or accept unlimited password guesses.

### Choices deliberately left open

The hosting provider is **not chosen**. The audit sketches Fly.io / Railway /
Render on a cheap *paid* tier — free tiers sleep, and a sleeping licence server
means a customer waits thirty seconds to launch the app they paid for — but that
is a recommendation, not a decision, and nothing in the code assumes it.

Also still open, from the audit: the payment provider (Lemon Squeezy or Paddle as
merchant of record), and the code-signing certificate, which is the gating
purchase for shipping a Windows installer at all.

---

## Note for whoever does the rename

The audit records that the company name in the code is not yet **SA Innovations**.
The backend introduces no new company-name strings beyond the API's `app_name`
("PhotoFlow API", a product name rather than a company one), so it does not add
to that work — but the rename remains a blocker to be swept across the whole
repository before release, not a thing to do piecemeal.


---

## Security model in one page

Five keys, four of them secret, and confusing any two is the mistake to avoid.

| | Kind | Held by | Protects |
|---|---|---|---|
| `PHOTOFLOW_DATABASE_URL` | connection string | backend only | everything |
| `PHOTOFLOW_JWT_SECRET` | HS256 symmetric | backend only | session access tokens |
| Ed25519 **private** key | asymmetric | backend only | signs entitlements and release manifests |
| Ed25519 **public** key | asymmetric | desktop app | lets the app verify without being able to mint |
| `PHOTOFLOW_STATE_KEY` | HMAC | inside the installer | the *client's own* cached trial state |

The third and fourth are the pair that makes offline entitlement checking safe.
The fifth is a different mechanism entirely, already in `core/licensing.py`,
which Phase 3 does not touch and Phase 4 does not remove: it stops a customer
editing their cached expiry in a text editor, and its docstring is honest that
the key ships in the binary and can be extracted.

**Client secret vs server secret vs public verification key** — the distinction
the Phase 3 brief asked to be made explicit:

- A **client secret** (`PHOTOFLOW_STATE_KEY`) is not really a secret. It is on
  hardware the customer owns and can be extracted by anyone determined. It raises
  the effort of casual tampering and nothing more. Never use one to protect
  something that matters.
- A **server secret** (`DATABASE_URL`, `JWT_SECRET`, the Ed25519 private key) is
  a real secret. It exists on infrastructure you control, can be rotated, and
  must never be shipped anywhere a customer can read it.
- A **public verification key** is not a secret at all, and publishing it is
  the point. It is what lets an untrusted client check a server's claim without
  being able to fabricate one.

The threat model follows from that: the desktop app is an untrusted client on
hardware the customer owns, so it *asserts* and the backend *decides*. What none
of this prevents is someone patching the binary to skip the checks — unsolvable
for desktop software, as `core/licensing.py` says plainly. The defence is that
anything expensive requires a server response, so patching the client gains
nothing.
