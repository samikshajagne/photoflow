# PhotoFlow — Production Deployment & Licensing Audit

*Repository audit only. No code changed. Written 2026-08-10 against the working tree at
`D:\startup\photoflow`.*

---

## 0. Read this first — three things that change the plan

### 0.1 About 106 files of work are not committed to git

```
Last commit:  419b95f "oprnai"  —  2026-07-22
Working tree: 44 modified, 11 deleted, 51 untracked
```

Everything from the last three weeks — `core/licensing.py`, `core/telemetry.py`,
`core/diagnostics.py`, the whole `packaging/` directory, `LICENSE`, the collage and
passport modules, `docs/SHIPPING_PLAN.md`, ~20 test files — exists **only on this
laptop's disk**. There is no second copy anywhere.

Nothing else in this document matters until that is fixed. A disk failure or a bad
`git checkout` today loses the licensing system, the installer, the EULA and the test
suite that proves them. **Commit and push before any architecture work begins.**

One caution when you do: `.env` (real OpenAI key) and `utils/_secrets.py` are gitignored
and have never been committed — verified. Keep it that way.

### 0.2 The company name in the code is "Samiksha Technologies", not "SA Innovations"

`utils/version.py` hardcodes `COMPANY_NAME = "Samiksha Technologies"`,
`COMPANY_DOMAIN = "samikshatech.com"`, `SUPPORT_EMAIL = "hello@samikshatech.com"`. Those
values propagate into the Windows version resource, the Inno Setup installer, the app's
`QApplication.setOrganizationName` (which decides the `%LOCALAPPDATA%` folder name), the
EULA in `LICENSE`, the update-manifest URL, and all seven website pages.

If the company is now SA Innovations this is a real rename, not a find-and-replace — and
one of the touched values (`setOrganizationName`) changes where user data lives, so a
rename after the first customer installs means their presets and licence file move.
**Decide the name before the first paid build ships.** This audit assumes the decision is
still open and uses "the company" throughout.

### 0.3 The privacy claim and the album feature currently contradict each other

`core/vision_brain.py` base64-encodes **full client photographs** and POSTs them to
OpenAI's GPT-4o Vision API (`core/vision_brain.py:257-272`), driven from
`core/album/orchestrator.py:197` via `core/brain_stage.py`. Album generation — the flagship
feature — sends wedding photos off the machine.

Meanwhile the website home page and `docs/OWNER_DASHBOARD_PLAN.md` lead with *"Your
clients' photos stay on your machine. Nothing is uploaded."* And your requirement #10 says
"do not upload client photographs to our server."

Both cannot be true. This is not a bug to patch quietly — it is a product decision with a
legal edge (DPDP Act 2023 notice-and-consent, and a studio's own contract with their
clients). Options are laid out in §7. It also interacts directly with the credits system:
see §4.3.

---

## A. Current architecture

A single Python package, no server, no database, no accounts.

```
photoflow/
├── main.py                 CLI/legacy entry; loads .env from the project root
├── ui_qt/                  PyQt6 desktop UI — main.py, views/, workers/, models/, theme/
├── core/                   All analysis + the three product modes
│   ├── scanner, duplicate_detector, blur_detector, quality_scorer,
│   │   face_detector, face_embedder, person_cluster, identity,
│   │   organizer, pipeline, timeline, event_classifier
│   ├── album/              layout / template / raster / orchestrator / export engine
│   ├── collage*.py         collage mode (7 layouts, shapes, text, presets)
│   ├── passport_photo.py, face_beautify.py   passport mode
│   ├── vision_brain.py     OpenAI GPT-4o Vision  ← network egress of photos
│   ├── licensing.py        trial / activation / offline grace  (client-side only)
│   ├── telemetry.py        opt-in counters, closed event vocabulary
│   └── diagnostics.py      support report with path scrubbing
├── persistence/            analysis_cache.py (signature-invalidated), identity_store.py
├── utils/                  config.py, logger.py, paths.py, version.py
├── packaging/              photoflow.spec (PyInstaller), installer.iss (Inno), build.bat,
│                           preflight.py, make_secrets.py, make_version_info.py
├── website/                7-page static site + version.json update manifest + check_site.py
├── tests/                  ~95 files, suite reported at 1348 passing
├── data/                   default_config.yaml, models/, fonts/, templates/
└── .github/workflows/ci.yml   tests + preflight + website jobs
```

**Storage today:** a per-user SQLite/JSON analysis cache (`data/cache.db`), a JSON identity
store, JSON collage presets and an HMAC-signed `license.json` — all under
`utils/paths.user_data_dir()`. No relational database, no migrations.

**Network today:** exactly two egress points — OpenAI Vision (photos, with the customer's
own key) and `HttpBackend` in `core/licensing.py`, which is written but **never
instantiated**. `LicenseManager` defaults to `OfflineBackend`, which accepts any key ≥8
characters. `UPDATE_MANIFEST_URL` is defined in `utils/version.py` and **referenced
nowhere** — there is no update check in the app yet. `TELEMETRY_ENDPOINT` is explicitly
passed as `None` in `ui_qt/main.py:_start_licensing`.

So: the *shape* of the commercial layer exists, and nothing is plugged in.

---

## B. What should stay

Reuse, do not rewrite:

| Component | Why it stays |
|---|---|
| `core/licensing.LicenseBackend` protocol | Two methods (`activate`, `validate`). Your new backend becomes a third implementation; nothing else in the app changes. This is exactly the seam the new plan needs. |
| `core/licensing` state file + HMAC signing + grace logic | The offline/grace design (14d trial → 7d recheck → 21d grace, *a failed check never deactivates*) is more carefully reasoned than most commercial products. Keep the philosophy; §11 explains the one place it must tighten. |
| `machine_fingerprint()` | Already a hashed, non-identifying digest with a test asserting the hostname does not leak. Satisfies your "do not rely on MAC alone / avoid PII" requirement as written. |
| `core/telemetry.py` closed `ALLOWED_EVENTS` vocabulary | The enforcement mechanism for "we only send counts". Point its endpoint at the new backend; change nothing else. |
| `core/diagnostics.py` + path scrubbing | Support tooling you would otherwise have to build. |
| `utils/paths.py` (`resource_path` read / `user_data_dir` write) | The rule that prevents the Program-Files-is-read-only bug class. Every new module must follow it. |
| `utils/version.py` single source of truth | The update system depends on exactly this. |
| Entire `packaging/` directory, incl. `preflight.py` | Stable Inno `AppId`, one-folder build, UPX off, version consistency checks. Genuinely done. |
| `tests/` (~1348 passing) | The regression net that lets you refactor safely. |
| All of `core/` analysis + `core/album/` | Untouched by this work. |

### What is genuinely missing vs. your spec

Nothing in the repo does: user accounts, passwords, roles, a real licence server, a
database, migrations, credits (the word does not appear in the codebase outside an
unrelated comment), device activation records, an admin dashboard, release management, or
an update check. All net-new.

---

## C. What should be removed — and the honest answer on `website/`

**`website/` is safe to remove from the Python application's perspective.** No module
imports it; `photoflow.spec` does not bundle it; the installer does not reference it.

But three things break if you `rm -rf website/` today, and all three are fixable in the
same commit:

1. **CI breaks.** `.github/workflows/ci.yml` has a dedicated `website` job that runs
   `website/check_site.py`. Delete the job with the folder.
2. **The update manifest disappears.** `website/version.json` is the file
   `UPDATE_MANIFEST_URL` points at (`https://samikshatech.com/version.json`). Under the
   new architecture the backend serves `GET /updates/latest` instead — so this only
   matters if you delete the site *before* the backend endpoint exists. Order matters.
3. **Docs go stale.** `README.md`, `docs/SHIPPING_PLAN.md` and `website/README.md` all
   describe the site as part of this repo.

**Recommendation — do not delete it yet.** Move it, in this order:

1. Commit everything (§0.1) so the site's history is preserved and pushed.
2. `git mv website/ ../sa-website/` into its own repo (or copy it — the design work,
   `_headers` CSP config and `check_site.py` are worth keeping as the starting point for
   the new company site, not throwing away).
3. Ship the backend's `/updates/latest`, point `UPDATE_MANIFEST_URL` at it.
4. *Then* delete `website/` here, and the CI job with it, in one commit.

Also removable, lower stakes: `docs/PHASE*_TESTING.md` (16 files, historical) → `docs/history/`.
`.pytest_cache/` and `logs/` should not be in the tree at all.

**Do not remove:** `main.py` (a real second entry point), `scripts/`, `tools/` — untracked
but referenced by dev workflow.

---

## D. What needs to be added

```
photoflow/                          (this repo — desktop only, after §C)
├── core/ ui_qt/ persistence/ utils/ packaging/ tests/    [unchanged]
├── core/account/            NEW  login, token storage, refresh
├── core/licensing.py        MODIFIED  ApiBackend implementing LicenseBackend
├── core/entitlements.py     NEW  signed-token verification, credit reserve/commit
└── core/updates.py          NEW  manifest fetch, version compare, hash verify

backend/                            NEW REPO (or top-level dir — see §J)
├── app/
│   ├── api/       auth, me, license, credits, devices, updates
│   ├── admin/     admin-only routers
│   ├── models/    SQLAlchemy
│   ├── schemas/   Pydantic
│   ├── services/  licensing, credits, activation, releases
│   ├── security/  hashing, JWT, Ed25519 signing, rate limits
│   └── db/
├── migrations/    Alembic
└── tests/

admin/                              NEW  local-only dashboard
```

Recommended stack, chosen for *your* maintainability rather than novelty: **FastAPI +
SQLAlchemy + Alembic + PostgreSQL**, Pydantic schemas, `argon2` password hashing,
`PyNaCl` for Ed25519. It is the same language as the desktop app, so there is one
ecosystem to maintain, and `docs/OWNER_DASHBOARD_PLAN.md` already reached the same
conclusion independently.

**Admin dashboard:** build it as **FastAPI + server-rendered Jinja templates + HTMX**,
running as a separate local process that calls the backend API over HTTPS with an admin
token — never touching Postgres directly, exactly as you specified. A React SPA here would
double the toolchain for a single-user internal tool.

---

## E. Proposed architecture

```
   Public company website (separate repo)
              │  download link → GitHub Releases
              ▼
   PhotoFlow-Setup-x.y.z.exe   (Inno Setup, code-signed)
              │
              ▼
   ┌──────────────────────────────────────────┐
   │ PhotoFlow Desktop — client's Windows PC  │
   │                                          │
   │  Photos ──► local analysis  (never sent) │
   │                                          │
   │  core/account       login, JWT           │
   │  core/entitlements  signed token cache   │
   │  core/updates       version check        │
   │  Knows: API_BASE_URL + public verify key │
   │  Knows no DB URL, no secrets             │
   └───────────────┬──────────────────────────┘
                   │ HTTPS (TLS only)
                   ▼
   ┌──────────────────────────────────────────┐
   │ PhotoFlow Backend API   (hosted)         │
   │  /auth /me /license /credits             │
   │  /devices /updates                       │
   │  /admin/*   (separate auth, IP/token)    │
   │  Holds: DB creds, Ed25519 private key,   │
   │         JWT secret, webhook secrets      │
   └───────────────┬──────────────────────────┘
                   │
                   ▼
        PostgreSQL (Neon)  ── the source of truth
                   ▲
                   │ HTTPS, admin token
   ┌───────────────┴──────────────┐        ┌──────────────────────┐
   │ Local Admin Dashboard        │        │ Payment provider     │
   │ localhost:8787 — your PC     │        │ (Lemon Squeezy /     │
   │ never public, no DB access   │        │  Paddle) → webhook   │
   └──────────────────────────────┘        └──────────┬───────────┘
                                                      │ signed webhook
                                                      ▼  grants credits
                                              PhotoFlow Backend API
```

Two deliberate choices beyond your brief, both worth arguing about:

- **A payment provider sits beside the backend, not inside it.** Building card payments,
  Indian GST, invoicing and refunds yourself is a bigger project than everything else in
  this document combined. Lemon Squeezy or Paddle act as merchant of record and hit your
  `/webhooks/payment` endpoint; your backend stays the source of truth for licences and
  credits (which no payment provider can model properly). This matches the conclusion
  already recorded in `docs/OWNER_DASHBOARD_PLAN.md`.
- **Releases are hosted on GitHub Releases, not your backend.** A 300–600 MB installer on
  an app server is bandwidth you pay for; GitHub Releases is free and has a permanent
  `releases/latest/download/...` URL. Your backend serves the *metadata and the signature*,
  which is the part that must be trustworthy.

---

## F. Database schema

```
users
  id              uuid pk
  email           citext unique not null
  name            text
  password_hash   text not null            -- argon2id, never plaintext
  role            enum(ADMIN, CLIENT) not null default CLIENT
  status          enum(ACTIVE, DISABLED, PENDING) not null default ACTIVE
  email_verified  bool default false
  created_at / updated_at / last_login_at / last_seen_at   timestamptz
  index (email), (status)

licenses
  id                uuid pk
  user_id           uuid fk → users.id  on delete restrict
  key               text unique not null       -- store a hash + last-4 for display
  product           text not null default 'photoflow'
  plan              text not null              -- free_trial | monthly | annual | lifetime | studio
  status            enum(PENDING, ACTIVE, EXPIRED, REVOKED, SUSPENDED) not null
  activation_limit  int not null default 1
  starts_at / expires_at / revoked_at / last_validated_at   timestamptz null
  notes             text
  created_at / updated_at
  index (user_id), (status), (expires_at)
  -- NOTE: expiry is derived from expires_at at read time, never a stale status column.
  --       A nightly job may materialise EXPIRED for reporting, but reads must not trust it.

devices
  id            uuid pk
  user_id       uuid fk → users.id  on delete cascade
  fingerprint   text not null              -- hashed, from machine_fingerprint()
  name          text                       -- user-supplied nickname, optional
  os            text                       -- "Windows 11 23H2"
  app_version   text
  first_seen_at / last_seen_at   timestamptz
  unique (user_id, fingerprint)
  index (fingerprint)

license_activations                        -- the join: which device holds which seat
  id             uuid pk
  license_id     uuid fk → licenses.id  on delete cascade
  device_id      uuid fk → devices.id   on delete cascade
  status         enum(ACTIVE, DEACTIVATED, REVOKED) not null
  activated_at / deactivated_at   timestamptz
  unique (license_id, device_id) where status = 'ACTIVE'   -- partial unique index
  index (license_id, status)
  -- seat count = count(*) where license_id = ? and status = 'ACTIVE'

credit_transactions                        -- append-only ledger, never UPDATE or DELETE
  id             uuid pk
  user_id        uuid fk → users.id  on delete restrict
  license_id     uuid fk → licenses.id  null
  amount         bigint not null            -- signed: +grant, −usage
  type           enum(PURCHASE, ADMIN_GRANT, USAGE, REFUND, BONUS, ADJUSTMENT, EXPIRY)
  reason         text
  reference_id   text                       -- payment id, or the client's idempotency key
  balance_after  bigint not null            -- materialised for cheap reads + audit
  created_at     timestamptz
  unique (user_id, reference_id) where reference_id is not null   -- idempotency
  index (user_id, created_at desc)

credit_reservations                        -- see §4.3; makes offline usage safe
  id             uuid pk
  user_id        uuid fk → users.id
  amount         bigint not null
  status         enum(OPEN, COMMITTED, RELEASED, EXPIRED)
  expires_at     timestamptz not null       -- auto-release, so a crash can't strand credits
  created_at / settled_at
  index (user_id, status), (expires_at) where status = 'OPEN'

releases
  id                 uuid pk
  version            text unique not null    -- semver
  channel            enum(STABLE, BETA) not null
  status             enum(DRAFT, PUBLISHED, YANKED) not null
  released_at        timestamptz
  notes              text
  notes_url          text
  download_url       text not null
  installer_filename text not null
  sha256             char(64) not null
  signature          text not null           -- Ed25519 over the manifest, base64
  minimum_supported  text
  critical           bool default false
  index (channel, status, released_at desc)

refresh_tokens
  id           uuid pk
  user_id      uuid fk → users.id  on delete cascade
  token_hash   text unique not null          -- store the hash, never the token
  device_id    uuid fk → devices.id  null
  issued_at / expires_at / revoked_at
  index (user_id), (expires_at)

audit_logs
  id            bigserial pk
  actor_user_id uuid fk → users.id  null     -- null = system
  actor_ip      inet
  action        text not null                -- ADMIN_CREATED_LICENSE, DEVICE_REVOKED, …
  target_type   text                         -- 'license' | 'user' | 'device' | 'release'
  target_id     text
  metadata      jsonb                        -- never tokens, keys or passwords
  created_at    timestamptz
  index (created_at desc), (actor_user_id), (target_type, target_id)
```

Relationships:

```
User ─┬─< License ─< LicenseActivation >─ Device >─ User
      ├─< CreditTransaction   (append-only)
      ├─< CreditReservation
      └─< RefreshToken
Release   (standalone)
AuditLog  (references anything, by loose type+id — deliberately not FK'd,
           so deleting a target never destroys the audit trail)
```

Three points that are easy to get wrong and matter:

- **The ledger is append-only.** Balance = `SUM(amount)`, with `balance_after` materialised
  for cheap reads. A correction is a new `ADJUSTMENT` row, never an edit. This is what makes
  a credit dispute with a customer answerable.
- **Idempotency is a database constraint, not application logic.** The partial unique index
  on `(user_id, reference_id)` means a retried request after a network timeout cannot
  double-charge, even if the retry logic has a bug.
- **`audit_logs` has no foreign keys** on purpose. Deleting a user must not delete the
  record that you deleted them.

---

## G. Security model — every secret, and where it lives

| Secret | Lives | Never |
|---|---|---|
| PostgreSQL connection string | Backend host env var only | In the desktop app, in the admin dashboard, in git |
| JWT signing secret | Backend host env var | Anywhere else |
| **Ed25519 private key** (signs entitlement tokens *and* release manifests) | Backend host env var / secret manager | In the installer, in the repo, on your laptop unencrypted |
| Ed25519 **public** key | Compiled into the desktop app — this is correct and safe | — |
| Payment webhook secret | Backend host env var | — |
| Admin API token | Your machine only, in the local dashboard's env | Committed, emailed, or in the desktop app |
| `PHOTOFLOW_STATE_KEY` (HMAC for local state) | `utils/_secrets.py`, gitignored, generated by `packaging/make_secrets.py` | Committed. **Currently still the placeholder — see §I.3** |
| `OPENAI_API_KEY` | See §7 — this is an open decision, not a settled one | Shipped inside the installer under any circumstances |
| Authenticode code-signing cert | Hardware token / encrypted store, password not in the build script | In the repo |

**Threat model, stated plainly:** the desktop app is an untrusted client running on
hardware the customer owns. Everything it says about itself can be forged. So:

- The app **asserts** a device fingerprint; the backend **decides** whether to allow it.
- The app **displays** a credit balance; the backend **owns** it. A tampered local balance
  buys nothing, because spending requires a server-issued reservation.
- The app **verifies** an entitlement token with a public key; it cannot **mint** one.
- Any endpoint that grants value (`/credits`, `/license/activate`, `/admin/*`) is
  rate-limited and audit-logged.

What this does **not** protect against, and you should not pretend otherwise: a determined
person patching the binary to skip the checks entirely. That is unsolvable for desktop
software, which `core/licensing.py`'s own docstring already says honestly. The defence is
that credits gate *server-issued value* — if the expensive operation needs a server
response, patching the client gains nothing.

---

## H. Deployment plan

| Component | Where | Cost |
|---|---|---|
| Company website | Cloudflare Pages (free tier, unlimited bandwidth) | ₹0 + domain ~₹1,200/yr |
| Installer binaries | GitHub Releases, permanent `latest/download` URL | ₹0 |
| Backend API | Fly.io / Railway / Render — start on the cheapest paid tier, not free (free tiers sleep, and a sleeping licence server means a customer waits 30s to launch) | ~₹400–800/mo |
| PostgreSQL | Neon — separate `dev` and `prod` branches | ₹0 → ~₹1,600/mo at scale |
| Local admin dashboard | `localhost:8787` on your PC, never exposed | ₹0 |
| Payment | Lemon Squeezy / Paddle, merchant of record | ~5% + fee per sale |
| **Code-signing certificate** | OV ~₹15–35k/yr, EV ~₹25–60k/yr | **The gating purchase — see §I.5** |

Installed on client machines: the PyInstaller one-folder build under `C:\Program Files`,
user data under `%LOCALAPPDATA%`, and nothing else. No database, no secrets, no source.

---

## I. Risks in the current codebase

Ordered by how much damage each does if ignored.

**1. Uncommitted work (§0.1).** Total loss of three weeks of shipping engineering on a
single disk failure. *Fix: today, before anything else.*

**2. Album generation uploads client photos to OpenAI (§0.3).** Contradicts the marketing
claim, your requirement #10, and a studio's likely contract with their clients. Also an
unbounded per-album cost sitting on whoever's API key it is. *Fix: a product decision, §7.*

**3. `_STATE_SIGNING_KEY` is still the placeholder.** `core.licensing.using_placeholder_key()`
returns True in a fresh checkout, and `utils/_secrets.py` does not exist on this machine.
Any build shipped today has a signing key that is published in the source. `preflight.py`
warns about it; the warning has not been acted on. *Fix: run `packaging/make_secrets.py`,
back the key up somewhere you will not lose it — losing it forces every customer to
re-enter their key.*

**4. `.env` is written to the project root — a fourth instance of the writable-path bug.**
`ui_qt/views/api_settings_dialog.py:257` upserts `OPENAI_API_KEY` into the project-root
`.env`, and `ui_qt/workers/analysis_process.py:34` and `main.py:39` read from there. Under
`C:\Program Files` that write **fails for every real customer** while working perfectly in
development. This is exactly the bug class `utils/paths.py` was created to prevent
(presets, model downloads and logs were the first three). *Fix: move API-key storage to
`user_data_dir()`, or to the OS credential store.*

**5. No code-signing certificate.** Without Authenticode, Windows SmartScreen shows an
"unrecognised app" warning on every download, and — critically for your §8 — **you cannot
verify the publisher of an update**. SHA-256 over HTTPS protects against corruption and
casual MITM, but signing is what makes auto-update *safe* rather than merely *checked*.
Auto-update should not ship before the certificate does.

**6. `packaging/build.bat` has never been run, and no clean-machine install test exists.**
The single most informative test available — build on this box, install on a machine that
has never had Python — is still outstanding. It cannot be done from a sandbox.

**7. InsightFace `buffalo_l` weights are non-commercial-licensed.** Already handled by the
`requirements.txt` / `requirements-extra.txt` split (`insightface` is an extra and must
never be in a release venv), and `preflight.py` warns when it is installed. Keep it that
way; verify before every paid build.

**8. `OfflineBackend` accepts any key of ≥8 characters** and is the default in
`LicenseManager.__init__`. Fine today; must not survive into a paid build. *Fix: make the
default backend configuration-driven, and have `preflight.py` fail — not warn — on a
release build that would ship `OfflineBackend`.*

**9. Adding accounts makes you a data fiduciary under the DPDP Act 2023.** Today PhotoFlow
holds no personal data at all. Email addresses, names, device records and IP addresses in
audit logs change that: you need a privacy notice, a consent record, a deletion path, and
breach-notification readiness. Cheap to design in now, expensive to retrofit.

**10. Test-suite scope.** ~1348 tests, but none cover a backend that does not exist, and
the desktop side has no tests for API-unavailable, expired-token or reservation-failure
paths. Those are the paths that will actually break at a customer's site.

---

## J. Implementation plan

Phases sized so each ends with something demonstrable, and each can be approved separately.

**Phase 0 — Preserve (today, ~30 min).** Commit and push all 106 files. Verify no secrets
in the diff. Tag `v0.9.0-pre-backend`. *Blocks everything.*

**Phase 1 — Decisions (no code).** Company name; OpenAI/privacy resolution; build-vs-buy
for payments; monorepo vs. separate backend repo. §0.2, §0.3, §E.

**Phase 2 — Backend foundation.** FastAPI skeleton, Neon dev+prod branches, Alembic,
config/secrets handling, health check, deploy pipeline. Deliverable: `/health` responds in
production over TLS.

**Phase 3 — Auth + users + licences.** argon2 hashing, JWT access + refresh, roles,
licence generation/validation/expiry/revocation, `LicenseBackend` contract honoured so
`core/licensing.py` needs no restructuring. Deliverable: a real key activates against the
real server.

**Phase 4 — Devices.** Activation, deactivation, seat limits, revocation, admin override.
Deliverable: seat limit enforced; second machine refused with a clear message.

**Phase 5 — Credits.** Ledger, reservations, admin adjustments, idempotency. Deliverable: a
credit consumed on one machine is reflected on another within a minute, and a replayed
request charges once.

**Phase 6 — Local admin dashboard.** Overview, users, licences, devices, credits, releases,
audit log. Deliverable: you can onboard a customer end-to-end without touching SQL.

**Phase 7 — Desktop integration.** `core/account`, `core/entitlements`, login UI, offline
grace with signed tokens, balance display. Deliverable: full flow on a clean Windows box.

**Phase 8 — Updates.** `/updates/latest`, `core/updates.py`, hash + Ed25519 verification,
non-blocking in-app banner. Deliverable: a published release is offered to a running client.
*Auto-install deferred until the signing certificate exists.*

**Phase 9 — Packaging + first signed build.** Certificate purchase, `build.bat` first real
run, clean-machine install test.

**Phase 10 — Security review.** The §15 checklist, plus an explicit attempt to break your
own credit system from a patched client.

Realistic effort: phases 2–7 are the bulk. Phases 8–10 depend on a purchase and a physical
test machine, not on code.

---

## Open questions for you (§7 and friends)

**7. The OpenAI decision.** Three coherent options, no fourth:

| Option | Privacy claim | Cost model | Effort |
|---|---|---|---|
| **A. Customer brings their own key** (status quo) | Must change to "photos stay local unless you enable Vision, which sends them to OpenAI" | Customer pays OpenAI directly; zero COGS for you | Lowest — mostly honest copy + a clear consent dialog |
| **B. Drop Vision; local-only labelling** | "Nothing is uploaded" becomes true again | Zero | Medium — album quality drops to the MediaPipe fallback |
| **C. Proxy Vision through your backend, metered by credits** | Weakest — photos transit *your* server | You pay OpenAI, recovered via credits | Highest — bandwidth, retention policy, DPDP exposure |

Option C is the one that makes the credits system obviously worth building; it is also the
one that most damages the differentiator you are selling on. Option A with honest wording is
probably right for now, with credits metering something else.

**4.3. What do credits actually buy?** This is unanswered in the brief and it determines
the whole design. If credits meter a **local** operation (albums generated, photos
processed), a customer can go offline and consume without limit — the ledger can only be
advisory. If credits meter a **server-side** operation (Vision calls, cloud proofing), the
server can enforce them absolutely. The `credit_reservations` table in §F is the compromise:
the app reserves a batch online, works offline against the reservation, and commits on
reconnect — bounded, not unlimited. But it needs your answer on *what is being sold* before
it can be sized.

**11. Offline grace.** The existing rule — *a failed check never deactivates* — and your
requirement that offline must not permanently bypass validation are in direct tension. The
resolution: split them. **Licence** validation keeps the forgiving 21-day grace (a
photographer on location must never be locked out mid-wedding; that is a refund and a bad
story, not protected revenue). **Credits** get a short-TTL signed entitlement token (say
72 hours) plus reservations, because that is where money actually leaks. Two different
answers because the failure costs are asymmetric.

---

*Nothing in the repository was modified for this audit.*
