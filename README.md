# PhotoFlow

A local-first Windows desktop tool that triages wedding photo shoots
(1,000–5,000 images) into `BestShots` / `Duplicates` / `Blurry` /
`Review`, so photographers don't spend hours sorting manually.

## ⚠️ Milestone 1 scope (this codebase)

This milestone is **setup only**, per the agreed roadmap:

**In scope:**
- Project scaffold (final folder structure, with empty packages where
  future milestones will add code)
- Configuration system: load defaults, merge an optional user override,
  validate everything into typed objects
- Logging system: rotating file log + console output
- A `main.py` that proves the above two work together
- Unit tests for the config and logging modules

**Explicitly NOT in scope yet** (later milestones):
- Scanning a photo folder
- Any image analysis (blur, faces, duplicates, quality score)
- Classifying or moving/copying any files
- The Streamlit UI
- The SQLite result cache

If you run `main.py`, it will load config, set up logging, print a
startup confirmation, and exit. It does not touch any photos.

---

## Project structure

```
photoflow/
├── main.py                     # Entry point for this milestone (see below)
├── core/
│   └── __init__.py             # Empty placeholder — pipeline modules land here in Milestone 2
├── persistence/
│   └── __init__.py             # Empty placeholder — SQLite cache lands here in Milestone 2/3
├── ui/
│   ├── __init__.py             # Empty placeholder — Streamlit app lands here in a later milestone
│   └── components/
│       └── __init__.py         # Empty placeholder — reusable widgets (gallery, progress bar, etc.)
├── utils/
│   ├── __init__.py             # Package marker + one-line description
│   ├── config.py               # Loads, merges, and validates configuration
│   └── logger.py                # Sets up rotating-file + console logging
├── data/
│   └── default_config.yaml     # Shipped default configuration values
├── tests/
│   ├── __init__.py             # Package marker (lets pytest resolve `from utils...` imports)
│   ├── test_config.py          # Unit tests for utils/config.py
│   └── test_logger.py          # Unit tests for utils/logger.py
├── logs/
│   └── .gitkeep                 # Keeps the (otherwise empty) logs/ folder in the repo
├── requirements.txt             # Runtime dependencies for *this* milestone only
├── requirements-dev.txt         # + pytest, for running the test suite
├── pytest.ini                    # Tells pytest where to find tests
└── .gitignore
```

### File-by-file explanation

| File | What it does |
|---|---|
| `main.py` | The only executable entry point right now. Parses an optional `--config` CLI flag, loads/validates configuration, sets up logging, and logs a handful of confirmation messages. This is intentionally the *full* extent of the program's behavior in Milestone 1 — it's a smoke test for the scaffold, not a pipeline runner. |
| `utils/config.py` | Defines the configuration schema as frozen (immutable) dataclasses (`IOConfig`, `LoggingConfig`, `ThresholdsConfig`, `ScoringWeightsConfig`, `PerformanceConfig`, wrapped in `AppConfig`). `load_config()` reads `data/default_config.yaml`, optionally deep-merges a user override file on top, and validates everything (required sections/keys present, correct types, sane values like weights summing to 1.0). Raises a clear `ConfigError` on any problem. Note: `ThresholdsConfig`, `ScoringWeightsConfig`, and `PerformanceConfig` are defined and validated now, but nothing reads them yet — they exist so the schema is locked in before Milestone 2 needs it. |
| `utils/logger.py` | `setup_logging()` configures the `photoflow` logger with a `RotatingFileHandler` (writes to `logs/photoflow.log`, rotates by size) and a console `StreamHandler`, sharing one formatter. It's idempotent — calling it twice replaces handlers instead of duplicating them. `get_logger(name)` is what every other module will call (e.g. `get_logger(__name__)`) to get a properly namespaced child logger that inherits these handlers. |
| `data/default_config.yaml` | The actual default values: supported file extensions, output folder naming, copy-vs-move behavior, logging level/rotation settings, and the not-yet-used analysis thresholds/weights/performance settings reserved for later milestones. |
| `core/__init__.py`, `persistence/__init__.py`, `ui/__init__.py`, `ui/components/__init__.py` | Empty packages with a docstring explaining what will live there. They exist now purely so the folder structure is visible and stable; no logic is implemented inside them. |
| `tests/test_config.py` | Covers: default config loads cleanly; an override file correctly merges on top of (not replaces) the defaults; missing/malformed override files raise `ConfigError`; semantic validation rules (bad log level, weights not summing to 1, extensions missing a leading dot, negative worker pool size) all raise `ConfigError`; `worker_pool_size: null` is accepted as valid (means "auto"). |
| `tests/test_logger.py` | Covers: `setup_logging()` creates the log directory and a working log file; calling it twice doesn't create duplicate handlers; `get_logger()` produces correctly namespaced logger names; the configured log level is actually respected (e.g. `INFO` messages are suppressed when level is `WARNING`). |
| `requirements.txt` | Just `PyYAML` — the only third-party dependency this milestone's code actually imports. OpenCV/Pillow/NumPy/ImageHash/MediaPipe/Streamlit are deliberately *not* listed yet; see the comment in the file. |
| `requirements-dev.txt` | `requirements.txt` plus `pytest`, for anyone running the test suite. |
| `pytest.ini` | Points pytest at the `tests/` directory. |
| `.gitignore` | Standard Python ignores, plus the runtime-generated `logs/*.log` and a future `data/cache.db`. |

---

## Setup instructions

**Requirements:** Python 3.10 or newer (Windows, macOS, or Linux — nothing
in this milestone is platform-specific yet).

1. Clone/copy the project, then from the `photoflow/` directory create a
   virtual environment:

   **Windows (PowerShell):**
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   **macOS/Linux:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run it:

   ```bash
   python main.py
   ```

   You should see console output similar to:

   ```
   2026-06-18 10:00:00 | INFO     | photoflow.main | PhotoFlow scaffold initialized successfully (Milestone 1).
   2026-06-18 10:00:00 | INFO     | photoflow.main | Supported file types: .jpg, .jpeg, .png, .tif, .tiff | Output folder name: PhotoFlow_Output | File mode: copy
   2026-06-18 10:00:00 | INFO     | photoflow.main | Log files are being written to: /path/to/photoflow/logs
   2026-06-18 10:00:00 | INFO     | photoflow.main | No image scanning or analysis is implemented yet — that begins in Milestone 2.
   ```

   The same lines will also be appended to `logs/photoflow.log`.

4. (Optional) Run with a custom config override, e.g. to bump the log
   level to `DEBUG`:

   ```bash
   echo "logging:`n  level: DEBUG" > my_override.yaml   # PowerShell
   python main.py --config my_override.yaml
   ```

---

## Test instructions

1. Install the dev dependencies (includes `pytest` on top of the base
   requirements):

   ```bash
   pip install -r requirements-dev.txt
   ```

2. Run the full test suite from the `photoflow/` directory:

   ```bash
   pytest
   ```

   For more detail on each test:

   ```bash
   pytest -v
   ```

3. Expected result: all tests in `tests/test_config.py` and
   `tests/test_logger.py` pass. Tests use `tmp_path` fixtures for any
   file I/O, so they never write into the real `logs/` directory or
   touch `data/default_config.yaml`.

---

## What happens next (Milestone 2 preview, not implemented here)

Milestone 2 will fill in `core/scanner.py`, `core/preprocessor.py`,
`core/blur_detector.py`, `core/face_detector.py`,
`core/duplicate_detector.py`, `core/quality_scorer.py`,
`core/classifier.py`, `core/organizer.py`, and `core/pipeline.py`, wired
together end-to-end on a small sample folder via the CLI — no caching,
multiprocessing, or UI yet. This document will be updated alongside it.
