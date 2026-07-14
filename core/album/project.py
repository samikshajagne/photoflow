"""
The canonical AlbumProject document for PhotoFlow (Phase 1).

``AlbumProject`` is the **single source of truth** for an album: every stage
(analysis, auto-edit, story, layout, export) and every future module
(identity, event classification, UI) reads from and writes to this one
structure. No module should invent its own parallel project state.

It is a plain, JSON-serializable document so it can be persisted as
``album_manifest.json`` and reloaded to resume work (which is also how manual
overrides survive a re-run). It holds:

- ``meta``      — project metadata (source folder, timestamps, album spec).
- ``photos``    — the photo inventory: one :class:`PhotoRecord` per analyzed
                  image, carrying its category, quality metrics, tier,
                  detected face info, capture time, persons (empty until the
                  identity phase), and its auto-edit recipe.
- ``events``    — timeline/event classifications (chronological segments).
- ``sections``  — the story: ordered album sections and their photos.
- ``spreads``   — laid-out spreads (frame + crop per placed photo).
- ``overrides`` — sticky manual category overrides (path -> category).
- ``export``    — export metadata (manifest path, retouch flags).

This module has no Qt and no heavy deps; it is pure data + (de)serialization.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)

PathLike = Union[str, Path]

SCHEMA_VERSION = 1
MANIFEST_FILENAME = "album_manifest.json"

# Quality tiers (finer than the four folders). The BestShots boundary mirrors
# the pipeline's BEST_SHOTS_QUALITY_FLOOR so the two never drift.
TIER_HERO = "Hero"        # 90-100
TIER_BEST = "BestShots"   # 75-89
TIER_REVIEW = "Review"    # 60-74
TIER_LOW = "Low"          # <60
_TIER_HERO_MIN = 90.0
_TIER_BEST_MIN = 75.0
_TIER_REVIEW_MIN = 60.0


def quality_tier(score: Optional[float]) -> Optional[str]:
    """Map a 0-100 quality score to its tier (``None`` if unscored)."""
    if score is None:
        return None
    if score >= _TIER_HERO_MIN:
        return TIER_HERO
    if score >= _TIER_BEST_MIN:
        return TIER_BEST
    if score >= _TIER_REVIEW_MIN:
        return TIER_REVIEW
    return TIER_LOW


# --- Identity roles (Phase 2) ---------------------------------------------- #
# A cluster of faces (one person) is labelled with a role; family members may
# also carry a "side" (whose family). Labels are free strings, but these are
# the canonical ones the labelling UI offers.
ROLE_BRIDE = "bride"
ROLE_GROOM = "groom"
ROLE_MOTHER = "mother"
ROLE_FATHER = "father"
ROLE_BROTHER = "brother"
ROLE_SISTER = "sister"
ROLE_RELATIVE = "relative"
ROLE_FRIEND = "friend"

SIDE_BRIDE = "bride"
SIDE_GROOM = "groom"

PRINCIPAL_ROLES = frozenset({ROLE_BRIDE, ROLE_GROOM})
_IMMEDIATE_FAMILY = frozenset({ROLE_MOTHER, ROLE_FATHER, ROLE_BROTHER, ROLE_SISTER})
_FAMILY_ROLES = _IMMEDIATE_FAMILY | {ROLE_RELATIVE}

# Presence tokens used by the story builder's section rules.
TOKEN_FAMILY_BRIDE = "family_bride"
TOKEN_FAMILY_GROOM = "family_groom"
TOKEN_CLOSE_FAMILY = "close_family"


def cluster_tokens(label: Optional[str], side: Optional[str]) -> set[str]:
    """
    Presence tokens contributed by a labelled cluster.

    Bride/Groom map to themselves; family roles map to a side token
    (``family_bride`` / ``family_groom``) and, for immediate family, also
    ``close_family``. Unlabelled clusters contribute nothing.
    """
    if not label:
        return set()
    if label == ROLE_BRIDE:
        return {ROLE_BRIDE}
    if label == ROLE_GROOM:
        return {ROLE_GROOM}
    if label in _FAMILY_ROLES:
        tokens: set[str] = set()
        if side == SIDE_BRIDE:
            tokens.add(TOKEN_FAMILY_BRIDE)
        elif side == SIDE_GROOM:
            tokens.add(TOKEN_FAMILY_GROOM)
        if label in _IMMEDIATE_FAMILY:
            tokens.add(TOKEN_CLOSE_FAMILY)
        return tokens
    if label == ROLE_FRIEND:
        return {ROLE_FRIEND}
    return {label}  # any custom label is its own token


def normalize_path(path: PathLike) -> str:
    """Absolute, normalized path string for reliable cross-stage matching."""
    return str(Path(path).resolve(strict=False))


@dataclasses.dataclass
class PhotoRecord:
    """One photo in the inventory: identity-free analysis + edit state."""

    source_path: str
    category: str = FOLDER_REVIEW
    capture_time: Optional[str] = None  # ISO-8601
    quality_score: Optional[float] = None
    tier: Optional[str] = None
    blur_score: Optional[float] = None
    sharpness: Optional[float] = None
    brightness: Optional[float] = None
    contrast: Optional[float] = None
    faces_detected: Optional[bool] = None
    face_count: Optional[int] = None
    usable: Optional[bool] = None
    is_best_shot: bool = False
    is_duplicate: bool = False
    persons: list[str] = dataclasses.field(default_factory=list)  # empty in Phase 1
    edit_recipe: Optional[dict[str, Any]] = None
    # Optional path to a retouched/relinked file to render in place of the
    # original (the retouch round-trip); ``None`` means render the original.
    linked_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PhotoRecord":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass
class EventRecord:
    """A chronological event/ceremony segment (named in Phase 3)."""

    index: int
    name: str
    photos: list[str]
    start: Optional[str] = None
    end: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventRecord":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass
class SectionRecord:
    """One album section and its ordered photos."""

    name: str
    kind: str
    photos: list[str]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SectionRecord":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass
class SpreadRecord:
    """A laid-out spread, tagged with its section."""

    index: int
    section: str
    width_px: int
    height_px: int
    placements: list[dict[str, Any]]  # {path, frame_px:[x,y,w,h], crop:[x,y,w,h]}

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpreadRecord":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass
class PersonClusterRecord:
    """
    One discovered person (a cluster of faces) and its label.

    Attributes:
        cluster_id: Stable id within this analysis run.
        photos: Distinct photos the person appears in.
        size: Number of member faces.
        centroid: L2-normalized mean embedding (used to re-bind the label to a
            freshly computed cluster on a later run).
        representative: A photo to show for this person in the labelling UI.
        label: Role (e.g. ``"bride"``); ``None`` until the photographer labels it.
        side: ``"bride"``/``"groom"`` for family members; ``None`` otherwise.
    """

    cluster_id: int
    photos: list[str] = dataclasses.field(default_factory=list)
    size: int = 0
    centroid: list[float] = dataclasses.field(default_factory=list)
    representative: Optional[str] = None
    label: Optional[str] = None
    side: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonClusterRecord":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass
class ProjectMeta:
    source_folder: str
    created_at: str
    schema_version: int = SCHEMA_VERSION
    generator: str = "photoflow"
    album_spec: dict[str, Any] = dataclasses.field(default_factory=dict)
    photo_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectMeta":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass
class ExportMeta:
    manifest_path: Optional[str] = None
    retouch_needed: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExportMeta":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass
class AlbumProject:
    """The whole album, persisted as ``album_manifest.json``."""

    meta: ProjectMeta
    photos: dict[str, PhotoRecord] = dataclasses.field(default_factory=dict)
    clusters: list[PersonClusterRecord] = dataclasses.field(default_factory=list)
    events: list[EventRecord] = dataclasses.field(default_factory=list)
    sections: list[SectionRecord] = dataclasses.field(default_factory=list)
    spreads: list[SpreadRecord] = dataclasses.field(default_factory=list)
    overrides: dict[str, str] = dataclasses.field(default_factory=dict)
    export: ExportMeta = dataclasses.field(default_factory=lambda: ExportMeta())

    # ----------------------------------------------------------------- #
    # Construction
    # ----------------------------------------------------------------- #
    @classmethod
    def new(cls, source_folder: PathLike, album_spec: Optional[dict] = None) -> "AlbumProject":
        meta = ProjectMeta(
            source_folder=str(source_folder),
            created_at=datetime.now().isoformat(timespec="seconds"),
            album_spec=album_spec or {},
        )
        return cls(meta=meta)

    # ----------------------------------------------------------------- #
    # Access helpers
    # ----------------------------------------------------------------- #
    def add_photo(self, record: PhotoRecord) -> None:
        self.photos[record.source_path] = record
        self.meta.photo_count = len(self.photos)

    def get(self, source_path: PathLike) -> Optional[PhotoRecord]:
        key = str(source_path)
        if key in self.photos:
            return self.photos[key]
        norm = normalize_path(source_path)
        for path, rec in self.photos.items():
            if normalize_path(path) == norm:
                return rec
        return None

    def candidate_pool(self) -> list[PhotoRecord]:
        """Album-eligible photos: usable and not duplicates (BestShots + Review)."""
        return [
            rec
            for rec in self.photos.values()
            if rec.category in (FOLDER_BEST_SHOTS, FOLDER_REVIEW)
        ]

    # ----------------------------------------------------------------- #
    # Identity / labelling (Phase 2)
    # ----------------------------------------------------------------- #
    def clusters_for_review(self) -> list[PersonClusterRecord]:
        """Clusters to present for labelling, largest (most photographed) first."""
        return sorted(self.clusters, key=lambda c: (-c.size, c.cluster_id))

    def label_cluster(
        self, cluster_id: int, label: Optional[str], side: Optional[str] = None
    ) -> None:
        """
        Label a person cluster; the label propagates to all its photos.

        Passing ``label=None`` clears the label. Recomputes every photo's
        ``persons`` tokens so the story/queries stay in sync.
        """
        found = False
        for cluster in self.clusters:
            if cluster.cluster_id == cluster_id:
                cluster.label = label
                cluster.side = side if label else None
                found = True
                break
        if not found:
            raise KeyError(f"No cluster with id {cluster_id}")
        self.recompute_persons()

    def has_identity(self) -> bool:
        """True once at least one cluster is labelled."""
        return any(c.label for c in self.clusters)

    def recompute_persons(self) -> None:
        """Rebuild each photo's ``persons`` tokens from the labelled clusters."""
        tokens_by_photo: dict[str, set[str]] = {}
        for cluster in self.clusters:
            tokens = cluster_tokens(cluster.label, cluster.side)
            if not tokens:
                continue
            for photo in cluster.photos:
                tokens_by_photo.setdefault(photo, set()).update(tokens)
        for path, rec in self.photos.items():
            rec.persons = sorted(tokens_by_photo.get(path, set()))

    # ----------------------------------------------------------------- #
    # Serialization
    # ----------------------------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta.to_dict(),
            "photos": {p: rec.to_dict() for p, rec in self.photos.items()},
            "clusters": [c.to_dict() for c in self.clusters],
            "events": [e.to_dict() for e in self.events],
            "sections": [s.to_dict() for s in self.sections],
            "spreads": [s.to_dict() for s in self.spreads],
            "overrides": dict(self.overrides),
            "export": self.export.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlbumProject":
        return cls(
            meta=ProjectMeta.from_dict(data.get("meta", {})),
            photos={
                p: PhotoRecord.from_dict(rec)
                for p, rec in data.get("photos", {}).items()
            },
            clusters=[
                PersonClusterRecord.from_dict(c) for c in data.get("clusters", [])
            ],
            events=[EventRecord.from_dict(e) for e in data.get("events", [])],
            sections=[SectionRecord.from_dict(s) for s in data.get("sections", [])],
            spreads=[SpreadRecord.from_dict(s) for s in data.get("spreads", [])],
            overrides=dict(data.get("overrides", {})),
            export=ExportMeta.from_dict(data.get("export", {})),
        )

    def save(self, path: PathLike) -> Path:
        """Write the manifest. If ``path`` is a directory, writes the default name."""
        out = Path(path)
        if out.is_dir() or out.suffix == "":
            out = out / MANIFEST_FILENAME
        out.parent.mkdir(parents=True, exist_ok=True)
        self.export.manifest_path = str(out)
        out.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: PathLike) -> "AlbumProject":
        p = Path(path)
        if p.is_dir():
            p = p / MANIFEST_FILENAME
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
