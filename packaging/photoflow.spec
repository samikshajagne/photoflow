# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for PhotoFlow — builds the frozen Windows desktop app.

Build from the project root (NOT from packaging/):

    pip install pyinstaller
    pyinstaller packaging/photoflow.spec --noconfirm

Output: dist/PhotoFlow/PhotoFlow.exe  (a one-folder build)

Why one-folder rather than one-file: a --onefile build unpacks several hundred
megabytes of OpenCV/ONNX/Qt to a temp directory on *every* launch, which makes
startup slow and confuses antivirus. The Inno Setup installer wraps this folder
into a single .exe for the customer anyway, so they never see the difference.

The heavy native dependencies (PyQt6, OpenCV, MediaPipe, ONNX Runtime) ship data
files and dynamically-imported submodules that PyInstaller cannot discover by
static analysis, so they're collected explicitly below.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# The spec's own directory isn't on sys.path, and SPECPATH points at it, so
# resolve the project root from there to import our version module.
ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))

# PyInstaller resolves every relative path in Analysis() -- the entry-point
# script in `scripts`, and each source in `datas` -- against the *.spec
# file's own directory (packaging/), via `CONF['spec']`, not against the
# process's current working directory. This is fixed PyInstaller behaviour,
# not something a working-directory change (e.g. build.bat's own `cd` to the
# project root before invoking pyinstaller) can influence -- which is exactly
# why the build failed looking for packaging\ui_qt\main.py instead of
# ui_qt\main.py. The fix is to make those paths absolute, anchored to ROOT,
# regardless of where `pyinstaller` was invoked from.
from utils.version import APP_NAME, __version__, version_tuple  # noqa: E402

block_cipher = None

# --------------------------------------------------------------------------- #
# Bundled data
# --------------------------------------------------------------------------- #
# (source, destination-inside-bundle). Destinations must mirror the source tree,
# because modules locate these files relative to their own __file__ (see
# utils.paths.resource_path) and that resolves inside the bundle once frozen.
datas = [
    ("data/default_config.yaml", "data"),
    ("data/models", "data/models"),          # MediaPipe .tflite face models
    ("data/fonts", "data/fonts"),            # album/collage text rendering
    ("data/templates", "data/templates"),    # album layout templates
    ("ui_qt/theme/dark.qss", "ui_qt/theme"), # loaded by ui_qt/theme.py at runtime
]

# Optional trees: only bundle them if the checkout actually has them, so the
# build doesn't fail on a fresh clone that hasn't fetched extras.
for optional in ("data/themes",):
    if (ROOT / optional).is_dir():
        datas.append((optional, optional))

# Package-shipped data (model graphs, label maps, native libs).
for pkg in ("mediapipe", "insightface", "onnxruntime", "rembg"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        # Optional dependency isn't installed in this build environment; the
        # corresponding feature degrades gracefully at runtime.
        pass

# Anchor every relative source to ROOT (see the note above `ROOT = ...`):
# PyInstaller resolves a relative `datas` source against packaging/, not the
# project root. The literal tuples above are kept relative and unchanged, since
# packaging/preflight.py's own parser scans this file's text for quoted string
# pairs to confirm what's bundled -- so the anchoring happens here instead,
# after that literal list is fully assembled. collect_data_files() above
# already returns absolute paths, so the isabs() check leaves those alone.
datas = [(src if os.path.isabs(src) else str(ROOT / src), dest) for src, dest in datas]

# --------------------------------------------------------------------------- #
# Dynamically-imported submodules
# --------------------------------------------------------------------------- #
hiddenimports = []

# PhotoFlow imports a great many of its own modules lazily (inside functions) to
# keep startup fast and heavy dependencies optional. PyInstaller finds imports by
# static analysis, so it misses every one of those. Rather than maintaining a
# hand-written list -- which drifted to ~38 missing entries and would drift again
# -- collect every first-party submodule. They're small pure-Python modules, so
# including them all costs almost nothing and removes a whole class of
# "ModuleNotFoundError only in the frozen build" failures.
for pkg in ("core", "ui_qt", "utils", "persistence"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass
for pkg in ("mediapipe", "insightface", "onnxruntime", "psd_tools", "scipy.spatial"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    [str(ROOT / "ui_qt" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Trim things we never use; each one meaningfully shrinks the build.
    excludes=[
        "streamlit", "tkinter", "matplotlib", "notebook", "IPython",
        "pytest", "PyQt6.QtWebEngineCore", "PySide6", "PyQt5",
    ],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is off deliberately: it frequently trips Windows Defender heuristics
    # on Qt/OpenCV DLLs, and a false-positive virus warning costs more
    # downloads than the saved megabytes are worth.
    upx=False,
    console=False,                       # GUI app: no console window
    icon=str(ROOT / "packaging" / "photoflow.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
