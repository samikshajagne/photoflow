# PyInstaller spec for PhotoFlow — builds a frozen Windows desktop app.
#
# Build from the project root:
#     pip install pyinstaller
#     pyinstaller packaging/photoflow.spec
# Output: dist/PhotoFlow/PhotoFlow.exe
#
# The heavy native deps (mediapipe, insightface, onnxruntime, opencv, PyQt6)
# ship data files and dynamically-imported submodules that PyInstaller does not
# discover automatically; we collect them explicitly below.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# --- Bundled data + model files -------------------------------------------- #
datas = [
    ("data/default_config.yaml", "data"),
    ("data/models", "data/models"),
]
# Package-shipped data (model graphs, label maps, etc.).
for pkg in ("mediapipe", "insightface", "onnxruntime"):
    datas += collect_data_files(pkg)

# --- Dynamically-imported submodules --------------------------------------- #
hiddenimports = []
for pkg in ("mediapipe", "insightface", "onnxruntime", "psd_tools"):
    hiddenimports += collect_submodules(pkg)

a = Analysis(
    ["ui_qt/main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["streamlit", "tkinter", "matplotlib"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI app: no console window
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PhotoFlow",
)
