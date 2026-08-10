# Getting PhotoFlow to customers — end to end

*Written 2026-08-05. Everything between "it runs on my machine" and "a photographer
in another city downloaded it, installed it, and gets updates" — on a budget of
**₹0/month**, with a path that still works if this becomes popular.*

---

## 0. Where you actually stand

| Piece | Status |
| --- | --- |
| The application | Works. 1333 tests passing. |
| Build scripts (PyInstaller + Inno Setup) | Written (`packaging/`) — **never run yet** |
| The installer itself | **Does not exist** |
| Website | Built (`website/`), 7 pages, not deployed |
| Licensing / trial / grace | Implemented, tested |
| Update mechanism | **Not built** |
| Payment | **Not chosen** |
| Repo hygiene for release | **Needs a cleanup pass** (§1) |

Two things block a first download: **you have never produced a build**, and
**nothing is hosted**. Both are a day's work, not a project.

---

## 1. Cleanup before the first build

Findings from an actual survey of the repo, not generic advice:

**Delete — dead code.** `ui/` is a leftover from the Streamlit era: it contains
only `__init__.py` and `components/folder_utils.py`, plus a `__pycache__` holding
`app.cpython-*.pyc` for an `app.py` that no longer exists. **Nothing imports it**
(verified). Delete the folder.

**Keep in the repo, but they never ship.** `scripts/benchmark_embedders.py`,
`tools/diagnose.py`, `website/check_site.py`, `tests/`. PyInstaller only bundles
what `photoflow.spec` lists in `datas`, so none of these reach a customer. No
action needed beyond not adding them to the spec.

**Tidy the root.** Thirteen markdown/txt files sit at the top level
(`PHASE_3_4_*` ×3, `SESSION2_VERIFICATION.md`, `DEMO_ONE_PAGER.txt`,
`implementation_plan.md`, `API_vs_LOCAL_ANALYSIS.md`, …). They're useful history;
move them to `docs/history/` so the root shows only `README`, `CONTRIBUTING`,
`ROADMAP`, `LICENSE`, `requirements*.txt`, `pyproject.toml`. Cosmetic, but the
root is the first thing a future collaborator (or you in six months) reads.

**Split requirements.** `requirements.txt` currently pulls in everything,
including three things you don't want in a shipping build:

- **`insightface`** — its pretrained weights are **non-commercial**. Do not
  install it in the build environment. `core/sface_backend.py` (Apache-2.0)
  covers recognition; see `PRODUCT_IDEA_CATALOGUE.md` §0.1.
- **`rembg`** — hundreds of MB of extra model runtime for one optional effect.
- **`openai`** — only needed for cloud scene labelling.

Suggested split:

```
requirements.txt        # runtime, minimal: PyYAML Pillow numpy opencv-python-headless
                        #   mediapipe PyQt6 psd-tools ImageHash python-dotenv
requirements-extra.txt  # optional: rembg openai insightface  (NOT for release builds)
requirements-dev.txt    # -r requirements.txt + pytest ruff mypy
```

The smaller the build venv, the smaller the installer, and installer size is a
real conversion factor.

**Add to `.gitignore`:** `__pycache__/`, `dist/`, `build/`, `packaging/output/`.
(`.venv/` and `logs/*.log` are already covered.)

**Two writable-path bugs found and fixed today** — worth knowing because the same
mistake is easy to repeat:

1. Collage presets and downloaded models were saved *next to the application*.
   Fine in development, impossible once installed (Program Files is read-only).
2. `log_dir: "logs"` in `default_config.yaml` is relative, so for a desktop
   shortcut it resolved against whatever the working directory happened to be.

Both now route through `utils/paths.py`. **The rule: read with
`resource_path()`, write with `user_data_dir()`.** Anything you add that saves a
file must follow it.

---

## 2. Producing the installer

Once, on a Windows machine:

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
packaging\build.bat
```

That runs the version resource → the test suite → PyInstaller → Inno Setup, and
aborts if tests fail. Output: `packaging\output\PhotoFlow-Setup-0.9.0.exe`.

Full detail, including troubleshooting, is in `packaging/BUILD.md`.

**Expect the first build to fail once or twice.** The usual cause is a lazily
imported module PyInstaller didn't detect — you'll see `ModuleNotFoundError` at
runtime but not in development. Add it to `hiddenimports` in the spec and rebuild.
Budget an afternoon for the first one, ten minutes for every one after.

**Test the build on a machine that has never had Python installed.** This is the
single most valuable test you can run. A build that works on your dev box and
fails on a clean Windows install is the classic packaging failure, and you cannot
detect it on your own machine.

---

## 3. Hosting, for free

### The website → **Cloudflare Pages**

Free, unlimited bandwidth, global CDN, custom domain with automatic HTTPS. For a
static site with no build step it is simply the best free option.

Netlify (100 GB/month) and GitHub Pages (100 GB soft limit) are also fine.
**Vercel works but is a worse fit here** — its free tier is aimed at Node/Next.js
apps, has a 100 GB/month bandwidth cap, and its terms restrict commercial use on
the Hobby plan. **Streamlit Cloud is the wrong tool entirely** — it hosts Python
data apps, not static marketing sites, and it sleeps when idle.

Setup: push the repo to GitHub → Cloudflare Pages → connect repo → build command
*empty*, output directory `website` → done. Every `git push` redeploys.

### The installer → **GitHub Releases**, not your web host

This matters. Do **not** put a 300–600 MB installer in the website repo:

- Cloudflare Pages caps individual files at ~25 MB.
- Git repositories handle large binaries terribly; every version bloats history
  permanently.

GitHub Releases is built exactly for this: free, no bandwidth limit for public
repos, 2 GB per file, and it keeps every past version. Your download button
becomes a link to the release asset, or better, to
`https://github.com/<you>/photoflow/releases/latest/download/PhotoFlow-Setup.exe`
— a permanent URL that always serves the newest build.

Update `website/download.html` to point there instead of the local placeholder,
and `website/downloads/README.md` becomes unnecessary.

### The domain

The only thing worth paying for. A `.com` is roughly ₹1,000–1,500/year; `.in`
similar. Cloudflare Registrar sells at cost. Until you buy one,
`photoflow.pages.dev` works fine and costs nothing.

### Running total

| Item | Cost |
| --- | --- |
| Website hosting | ₹0 |
| Installer hosting | ₹0 |
| Domain | ~₹1,200/year (optional at first) |
| **Total** | **₹0–100/month** |

---

## 4. Updates

Free and simple, in three parts:

**1. A manifest on the website** — `website/version.json`:

```json
{
  "version": "0.9.1",
  "released": "2026-08-20",
  "url": "https://github.com/<you>/photoflow/releases/latest/download/PhotoFlow-Setup.exe",
  "notes_url": "https://photoflow.pages.dev/changelog.html",
  "critical": false
}
```

**2. An in-app check.** On startup, fetch that file, compare against
`utils.version.__version__`, and if it's newer show a quiet, dismissible banner:
*"PhotoFlow 0.9.1 is available — what's new / Download / Not now."* Rules that
matter: check at most once a day, never block startup, never auto-download, and
fail silently when offline. A studio mid-wedding must never be interrupted by an
update prompt.

**3. Installing over the top.** The Inno Setup script already handles this: the
stable `AppId` means a new installer upgrades in place rather than installing a
second copy, and uninstall deliberately leaves `%LOCALAPPDATA%` alone so presets
and licences survive. **Never change that `AppId`.**

Silent auto-update (download and install unattended) is deliberately *not* the
plan yet. It needs code signing to be safe, and a bad auto-update pushed to every
customer at once is the worst failure mode a small vendor can have. Manual
updates are fine at this scale.

---

## 5. Payment and licences

You already have the client side (`core/licensing.py`): 14-day trial, activation,
21-day offline grace.

**Phase 1 — free beta, no payment (now).** Nothing to build. The trial and the
"free while in beta" badge are already consistent with the site.

**Phase 2 — first paid customers (manual, ₹0 infrastructure).** Take payment by
UPI or bank transfer, generate a key by hand, email it. `OfflineBackend` accepts
any well-formed key today. **Ten customers is entirely manageable this way**, and
doing it manually teaches you what the automated version actually needs. Track
them in a spreadsheet.

**Phase 3 — automate (when manual hurts).** Point `HttpBackend` at
**Lemon Squeezy** or **Paddle**. Both act as merchant of record: they handle
Indian GST, international cards, invoices and refunds, and both issue licence
keys. That combination is worth far more to you than a nicer licensing API,
because taking money legally across borders is the genuinely hard part. Roughly
5% + transaction fee, charged only when you earn.

Do not build your own licence server until a provider is actually costing you
more than it saves.

---

## 6. Support, on ₹0

- A real inbox on your domain (Zoho Mail's free tier handles this) instead of the
  placeholder `hello@samikshatech.com` that appears twice in `contact.html`.
- A **"Copy diagnostics" button** in the app that copies version, OS and the last
  chunk of the log to the clipboard. It turns "it crashed" into a useful report
  and costs an hour to build.
- GitHub Issues, if you make the repo public — free tracker with no setup.
- Answer fast while you have few customers. Early responsiveness is the main
  advantage you have over Aftershoot, and it buys word of mouth.

---

## 7. Release checklist (repeat every version)

1. Bump `__version__` in `utils/version.py` — the only place it lives.
2. `packaging\build.bat` (tests run automatically; it aborts on failure).
3. Sign the installer if you have a certificate (§9).
4. Install it on a clean Windows machine and click through all three tools.
5. Create a GitHub Release, tag `v0.9.1`, attach the installer.
6. Update `website/version.json` and add a `changelog.html` entry.
7. Push — Cloudflare redeploys automatically.
8. Email existing customers if the release matters to them.

Steps 1–2 are automated. The rest is about twenty minutes.

---

## 8. What changes as you grow

| Customers | What breaks first | Fix |
| --- | --- | --- |
| 1–10 | Nothing. Manual keys, manual support. | — |
| 10–50 | Issuing keys and chasing payments by hand | Lemon Squeezy / Paddle (§5) |
| 50–200 | You can't tell who's still active | Licence server + dashboard (`OWNER_DASHBOARD_PLAN.md`) |
| 200–1000 | Support volume, repeat questions | Docs site, in-app help, a real tracker |
| 1000+ | Manual releases, unclear priorities | CI builds on tag, consented telemetry driving the roadmap |

Nothing in the current design has to be thrown away to get to the bottom row.
The licence backend is a pluggable protocol, telemetry is already opt-in and
aggregate, and the site is static so it scales for free.

---

## 9. The things most likely to hurt you

**Code signing.** The biggest single conversion problem. An unsigned installer
shows Windows SmartScreen's "unrecognised app" warning and a large share of
people stop there. OV certificate ≈ ₹15–35k/year, EV ≈ ₹25–60k/year and clears
the warning immediately. Until you can afford one, the Download and Support pages
already explain how to get past the warning — but treat this as the first thing
you buy after the domain.

**Installer size.** 300–600 MB is a lot on a patchy connection. Trimming the
build venv (§1) is the cheapest win. Bundle only the small models and download
the large optional ones on demand.

**Antivirus false positives.** PyInstaller output gets flagged sometimes. UPX is
already off in the spec for exactly this reason. If it happens, submit a
false-positive report to the vendor; signing helps here too.

**The clean-machine test.** Say it again: build on your machine, test on one that
has never had Python. Everything else on this list is recoverable; shipping an
installer that can't start is not.

**Windows-only.** Fine — that's what your customers use. The download page already
invites macOS/Linux requests so demand tells you when it's worth doing.

---

## 10. Do this, in this order

**This week**
1. Delete `ui/`, split requirements, move root docs to `docs/history/`, extend
   `.gitignore`.
2. Run `packaging\build.bat` and get a working installer.
3. Test it on a clean Windows machine.

**Next week**
4. Push to GitHub; deploy `website/` on Cloudflare Pages.
5. Create the first GitHub Release and point the download button at it.
6. Set up a real support inbox.

**The week after**
7. Add `version.json` and the in-app update check.
8. Give it to two or three studios you know and watch them install it.

**When money starts moving**
9. Buy the domain, then a code-signing certificate.
10. Move licensing to Lemon Squeezy or Paddle.

The only genuine blockers are steps 2 and 3. Everything after that is
distribution, and distribution is free.
