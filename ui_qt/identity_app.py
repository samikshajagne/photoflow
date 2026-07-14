"""
Standalone launcher for the identity labelling screen.

Opens an existing album manifest, lets the photographer label the discovered
people, and saves the labels back on "Apply" — so a subsequent album
regeneration produces the person-aware sheets. This keeps labelling usable
without wiring identity into the full desktop app.

Usage:
    python -m ui_qt.identity_app <album_dir_or_manifest>

``<album_dir_or_manifest>`` is the folder that contains ``album_manifest.json``
(typically ``<shoot>/PhotoFlow_Album``), or the manifest file itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

from core.album.project import AlbumProject
from utils.logger import get_logger

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2

    target = Path(argv[0])
    project = AlbumProject.load(target)

    # Qt is imported here so the module is importable without a display.
    from PyQt6.QtWidgets import QApplication

    from ui_qt.views.identity_panel import IdentityPanel

    app = QApplication.instance() or QApplication([])
    panel = IdentityPanel(project)
    panel.setWindowTitle("PhotoFlow — Label People")
    panel.resize(640, 720)

    def _save() -> None:
        out = project.save(target)
        logger.info("Saved labels to %s", out)
        print(f"Saved labels to {out}")

    panel.applied.connect(_save)
    panel.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
