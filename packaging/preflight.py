#!/usr/bin/env python3
"""
Pre-build checks — catch packaging mistakes before PyInstaller runs.

    python packaging/preflight.py

Why this exists: the classic PyInstaller failure is a build that succeeds and
then crashes for the customer, because a data file wasn't bundled or a lazily
imported module wasn't detected. Those take a full build (several minutes) plus a
run on a clean machine to discover. This script checks the same assumptions in
about a second, so the slow feedback loop is only used for problems that
genuinely need it.

It is also the only meaningful validation available when you aren't on Windows,
since PyInstaller cannot cross-compile.

``build.bat`` runs this first. Exit code 0 means "nothing obviously wrong";
warnings don't fail the build, errors do.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

errors: list[str] = []
warnings: list[str] = []
notes: list[str] = []


def check(label: str, ok: bool, detail: str = "", fatal: bool = True) -> bool:
    mark = "OK  " if ok else ("FAIL" if fatal else "WARN")
    print(f"  [{mark}] {label}{(' — ' + detail) if detail and not ok else ''}")
    if not ok:
        (errors if fatal else warnings).append(f"{label}{': ' + detail if detail else ''}")
    return ok


# --------------------------------------------------------------------------- #
print("\n=== 1. Project layout ===")
# --------------------------------------------------------------------------- #
check("entry point ui_qt/main.py exists", (ROOT / "ui_qt" / "main.py").is_file())
check("spec file exists", (ROOT / "packaging" / "photoflow.spec").is_file())
check("installer script exists", (ROOT / "packaging" / "installer.iss").is_file())
check("app icon exists", (ROOT / "packaging" / "photoflow.ico").is_file())
check(
    "LICENSE exists (the installer displays it)",
    (ROOT / "LICENSE").is_file(),
    "installer.iss references ..\\LICENSE",
)

# --------------------------------------------------------------------------- #
print("\n=== 2. Version consistency ===")
# --------------------------------------------------------------------------- #
from utils.version import __version__, version_tuple  # noqa: E402

print(f"        version = {__version__}  ->  {version_tuple()}")

version_info = ROOT / "packaging" / "version_info.txt"
if not version_info.is_file():
    # Generated, and gitignored, so a fresh clone won't have it. Make it rather
    # than failing: the whole point is to remove avoidable build failures.
    print("  [..  ] version_info.txt missing — generating it")
    import subprocess

    subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "make_version_info.py")], check=False
    )
if check("version_info.txt exists", version_info.is_file()):
    text = version_info.read_text(encoding="utf-8")
    check(
        "version_info.txt matches utils/version.py",
        f"'{__version__}'" in text,
        f"regenerate it: python packaging/make_version_info.py",
    )

# The installer's default version should track the app, since iscc may be run
# by hand without build.bat passing /DMyAppVersion.
iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', iss)
if match:
    check(
        "installer.iss fallback version matches",
        match.group(1) == __version__,
        f"installer.iss says {match.group(1)}, app says {__version__}",
        fatal=False,
    )

# --------------------------------------------------------------------------- #
print("\n=== 3. Bundled data files (from the spec's `datas`) ===")
# --------------------------------------------------------------------------- #
# Parsed out of the spec rather than duplicated here, so this can't drift.
spec_text = (ROOT / "packaging" / "photoflow.spec").read_text(encoding="utf-8")
listed = re.findall(r'\(\s*"([^"]+)"\s*,\s*"[^"]*"\s*\)', spec_text)
for rel in listed:
    check(f"bundles {rel}", (ROOT / rel).exists())

# Things the app loads at runtime that MUST be bundled; a miss here is the
# "works in dev, broken once frozen" bug class.
must_bundle = {
    "ui_qt/theme/dark.qss": "the dark theme stylesheet (ui_qt/theme.py loads it)",
    "data/models": "MediaPipe face models",
    "data/fonts": "fonts for album/collage text",
    "data/default_config.yaml": "default configuration",
}
for rel, why in must_bundle.items():
    check(
        f"spec lists {rel}",
        any(rel == item or rel.startswith(item + "/") for item in listed),
        f"{why} — add it to `datas` in photoflow.spec",
    )

# --------------------------------------------------------------------------- #
print("\n=== 4. Runtime imports ===")
# --------------------------------------------------------------------------- #
required = [
    ("yaml", "PyYAML"),
    ("PIL", "Pillow"),
    ("numpy", "numpy"),
    ("cv2", "opencv-python-headless"),
    ("imagehash", "ImageHash"),
    ("mediapipe", "mediapipe"),
    ("PyQt6", "PyQt6"),
    # core/auth.py's imports -- both are real requirements (production login and
    # licensing depend on them), but neither is reached by "ui_qt.main imports
    # cleanly" below, because ui_qt.main only imports core.auth lazily, inside
    # _start_licensing(), which then swallows a missing-module error silently
    # (deliberately, so a licensing bug can never block startup). That means a
    # build venv missing these would pass every other check here and produce an
    # installer that launches fine and just has no login/licensing at all.
    ("keyring", "keyring"),
    ("requests", "requests"),
]
for module, package in required:
    check(
        f"import {module}",
        importlib.util.find_spec(module) is not None,
        f"pip install {package}",
    )

optional = [
    ("psd_tools", "psd-tools", "layered PSD album export"),
    ("scipy", "scipy", "agglomerative person clustering (falls back to greedy)"),
]
for module, package, feature in optional:
    if importlib.util.find_spec(module) is None:
        warnings.append(f"{package} missing — {feature} will be unavailable")
        print(f"  [WARN] import {module} — missing; {feature} unavailable")
    else:
        print(f"  [OK  ] import {module}")

# --------------------------------------------------------------------------- #
print("\n=== 5. Packages that must NOT be in a release build ===")
# --------------------------------------------------------------------------- #
# Each of these bloats the installer, and insightface additionally cannot be
# shipped commercially. Present = warning, because a dev machine legitimately
# has them; the point is to notice before building a release.
unwanted = {
    "insightface": (
        "pretrained weights are NON-COMMERCIAL — see docs/PRODUCT_IDEA_CATALOGUE.md §0.1"
    ),
    "rembg": "adds a large model runtime for one optional effect",
    "onnxruntime": "only needed by insightface/rembg; SFace uses OpenCV's own runtime",
    "openai": "cloud scene labelling is opt-in and has a local fallback",
}
for module, why in unwanted.items():
    if importlib.util.find_spec(module) is not None:
        warnings.append(f"{module} is installed in this environment — {why}")
        print(f"  [WARN] {module} present — {why}")
    else:
        print(f"  [OK  ] {module} absent")

# --------------------------------------------------------------------------- #
print("\n=== 6. Lazily-imported modules the spec must declare ===")
# --------------------------------------------------------------------------- #
# PyInstaller finds imports by static analysis, so anything imported inside a
# function is invisible to it and has to be listed in `hiddenimports`.
# The spec collects whole first-party packages via collect_submodules rather
# than naming modules individually, so treat a collected package as covering
# everything beneath it.
collected_pkgs = set()
for match in re.finditer(r"for pkg in \(([^)]*)\):", spec_text):
    collected_pkgs.update(re.findall(r'"([^"]+)"', match.group(1)))
hidden = re.search(r"hiddenimports\s*=\s*\[(.*?)\]", spec_text, re.S)
declared = set(re.findall(r'"([^"]+)"', hidden.group(1))) if hidden else set()


def _is_covered(module: str) -> bool:
    if module in declared:
        return True
    root_pkg = module.split(".")[0]
    return root_pkg in collected_pkgs

lazy_modules = set()
# Only first-party directories: globbing the whole tree would walk .venv, which
# holds tens of thousands of files and takes minutes.
first_party = ("core", "ui_qt", "utils", "persistence")
sources = [p for folder in first_party for p in (ROOT / folder).glob("**/*.py")]
for path in sources:
    if "__pycache__" in path.parts:
        continue
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        # An import indented inside a function/method = lazy.
        m = re.match(r"\s+from (core[\w.]+|utils[\w.]+|ui_qt[\w.]+) import", line)
        if m:
            lazy_modules.add(m.group(1))

missing_hidden = sorted(m for m in lazy_modules if not _is_covered(m))
if missing_hidden:
    for module in missing_hidden:
        warnings.append(f"lazily imported {module} is not in the spec's hiddenimports")
        print(f"  [WARN] {module} imported lazily but not declared")
    print("        (add these to `hiddenimports` in photoflow.spec if the build"
          " crashes with ModuleNotFoundError)")
else:
    print(f"  [OK  ] all {len(lazy_modules)} lazily-imported first-party modules declared")

# --------------------------------------------------------------------------- #
print("\n=== 7. Writable-path discipline ===")
# --------------------------------------------------------------------------- #
# An installed app cannot write beside itself (Program Files is read-only), so
# any module saving files must go through utils.paths. This has already been the
# cause of three real bugs (presets, model downloads, logs).
offenders = []
for path in (ROOT / "core").glob("**/*.py"):
    text = path.read_text(encoding="utf-8", errors="ignore")
    builds_own_data_path = re.search(
        r'Path\(__file__\)[^\n]*parent[^\n]*/\s*"data"', text
    )
    if not builds_own_data_path:
        continue
    # Reading a bundled file that way is fine (it resolves inside the frozen
    # bundle). Only *writing* is the bug, so require evidence of a write before
    # complaining -- a check that flags harmless reads gets ignored.
    writes = re.search(
        r"\.mkdir\(|\.write_text\(|\.write_bytes\(|open\([^)]*[\"']w|\.replace\(|urlretrieve",
        text,
    )
    if writes:
        offenders.append(path.relative_to(ROOT))
check(
    "no module writes to a data path derived from __file__",
    not offenders,
    f"{', '.join(str(p) for p in offenders)} — write via utils.paths instead",
    fatal=False,
)

from utils.paths import bundle_root, user_data_dir  # noqa: E402

check(
    "user data directory is outside the application directory",
    not user_data_dir().is_relative_to(bundle_root()),
    f"{user_data_dir()} is inside {bundle_root()}",
)

# --------------------------------------------------------------------------- #
print("\n=== 7b. Build secrets ===")
# --------------------------------------------------------------------------- #
from core.licensing import using_placeholder_key  # noqa: E402

if using_placeholder_key():
    warnings.append(
        "licence state is signed with the PLACEHOLDER key — fine for the free "
        "beta, but run `python packaging/make_secrets.py` before selling"
    )
    print("  [WARN] using the placeholder signing key (fine for beta, not for sale)")
else:
    print("  [OK  ] a real signing key is configured")
check(
    "utils/_secrets.py is gitignored",
    "utils/_secrets.py" in (ROOT / ".gitignore").read_text(encoding="utf-8"),
    "a committed signing key stays in git history forever",
)

# --------------------------------------------------------------------------- #
print("\n=== 8. Entry point actually imports ===")
# --------------------------------------------------------------------------- #
import os  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import ui_qt.main  # noqa: F401

    print("  [OK  ] ui_qt.main imports cleanly")
except Exception as exc:  # noqa: BLE001
    check("ui_qt.main imports cleanly", False, f"{type(exc).__name__}: {exc}")

# --------------------------------------------------------------------------- #
print("\n" + "=" * 68)
if errors:
    print(f"{len(errors)} ERROR(S) — fix these before building:")
    for item in errors:
        print(f"  x {item}")
if warnings:
    print(f"\n{len(warnings)} warning(s) — review, but not blocking:")
    for item in warnings:
        print(f"  ! {item}")
if not errors and not warnings:
    print("All preflight checks passed. Ready to build.")
elif not errors:
    print("\nNo blocking problems. Ready to build.")
print("=" * 68 + "\n")

raise SystemExit(1 if errors else 0)
