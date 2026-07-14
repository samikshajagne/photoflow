# Building & packaging PhotoFlow

PhotoFlow targets non-developer wedding photographers, so the goal is a
double-click Windows app. There are three ways to run/ship it, from least to
most packaged.

## 1. Run from source (developers)

```bash
python -m venv .venv
. .venv/Scripts/activate        # Windows;  use source .venv/bin/activate elsewhere
pip install -r requirements.txt
python -m ui_qt.main            # desktop app
python main.py PHOTO_FOLDER     # CLI
```

The identity / person-aware album features need `insightface` + `onnxruntime`
(both in `requirements.txt`) and a one-time model download on first use. If they
are missing the app still runs; the album just degrades to a time + quality
album.

## 2. Install as a package (entry points on PATH)

```bash
pip install .
photoflow        # launches the desktop app (gui entry point)
photoflow-cli    # the CLI
```

Defined in `pyproject.toml` under `[project.gui-scripts]` / `[project.scripts]`.

## 3. Frozen Windows executable (end users)

This is what you hand to a photographer — no Python required.

```bash
pip install -r requirements.txt pyinstaller
pyinstaller packaging/photoflow.spec
```

Output: `dist/PhotoFlow/PhotoFlow.exe` (a one-folder bundle). Zip the
`dist/PhotoFlow` folder to distribute.

Notes / gotchas:

- Build **on Windows** to produce a Windows binary (PyInstaller does not
  cross-compile).
- `mediapipe`, `insightface`, and `onnxruntime` ship data files and
  dynamically-imported submodules; the spec already collects them via
  `collect_data_files` / `collect_submodules`. If you hit a `FileNotFoundError`
  for a model graph at runtime, add that path to `datas` in
  `packaging/photoflow.spec`.
- The InsightFace recognition model (~300 MB) is downloaded at first run to the
  user's home cache. To ship fully offline, pre-download it and add the cache
  folder to `datas`.
- First launch of the frozen app is slow (it unpacks to a temp dir); subsequent
  launches are faster.

## Tests, lint, type-check

```bash
pip install -r requirements-dev.txt
pytest -q                 # full suite (Qt tests self-skip headless)
ruff check .              # lint
ruff format --check .     # formatting
mypy core utils ui_qt     # type-check
```

CI runs lint + tests on Python 3.10–3.12 (`.github/workflows/ci.yml`).
