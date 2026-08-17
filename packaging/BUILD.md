# Building the PhotoFlow installer

How to turn the source tree into the single `PhotoFlow-Setup.exe` a customer
downloads, double-clicks and runs.

**This has to be done on Windows.** PyInstaller doesn't cross-compile — a
Windows executable must be built on Windows.

---

## One-time setup

**1. Python and the project dependencies**

Use the same Python version you develop with (3.10+), in a virtualenv:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

**Install `requirements.txt`, not `requirements-extra.txt`.** The extras
(`insightface`, `rembg`, `onnxruntime`, `openai`) add hundreds of megabytes to
the installer, every feature they power has a working fallback, and
InsightFace's pretrained weights are licensed for non-commercial use only —
they must not ship in a paid build. Preflight warns if any of them are present.

**2. Inno Setup 6** — free, from <https://jrsoftware.org/isdl.php>.

Add its folder (typically `C:\Program Files (x86)\Inno Setup 6`) to your PATH so
`iscc` works from any prompt. The build script checks for it and tells you if
it's missing.

---

## Building

From the project root, with the virtualenv active:

```
packaging\build.bat
```

That runs four steps: **preflight checks**, the test suite, PyInstaller, then the
installer. It stops at the first failure — shipping a build that fails its own
tests isn't worth the minutes saved.

### Preflight

```
python packaging\preflight.py
```

Run this on its own any time. In about a second it verifies that every data file
the spec bundles actually exists, that every lazily-imported module is covered by
`hiddenimports`, that the version resource matches `utils/version.py`, that no
module writes to a path derived from `__file__` (see "Where the app writes
files"), that the entry point imports, and that the required packages are
installed. It also warns when `insightface`, `rembg`, `onnxruntime` or `openai`
are present, since none of them belong in a release build.

The point is feedback speed: these are exactly the mistakes that otherwise
surface only after a multi-minute build, or worse, on a customer's machine. It
generates `version_info.txt` if it's missing, so a fresh clone can build.

Results:

| Path | What it is |
| --- | --- |
| `dist\PhotoFlow\PhotoFlow.exe` | The frozen app (a folder, ~300–600 MB) |
| `packaging\output\PhotoFlow-Setup-0.9.0.exe` | The installer to publish |

To run the steps individually:

```
python packaging\make_version_info.py
pyinstaller packaging\photoflow.spec --noconfirm
iscc /DMyAppVersion=0.9.0 packaging\installer.iss
```

---

## Releasing a new version

1. Bump `__version__` in **`utils/version.py`** — that's the only place the
   version is written. The executable's file properties, the installer filename
   and the app's own reporting all derive from it.
2. Run `packaging\build.bat`.
3. Sign the installer (below).
4. Copy it to `website\downloads\PhotoFlow-Setup.exe`.
5. Update the version, size and date on `website\download.html`, and add an
   entry to `website\changelog.html`.

---

## Code signing — do this before you publish

An unsigned installer triggers Windows SmartScreen's "unrecognised app" warning.
A significant share of people stop there. This is the single highest-impact thing
you can do for conversion, and it costs money rather than effort.

| Certificate | Rough cost | Effect |
| --- | --- | --- |
| OV (organisation validated) | ₹15,000–35,000/year | Warning fades as your reputation builds |
| EV (extended validation) | ₹25,000–60,000/year | Trusted immediately, no reputation period |

You'll need company documents either way; EV certificates now generally require
the key to live on hardware or in a cloud HSM.

Once you have a certificate, sign **both** the app executable and the installer:

```
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
    /f mycert.pfx /p PASSWORD dist\PhotoFlow\PhotoFlow.exe

signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
    /f mycert.pfx /p PASSWORD packaging\output\PhotoFlow-Setup-0.9.0.exe
```

Always include a timestamp (`/tr`): without one, everything you signed stops
validating the day the certificate expires.

`signtool` comes with the Windows SDK.

---

## What gets bundled, and why

`photoflow.spec` explicitly includes:

- `data/models` — the MediaPipe face models, so face detection works offline
  from the first launch.
- `data/fonts`, `data/templates` — album and collage text rendering and layouts.
- `ui_qt/theme/dark.qss` — the stylesheet. `ui_qt/theme.py` loads it relative to
  its own file, which resolves inside the bundle once frozen.

Two deliberate choices worth keeping:

**One-folder, not one-file.** A `--onefile` build unpacks hundreds of megabytes
to a temp directory on *every launch*, making startup slow and upsetting
antivirus. The installer hides the folder from the customer anyway.

**UPX compression off.** UPX-packed Qt and OpenCV DLLs routinely trip Windows
Defender heuristics. A false-positive virus warning costs far more than the saved
megabytes.

---

## Where the app writes files

Important for a packaged build: an installed app **cannot write to its own
folder** (`C:\Program Files\...` is read-only for normal users). PhotoFlow
handles this in `utils/paths.py`:

| Purpose | Location |
| --- | --- |
| Read bundled resources | `utils.paths.resource_path()` — inside the bundle |
| Settings, collage presets | `%LOCALAPPDATA%\Samiksha Technologies\PhotoFlow` |
| Downloaded models | `...\PhotoFlow\cache\models` |
| Logs | `...\PhotoFlow\logs` |

> **That folder name is deliberate, not stale.** The company is now SA
> Innovations, but the data directory is pinned to the original name via
> `utils.version.LEGACY_DATA_DIR_NAME`. It holds `license.json` (the
> activation record) and the studio's saved collage presets, so renaming it
> would make every existing install appear unlicensed and lose its presets,
> silently. Changing it is a migration — copy the old directory across and
> verify it first — never a find-and-replace.

**If you add anything that saves a file, write it under
`utils.paths.user_data_dir()`.** Saving next to the application works perfectly
in development and then fails for every real customer — the exact bug class this
module exists to prevent.

The uninstaller deliberately leaves `%LOCALAPPDATA%` alone, so reinstalling
doesn't wipe a studio's saved presets.

---

## Troubleshooting

**`ModuleNotFoundError` at runtime but not in development** — a lazily-imported
module wasn't detected. Add it to `hiddenimports` in `photoflow.spec`.

**Missing file / stylesheet not applied when frozen** — the file isn't in
`datas`, or its destination doesn't mirror the source tree. Both matter.

**The build works but the app closes immediately** — temporarily set
`console=True` in the spec and run the exe from a terminal to see the traceback.

**The installer is enormous** — check whether `insightface`/`onnxruntime` are
installed in your build environment. They add hundreds of megabytes; if you're
shipping the permissively-licensed SFace backend instead, you don't need
InsightFace in the build venv at all.

**Antivirus flags the output** — confirm UPX is off, sign the binaries, and if
needed submit a false-positive report to the vendor.
