# PhotoFlow

A local-first Windows desktop tool that triages wedding photo shoots
(1,000–5,000 images) into `BestShots` / `Duplicates` / `Blurry` /
`Review`, so photographers don't spend hours sorting manually.

## Overview

PhotoFlow scans a folder of photos, automatically detects duplicates and blurry images, calculates quality scores, detects faces, and organizes the images into categorized directories under a parent output folder (`PhotoFlow_Output`). 

---

## Project structure

```
photoflow/
├── main.py                     # CLI entry point — scan + organize a photo folder
├── core/
│   ├── pipeline.py             # End-to-end orchestration (scan→dup→blur→face→quality→organize)
│   ├── scanner.py              # Walks a folder and enumerates supported image files
│   ├── duplicate_detector.py   # Perceptual-hash duplicate detection
│   ├── blur_detector.py        # Variance-of-Laplacian blur scoring
│   ├── face_detector.py        # MediaPipe face detection (Solutions + Tasks API fallback)
│   ├── quality_scorer.py       # 0-100 quality score (sharpness, exposure, faces)
│   ├── organizer.py            # Copies photos into BestShots/Duplicates/Blurry/Review
│   ├── auto_edit.py            # Auto tone/colour corrections
│   ├── identity.py             # Per-person identity tracking across a shoot
│   ├── face_embedder.py        # Face embedding via InsightFace
│   ├── person_cluster.py       # Clusters embeddings into person identities
│   ├── timeline.py             # Shoot timeline segmentation
│   └── album/                  # Album layout and export pipeline
├── ui_qt/
│   ├── main.py                 # Desktop UI entry point (PyQt6)
│   ├── views/                  # Qt view modules (main window, gallery, etc.)
│   ├── workers/                # Background Qt worker threads
│   ├── models/                 # Qt data models
│   └── theme.py                # Dark theme applied to the Qt app
├── tools/
│   └── diagnose.py             # Diagnostic runner — full DEBUG log + env/model checks
├── utils/
│   ├── config.py               # Loads, merges, and validates configuration
│   └── logger.py               # Rotating-file + console logging setup
├── data/
│   ├── default_config.yaml     # Shipped default configuration values
│   └── models/                 # Optional bundled model files (e.g. blaze_face_short_range.tflite)
├── tests/                      # pytest unit tests
├── logs/                       # Runtime log output (gitignored except .gitkeep)
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # + pytest, for running the test suite
├── pytest.ini                  # Points pytest at the tests/ directory
└── .gitignore
```

---

## Running the Application

### Option 1: Desktop UI (Recommended)
To launch the desktop application, run:
```bash
python -m ui_qt.main
```
or:
```bash
python ui_qt/main.py
```
This starts the PhotoFlow PyQt6 desktop application, providing an intuitive interface for folder selection, scanning, and reviewing photos.

### Option 2: Command-Line Interface (CLI)
You can also run the pipeline directly from the command line over a folder of photos.

**Usage:**
```bash
python main.py PHOTO_FOLDER [--output DIR] [--dry-run] [--config PATH]
```

**Examples:**
- Preview what would happen without copying any files (Dry Run):
  ```bash
  python main.py "C:/Users/me/Pictures/Trip" --dry-run
  ```
- Scan and organize copies of photos into `<PHOTO_FOLDER>/PhotoFlow_Output`:
  ```bash
  python main.py "C:/Users/me/Pictures/Trip"
  ```
- Organize photos into a different destination using a custom configuration override:
  ```bash
  python main.py ./photos --output ./sorted --config ./my_config.yaml
  ```

---

## Debugging — Diagnostic Runner

`tools/diagnose.py` runs the album pipeline on a folder with **full DEBUG logging** captured to a single file (`logs/photoflow_debug.log`), overwriting it on each run so you always get one clean, shareable capture.

### What it captures

| Section | What you learn |
|---|---|
| **Environment** | Python version, OS, and whether `cv2 / mediapipe / insightface / onnxruntime / PyQt6` import, plus whether the MediaPipe face model and InsightFace `buffalo_l` are present on disk |
| **Full DEBUG trace** | Every stage: scan → duplicates → blur → faces → quality → identity → story → layout → export, including per-photo "no faces" debug lines explaining why face counts come back 0 |
| **Category distribution** | How many photos landed in each output folder |
| **Face-count distribution** | How many photos had 0 / 1 / 2 / … detected faces (or `unknown`) |
| **Sections & spreads** | Section names + photo counts, total spread count, manifest path |
| **SUCCESS / FAILED marker** | A clear final line; on failure the full traceback is included |

### Usage

**Debug an album run** (paste `logs/photoflow_debug.log` when reporting issues):
```powershell
python tools\diagnose.py "D:\path\to\your\photos"
```

**Environment-only check** (no photos needed — verifies all backends are installed):
```powershell
python tools\diagnose.py
```

**Capture a pytest run alongside:**
```powershell
python -m pytest -q > logs\pytest.log 2>&1
```

> **Tip:** The log file is always overwritten on each run — you'll never get mixed output from different sessions.

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

3. Expected result: all tests pass. Tests use `tmp_path` fixtures for any
   file I/O, so they never write into the real `logs/` directory or
   touch `data/default_config.yaml`.

---

## Setup

**Requirements:** Python 3.10 or newer, Windows/macOS/Linux.

1. Create and activate a virtual environment:

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

2. Install runtime dependencies:
   ```bash
   pip install -r requirements.txt
   ```
