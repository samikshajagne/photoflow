"""
Identity labelling screen (Phase 2).

After analysis, PhotoFlow discovers people as face clusters. This panel shows
each cluster (a representative thumbnail + how many photos the person appears
in) and lets the photographer assign a role — Bride, Groom, Mother, Father,
Brother, Sister, Relative, Friend — and, for family members, a side
(bride/groom). Assigning a label calls
:meth:`~core.album.project.AlbumProject.label_cluster`, which propagates it to
every photo in the cluster, so a single choice labels all of that person's
shots.

The widget is a thin view over the already-tested data layer: it never invents
its own identity state, it just edits the :class:`AlbumProject`. Emit
:pyattr:`applied` when the photographer is done so the host can persist the
project and regenerate the (now person-aware) album.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.album.project import (
    AlbumProject,
    ROLE_BRIDE,
    ROLE_BROTHER,
    ROLE_FATHER,
    ROLE_FRIEND,
    ROLE_GROOM,
    ROLE_MOTHER,
    ROLE_RELATIVE,
    ROLE_SISTER,
    SIDE_BRIDE,
    SIDE_GROOM,
)
from ui_qt.workers.thumbnail_loader import ThumbnailLoader

_UNLABELED = "(unlabeled)"
_NO_SIDE = "—"

# Roles offered in the picker, in display order.
_ROLES = [
    ROLE_BRIDE,
    ROLE_GROOM,
    ROLE_MOTHER,
    ROLE_FATHER,
    ROLE_BROTHER,
    ROLE_SISTER,
    ROLE_RELATIVE,
    ROLE_FRIEND,
]
# Roles for which a bride/groom "side" applies.
_FAMILY_ROLES = {ROLE_MOTHER, ROLE_FATHER, ROLE_BROTHER, ROLE_SISTER, ROLE_RELATIVE}


class IdentityPanel(QWidget):
    """Review discovered people and label them; edits the AlbumProject in place."""

    labelsChanged = pyqtSignal()
    applied = pyqtSignal()

    def __init__(
        self,
        project: AlbumProject,
        thumbnail_loader: Optional[ThumbnailLoader] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("IdentityPanel")
        self._project = project
        self._loader = thumbnail_loader or ThumbnailLoader(edge=128, max_threads=2)
        # One image may preview several rows (a couple/group photo is the first
        # photo of more than one person), so map a path to *all* its labels.
        self._thumb_targets: dict[str, list[QLabel]] = {}
        self._loader.thumbnailReady.connect(self._on_thumb)

        root = QVBoxLayout(self)
        header = QLabel(
            "Label the people PhotoFlow found. A label applies to every photo of "
            "that person. Family members can have a side (bride / groom)."
        )
        header.setWordWrap(True)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self._rows = QVBoxLayout(body)
        self._rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._build_rows()

        apply_btn = QPushButton("Apply labels")
        apply_btn.clicked.connect(self.applied)
        root.addWidget(apply_btn, 0, Qt.AlignmentFlag.AlignRight)

    # ----------------------------------------------------------------- #
    # Public (also used by tests)
    # ----------------------------------------------------------------- #
    def apply_label(
        self, cluster_id: int, role: Optional[str], side: Optional[str] = None
    ) -> None:
        """Label a cluster (``role=None`` clears it) and notify listeners."""
        self._project.label_cluster(cluster_id, role, side)
        self.labelsChanged.emit()

    # ----------------------------------------------------------------- #
    # Rows
    # ----------------------------------------------------------------- #
    def _build_rows(self) -> None:
        clusters = self._project.clusters_for_review()
        if not clusters:
            self._rows.addWidget(QLabel("No people detected yet."))
            return
        # Give each person a distinct preview when possible: prefer a photo not
        # already used by another person, so e.g. bride and groom don't both
        # show the same couple shot.
        used: set[str] = set()
        for cluster in clusters:
            rep = self._distinct_representative(cluster, used)
            if rep:
                used.add(rep)
            self._rows.addWidget(self._make_row(cluster, rep))

    @staticmethod
    def _distinct_representative(cluster, used: set[str]) -> Optional[str]:
        for photo in cluster.photos:
            if photo not in used:
                return photo
        return cluster.representative or (cluster.photos[0] if cluster.photos else None)

    def _make_row(self, cluster, rep: Optional[str]) -> QFrame:
        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(row)

        thumb = QLabel()
        thumb.setFixedSize(96, 96)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setText("…")
        layout.addWidget(thumb)
        if rep:
            self._thumb_targets.setdefault(rep, []).append(thumb)
            self._loader.request(rep)

        info = QLabel(f"{cluster.size} photo(s)")
        info.setMinimumWidth(90)
        layout.addWidget(info)

        role_combo = QComboBox()
        role_combo.addItem(_UNLABELED)
        role_combo.addItems(_ROLES)
        if cluster.label in _ROLES:
            role_combo.setCurrentText(cluster.label)
        layout.addWidget(role_combo)

        side_combo = QComboBox()
        side_combo.addItems([_NO_SIDE, SIDE_BRIDE, SIDE_GROOM])
        if cluster.side in (SIDE_BRIDE, SIDE_GROOM):
            side_combo.setCurrentText(cluster.side)
        layout.addWidget(side_combo)
        layout.addStretch(1)

        def on_change(_=None, cid=cluster.cluster_id, rc=role_combo, sc=side_combo) -> None:
            role = None if rc.currentText() == _UNLABELED else rc.currentText()
            is_family = role in _FAMILY_ROLES
            sc.setEnabled(is_family)
            side = sc.currentText() if (is_family and sc.currentText() != _NO_SIDE) else None
            self.apply_label(cid, role, side)

        role_combo.currentTextChanged.connect(on_change)
        side_combo.currentTextChanged.connect(on_change)
        side_combo.setEnabled(role_combo.currentText() in _FAMILY_ROLES)
        return row

    def _on_thumb(self, path: str, image: QImage) -> None:
        targets = self._thumb_targets.get(path)
        if not targets or image.isNull():
            return
        pixmap = QPixmap.fromImage(image).scaled(
            96, 96,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        for target in targets:
            target.setPixmap(pixmap)

    def shutdown(self) -> None:
        self._loader.shutdown()
