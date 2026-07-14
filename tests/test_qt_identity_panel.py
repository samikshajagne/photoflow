"""
Offscreen test for the identity labelling panel. Skipped where PyQt6/native
libs can't load (same guard as the other Qt view tests).
"""

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.views.identity_panel import IdentityPanel
except Exception as exc:  # pragma: no cover - no Qt / native libs
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from core.album.project import (  # noqa: E402
    AlbumProject,
    PersonClusterRecord,
    PhotoRecord,
    ROLE_BRIDE,
    ROLE_MOTHER,
    SIDE_BRIDE,
)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _project() -> AlbumProject:
    project = AlbumProject.new("/shoot")
    project.add_photo(PhotoRecord(source_path="/shoot/a.jpg"))
    project.add_photo(PhotoRecord(source_path="/shoot/b.jpg"))
    project.clusters = [
        PersonClusterRecord(cluster_id=0, photos=["/shoot/a.jpg", "/shoot/b.jpg"], size=2,
                            centroid=[1.0, 0.0]),
        PersonClusterRecord(cluster_id=1, photos=["/shoot/b.jpg"], size=1, centroid=[0.0, 1.0]),
    ]
    return project


def test_apply_label_propagates_and_persists(qapp, tmp_path):
    project = _project()
    panel = IdentityPanel(project)

    panel.apply_label(0, ROLE_BRIDE)
    panel.apply_label(1, ROLE_MOTHER, SIDE_BRIDE)

    assert project.has_identity()
    # Bride token propagated to both of cluster 0's photos.
    assert ROLE_BRIDE in project.get("/shoot/a.jpg").persons
    assert ROLE_BRIDE in project.get("/shoot/b.jpg").persons

    # Labels survive save/reload.
    project.save(tmp_path)
    reloaded = AlbumProject.load(tmp_path)
    labels = {c.label for c in reloaded.clusters if c.label}
    assert {ROLE_BRIDE, ROLE_MOTHER} <= labels
    panel.shutdown()


def test_panel_with_no_clusters_builds(qapp):
    project = AlbumProject.new("/shoot")
    panel = IdentityPanel(project)  # must not raise
    assert panel is not None
    panel.shutdown()
