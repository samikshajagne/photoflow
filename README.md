<div align="center">

# PhotoFlow

**Three studio jobs. One desktop app.** 

Album generation, passport/ID photo sheets and a collage maker — running entirely
on your own computer.

*a product by [SA Innovations](https://sa-innovations.onrender.com)*

</div>

---

PhotoFlow is a local-first Windows desktop application for photo studios. It sorts
a whole wedding shoot, builds print-ready album spreads, produces passport and ID
photo sheets, and makes collages — without uploading a single photograph to
anyone's servers.

Free while in beta.

## The three tools

PhotoFlow opens by asking what you're doing today, and shows only the controls for
that job.

**Generate Album** — point it at a shoot. It scores every frame for sharpness and
exposure (measuring sharpness on the face when there is one, so a sharp subject
against a soft background isn't wrongly rejected), groups duplicates and bursts,
detects and clusters faces by person, then lays out print-ready spreads with
selectable themes and density.

**Passport & ID Photos** — face-aware automatic cropping to standard or custom
sizes, tiled print sheets with cutting-guide borders, **two or three different
people on one sheet** each with their own crop and copy count, and optional face
enhancement (skin smoothing, colour correction, background cleanup, teeth/eye
brightening) with hold-to-compare against the original.

**Collage Maker** — seven layouts including an aspect-following mosaic and
Pinterest-style masonry, six themes, shape collages (heart, star, or any number or
initials), gradient/image/blurred-photo backgrounds, titles and studio watermark,
per-photo filters, automatic best-photo selection from a folder, and print-safety
warnings when a photo is too low-resolution for the chosen output size.

## Design principles

- **Faces are never cut.** Every automatic crop checks where the faces are before
  deciding what to discard.
- **Runs locally.** Client photos stay on the studio's machine. It keeps working
  without an internet connection.
- **Nothing metered.** No per-photo charges, whatever the size of the shoot.
- **Automatic, never locked.** Every automated decision can be overridden.
- **Read bundled files, write user files.** Anything the app saves goes to the
  per-user data directory via `utils/paths.py` — never beside the application,
  which is read-only once installed.

## Running from source

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements-dev.txt
python -m ui_qt.main            # desktop application
python main.py PHOTO_FOLDER     # CLI: sort a folder only
```

### Dependencies are split three ways

| File | Contents |
| --- | --- |
| `requirements.txt` | Everything the app needs. This is what a release build installs. |
| `requirements-extra.txt` | Optional features, each with a working fallback. **Not for release builds.** |
| `requirements-dev.txt` | Runtime + pytest, ruff, mypy, PyInstaller. |

The extras are deliberately excluded from release builds: they add hundreds of
megabytes, and **InsightFace's pretrained weights are licensed for non-commercial
use only** (`core/sface_backend.py` is an Apache-2.0 alternative). The test suite
passes without them, which is how we know a release build works.

## Building the installer

Windows only — PyInstaller cannot cross-compile.

```bat
packaging\build.bat
```

That runs preflight checks, the test suite, PyInstaller and Inno Setup, and stops
at the first failure. Output: `packaging\output\PhotoFlow-Setup-<version>.exe`.

Run the checks alone at any time:

```bash
python packaging/preflight.py
```

Preflight verifies in about a second that every bundled data file exists, every
lazily-imported module is declared, the version resource matches
`utils/version.py`, no module writes to a path derived from `__file__`, and the
entry point imports — the mistakes that otherwise only surface after a full build
or on a customer's machine.

Full detail: [`packaging/BUILD.md`](packaging/BUILD.md).

## Tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests -q
```

1,300+ tests, all passing. Qt tests run offscreen and self-skip if PyQt6 is
unavailable. CI runs the suite on Python 3.10–3.12, plus preflight and the website
link checks, on every push.

## Project layout

```
photoflow/
├── core/                  Image processing and business logic (no UI imports)
│   ├── pipeline.py            scan → duplicates → blur → faces → quality → organize
│   ├── collage*.py            collage layouts, shapes, text, auto-build, presets
│   ├── passport_photo.py      ID photo crops and print sheets
│   ├── face_beautify.py       skin/colour/background/teeth enhancement
│   ├── licensing.py           trial, activation, offline grace period
│   ├── telemetry.py           opt-in aggregate usage counts
│   ├── diagnostics.py         support report (redacts photo paths and usernames)
│   └── album/                 album layout, templates, themes, rasterising, export
├── ui_qt/                 PyQt6 desktop UI (views, workers, theme)
├── utils/                 config, logging, paths, version
├── persistence/           analysis cache and identity store
├── packaging/             PyInstaller spec, Inno Setup script, build + preflight
├── website/               static marketing/download site (see website/README.md)
├── scripts/, tools/       developer utilities (never shipped)
├── data/                  bundled models, fonts, templates, default config
├── docs/                  plans and testing notes
└── tests/                 the test suite
```

## Documentation

| Document | What it covers |
| --- | --- |
| [`docs/SHIPPING_PLAN.md`](docs/SHIPPING_PLAN.md) | End-to-end distribution: hosting, updates, payment, costs |
| [`docs/PRODUCT_IDEA_CATALOGUE.md`](docs/PRODUCT_IDEA_CATALOGUE.md) | Feature ideas, competitive landscape, licensing blockers |
| [`docs/OWNER_DASHBOARD_PLAN.md`](docs/OWNER_DASHBOARD_PLAN.md) | Customer monitoring, what to track and what not to |
| [`packaging/BUILD.md`](packaging/BUILD.md) | Building and signing the installer |
| [`website/README.md`](website/README.md) | Deploying the site |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Code conventions |

## Licence

Proprietary. See [`LICENSE`](LICENSE) — an end-user licence agreement, not an
open-source licence. The application bundles open-source components (Qt, OpenCV,
NumPy, Pillow, MediaPipe, SciPy, psd-tools) under their own terms.

Support: `hello@samikshatech.com`
<!-- TODO (SA Innovations domain): update this address, LICENSE and
     utils/version.py::SUPPORT_EMAIL together once an SA Innovations
     domain and mailbox exist. -->
