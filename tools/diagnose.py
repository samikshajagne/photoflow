"""
PhotoFlow diagnostic runner.

Runs album generation on a folder with **full DEBUG logging** captured to a
single file (``logs/photoflow_debug.log``), plus an environment report (which
optional backends are installed, whether the face model is present) and a
face-count distribution. Share that one file to debug a run.

Usage (from the project root):

    python tools/diagnose.py "D:\\path\\to\\your\\photos"

Without a folder it just writes the environment report (handy for checking the
InsightFace / MediaPipe install). The log file is overwritten each run so it's
always a clean, single capture.
"""

from __future__ import annotations

import logging
import platform
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # make `core`, `utils`, ... importable

LOG_PATH = ROOT / "logs" / "photoflow_debug.log"
_FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"

# Optional/required modules whose presence matters for analysis + identity.
_MODULES = [
    "cv2",
    "numpy",
    "imagehash",
    "PIL",
    "mediapipe",
    "insightface",
    "onnxruntime",
    "PyQt6",
]


def _setup_logging() -> logging.Handler:
    """Capture everything (DEBUG) from both third-party and photoflow loggers."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_FMT, _DATE)

    file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(file_handler)
    root.addHandler(console)

    # Quiet very chatty third-party loggers so the log stays focused on
    # PhotoFlow's own actions (PIL logs every PNG chunk at DEBUG).
    for noisy in ("PIL", "matplotlib", "fontTools", "h5py"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # The photoflow logger sets propagate=False elsewhere; attach our handlers
    # directly so its DEBUG records are always captured (no duplicates because
    # propagate stays off and these are the same handler instances).
    pf = logging.getLogger("photoflow")
    pf.setLevel(logging.DEBUG)
    pf.propagate = False
    for h in list(pf.handlers):
        pf.removeHandler(h)
    pf.addHandler(file_handler)
    pf.addHandler(console)
    return file_handler


def _report_environment(log: logging.Logger) -> None:
    log.info("=== Environment ===")
    log.info("Python %s on %s", sys.version.split()[0], platform.platform())
    log.info("Project root: %s", ROOT)
    for name in _MODULES:
        try:
            mod = __import__(name)
            ver = getattr(mod, "__version__", "?")
            log.info("import %-12s OK (version %s)", name, ver)
        except Exception as exc:  # noqa: BLE001
            log.info("import %-12s MISSING (%s)", name, exc)

    try:
        import mediapipe as mp

        log.info("mediapipe.solutions present: %s", hasattr(mp, "solutions"))
    except Exception:  # noqa: BLE001
        pass

    model = ROOT / "data" / "models" / "blaze_face_short_range.tflite"
    log.info("MediaPipe face model present: %s (%s)", model.is_file(), model)
    insight = Path.home() / ".insightface" / "models" / "buffalo_l"
    log.info("InsightFace buffalo_l present: %s (%s)", insight.is_dir(), insight)


def _report_album(log: logging.Logger, folder: str) -> None:
    log.info("=== Album generation on: %s ===", folder)
    from core.album.orchestrator import AlbumOrchestrator

    project = AlbumOrchestrator().generate(folder)

    log.info("Photos analyzed: %d", len(project.photos))
    cats: dict[str, int] = {}
    faces: dict[int, int] = {}
    for rec in project.photos.values():
        cats[rec.category] = cats.get(rec.category, 0) + 1
        fc = rec.face_count if rec.face_count is not None else -1
        faces[fc] = faces.get(fc, 0) + 1
    log.info("Category distribution: %s", cats)
    log.info("Face-count distribution (faces->#photos, -1=unknown): %s", faces)
    log.info("People (clusters): %d (labelled: %d)",
             len(project.clusters), sum(1 for c in project.clusters if c.label))
    log.info("Sections: %s", [(s.name, len(s.photos)) for s in project.sections])
    log.info("Spreads: %d", len(project.spreads))
    log.info("Manifest: %s", project.export.manifest_path)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _setup_logging()
    log = logging.getLogger("photoflow.diagnose")
    log.info("########## PhotoFlow diagnostic run ##########")
    _report_environment(log)

    if not argv:
        log.warning("No folder provided; environment report only. "
                    "Run: python tools/diagnose.py <photo_folder>")
        log.info("########## done (log: %s) ##########", LOG_PATH)
        print(f"\nLog written to: {LOG_PATH}")
        return 2

    try:
        _report_album(log, argv[0])
        log.info("########## SUCCESS (log: %s) ##########", LOG_PATH)
        result = 0
    except Exception:  # noqa: BLE001 - capture the full traceback for sharing
        log.error("ALBUM GENERATION FAILED:\n%s", traceback.format_exc())
        log.info("########## FAILED (log: %s) ##########", LOG_PATH)
        result = 1

    print(f"\nLog written to: {LOG_PATH}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
