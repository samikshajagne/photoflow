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

**Phase 2 complete — foundation only, not deployed.**

Done: application structure, environment-based configuration, PostgreSQL
connection, Alembic with a reproducible initial migration, all nine core models,
password hashing, token strategy, current-user and role dependencies, health
endpoints, structured logging, safe error handling, 111 passing tests.

Not done, on purpose: login endpoints (Phase 3), licence workflow (Phase 4),
credit workflow (Phase 4), admin dashboard (Phase 6), any deployment.

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

### Must be true before the first production deploy

- [ ] A Neon **production** branch exists, separate from `dev`, and its URL has
      never been on a development machine.
- [ ] `PHOTOFLOW_JWT_SECRET` is generated (≥32 chars) and stored in the hosting
      provider's secret store — not in a file, not in the repository.
- [ ] `PHOTOFLOW_ENVIRONMENT=production`, `PHOTOFLOW_DEBUG=false`,
      `PHOTOFLOW_LOG_JSON=true`.
- [ ] `PHOTOFLOW_API_BASE_URL` is the real HTTPS URL, and `PHOTOFLOW_CORS_ORIGINS`
      lists explicit origins.
- [ ] TLS terminates in front of the app; plain HTTP is redirected or refused.
- [ ] `PHOTOFLOW_MIGRATION_CONFIRM=production alembic upgrade head` has been run
      once, deliberately, with the printed target read before confirming.
- [ ] A backup and restore has been *tested*, not merely configured. An untested
      backup is a hope.
- [ ] Rate limiting is in place on anything that grants value — Phase 4, but it
      must exist before real licences do.

The application refuses to start if the first four are wrong, which is the point:
a misconfigured production backend should fail visibly at deploy time rather than
quietly issue tokens signed with a key published on GitHub.

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
