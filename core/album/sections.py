"""
Album section builder for PhotoFlow (Phase 3).

The album is a sequence of custom **sections** — Cover, Couple, Bride, Groom,
Families, then each ceremony in order. Each section is *auto-populated* by a
rule over data PhotoFlow already produces, then the photographer adjusts it; the
adjustments are sticky (they survive re-analysis), exactly like the category
overrides.

Inputs a rule can draw on (carried in :class:`SectionContext`):
- ``persons_present`` — which labelled people appear in each photo
  (from :mod:`core.identity`).
- ``quality_by_path`` — the 0–100 quality score (for ranking / cover pick).
- ``events`` — chronological ceremony segments (from :mod:`core.timeline`).
- ``eligible`` — the pool a quality-ranked section may draw from (e.g.
  BestShots). Ceremony (event) sections deliberately ignore this so the album
  can include *all* of a ceremony's photos in order, not just the best.

This module is pure data/selection logic — no images, no Qt — so it is fully
unit-testable.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Optional

from core.timeline import EventSegment
from utils.logger import get_logger

logger = get_logger(__name__)

# Section kinds.
KIND_COVER = "cover"      # single strongest couple shot
KIND_COUPLE = "couple"    # photos containing all of `labels` (default bride+groom)
KIND_SOLO = "solo"        # photos whose only labelled person is `label`
KIND_GROUP = "group"      # photos containing `label`
KIND_EVENT = "event"      # a ceremony segment, in chronological order


class SectionError(Exception):
    """Raised when a section specification is invalid."""


@dataclasses.dataclass(frozen=True)
class SectionSpec:
    """
    Declares how one section is auto-populated.

    Attributes:
        name: Display name (e.g. "Bride").
        kind: One of the ``KIND_*`` constants.
        label: The person label for SOLO/GROUP sections.
        labels: The people for a COUPLE/COVER section (default bride + groom).
        event_index: Which timeline event a EVENT section uses.
        limit: Optional cap on the number of photos.
    """

    name: str
    kind: str
    label: Optional[str] = None
    labels: tuple[str, ...] = ()
    event_index: Optional[int] = None
    limit: Optional[int] = None

    def __post_init__(self) -> None:
        valid = {KIND_COVER, KIND_COUPLE, KIND_SOLO, KIND_GROUP, KIND_EVENT}
        if self.kind not in valid:
            raise SectionError(f"Unknown section kind '{self.kind}'")
        if self.kind in (KIND_SOLO, KIND_GROUP) and not self.label:
            raise SectionError(f"Section '{self.name}' ({self.kind}) needs a label")
        if self.kind == KIND_EVENT and self.event_index is None:
            raise SectionError(f"Section '{self.name}' (event) needs an event_index")
        if self.limit is not None and self.limit < 0:
            raise SectionError("limit must be >= 0")


@dataclasses.dataclass(frozen=True)
class SectionContext:
    """Everything the rules need to resolve sections (all plain data)."""

    persons_present: dict[str, set[str]]
    quality_by_path: dict[str, float]
    events: tuple[EventSegment, ...] = ()
    eligible: Optional[frozenset[str]] = None  # None => all known photos

    def _pool(self) -> set[str]:
        if self.eligible is not None:
            return set(self.eligible)
        return set(self.persons_present) | set(self.quality_by_path)

    def _rank(self, photos: Iterable[str]) -> list[str]:
        """Sort by quality descending, then path for determinism."""
        return sorted(
            photos, key=lambda p: (-self.quality_by_path.get(p, 0.0), p)
        )


# Default people for a couple/cover section.
_DEFAULT_COUPLE = ("bride", "groom")


def build_section(spec: SectionSpec, ctx: SectionContext) -> tuple[str, ...]:
    """
    Resolve a section spec to an ordered tuple of photo paths.

    Quality-ranked kinds (cover/couple/solo/group) draw from ``ctx.eligible``
    and are ordered best-first. EVENT sections return the segment's photos in
    chronological order and ignore ``eligible`` (the whole ceremony, in order).
    """
    if spec.kind == KIND_EVENT:
        photos = _event_photos(spec, ctx)
    else:
        photos = _ranked_photos(spec, ctx)

    if spec.limit is not None:
        photos = photos[: spec.limit]
    return tuple(photos)


def _ranked_photos(spec: SectionSpec, ctx: SectionContext) -> list[str]:
    pool = ctx._pool()
    present = ctx.persons_present

    if spec.kind == KIND_GROUP:
        match = {p for p in pool if spec.label in present.get(p, set())}
    elif spec.kind == KIND_SOLO:
        match = {p for p in pool if present.get(p, set()) == {spec.label}}
    elif spec.kind in (KIND_COUPLE, KIND_COVER):
        wanted = set(spec.labels or _DEFAULT_COUPLE)
        match = {p for p in pool if wanted <= present.get(p, set())}
    else:  # pragma: no cover - guarded by SectionSpec validation
        raise SectionError(f"Unsupported kind '{spec.kind}'")

    ranked = ctx._rank(match)
    if spec.kind == KIND_COVER:
        return ranked[:1]
    return ranked


def _event_photos(spec: SectionSpec, ctx: SectionContext) -> list[str]:
    if not 0 <= spec.event_index < len(ctx.events):
        raise SectionError(
            f"event_index {spec.event_index} out of range "
            f"(have {len(ctx.events)} events)"
        )
    return list(ctx.events[spec.event_index].photos)


@dataclasses.dataclass(frozen=True)
class Section:
    """
    A resolved section plus sticky manual overrides.

    ``auto`` is the rule's output. The photographer's edits are recorded as
    ``added`` / ``removed`` / an explicit ``order`` and replayed on top, so they
    persist even when ``auto`` changes after a re-run. Use :meth:`resolved` for
    the final ordered photo list.
    """

    spec: SectionSpec
    auto: tuple[str, ...]
    added: tuple[str, ...] = ()
    removed: frozenset[str] = frozenset()
    order: Optional[tuple[str, ...]] = None

    @classmethod
    def build(cls, spec: SectionSpec, ctx: SectionContext) -> "Section":
        return cls(spec=spec, auto=build_section(spec, ctx))

    def resolved(self) -> tuple[str, ...]:
        base = [p for p in self.auto if p not in self.removed]
        for extra in self.added:
            if extra not in self.removed and extra not in base:
                base.append(extra)
        if self.order is None:
            return tuple(base)
        base_set = set(base)
        ordered = [p for p in self.order if p in base_set]
        # Anything not named in `order` keeps its relative position at the end.
        ordered += [p for p in base if p not in set(ordered)]
        return tuple(ordered)

    # Sticky edits return a new Section (frozen dataclass; functional updates).
    def add(self, photo: str) -> "Section":
        if photo in self.added or photo in self.auto:
            removed = frozenset(self.removed - {photo})
            return dataclasses.replace(self, removed=removed)
        return dataclasses.replace(
            self, added=self.added + (photo,), removed=frozenset(self.removed - {photo})
        )

    def remove(self, photo: str) -> "Section":
        return dataclasses.replace(self, removed=frozenset(self.removed | {photo}))

    def reorder(self, order: Iterable[str]) -> "Section":
        return dataclasses.replace(self, order=tuple(order))


@dataclasses.dataclass(frozen=True)
class AlbumProject:
    """An ordered collection of sections."""

    sections: tuple[Section, ...] = ()

    @classmethod
    def build(cls, specs: Iterable[SectionSpec], ctx: SectionContext) -> "AlbumProject":
        return cls(tuple(Section.build(spec, ctx) for spec in specs))

    def resolved(self) -> list[tuple[str, tuple[str, ...]]]:
        """``(section_name, ordered_photos)`` for each section, in album order."""
        return [(s.spec.name, s.resolved()) for s in self.sections]

    def all_photos(self) -> list[str]:
        """Every photo across sections, in album order (duplicates kept per section)."""
        out: list[str] = []
        for section in self.sections:
            out.extend(section.resolved())
        return out
