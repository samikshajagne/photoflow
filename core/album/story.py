"""
Phase 1 Story Builder for PhotoFlow albums.

Turns the album's photo inventory into an ordered set of sections **without
needing identity recognition**. It composes the narrative from data that
already exists today: capture time, quality scores, BestShots, and face
*counts* (not identities). When richer data is absent it degrades gracefully —
no faces means no Portraits/Family sheets; no timestamps means a path-ordered
fallback; an empty candidate pool yields no sections at all.

Sections produced (omitted when empty):

- **Cover**      — the single strongest shot (prefers one with people).
- **Highlights** — the top BestShots by quality.
- **Ceremony**   — every album-eligible photo in chronological order (the story
                   backbone).
- **Family**     — group shots (face_count >= a threshold), best-first.
- **Portraits**  — single-subject shots (face_count == 1), best-first.
- **Closing**    — the last few photos chronologically.

Identity (Phase 3) will later split Couple/Bride/Groom/Family by person; this
Phase 1 version is the graceful, identity-free baseline. Sections may overlap
(a cover is also a highlight); the photographer curates from there.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from core.album.project import (
    AlbumProject,
    PhotoRecord,
    ROLE_BRIDE,
    ROLE_GROOM,
    SectionRecord,
    TOKEN_CLOSE_FAMILY,
    TOKEN_FAMILY_BRIDE,
    TOKEN_FAMILY_GROOM,
)
from core.album.sections import (
    KIND_COUPLE,
    KIND_GROUP,
    KIND_SOLO,
    SectionContext,
    SectionSpec,
    build_section,
)
from utils.logger import get_logger

logger = get_logger(__name__)


class StoryBuilder:
    """
    Builds identity-free album sections from an :class:`AlbumProject`.

    Args:
        highlights_limit: Max photos in the Highlights section.
        portrait_face_count: face_count that counts as a single-subject portrait.
        family_min_faces: Minimum face_count to count as a group/family shot.
        closing_count: Number of trailing chronological photos for Closing.
        cover_prefer_faces: Prefer a cover that contains people.
    """

    def __init__(
        self,
        highlights_limit: int = 24,
        portrait_face_count: int = 1,
        family_min_faces: int = 3,
        closing_count: int = 8,
        cover_prefer_faces: bool = True,
    ) -> None:
        self.highlights_limit = max(0, int(highlights_limit))
        self.portrait_face_count = int(portrait_face_count)
        self.family_min_faces = int(family_min_faces)
        self.closing_count = max(0, int(closing_count))
        self.cover_prefer_faces = bool(cover_prefer_faces)

    def build(self, project: AlbumProject) -> list[SectionRecord]:
        """Return the ordered, non-empty sections for ``project``."""
        candidates = project.candidate_pool()
        if not candidates:
            logger.info("Story builder: no album-eligible photos.")
            return []

        # Person-aware narrative when identities are labelled; otherwise the
        # Phase 1 (time + quality) story, unchanged.
        if project.has_identity():
            return self._build_with_identity(project, candidates)

        by_quality = sorted(candidates, key=self._quality_key)
        chronological = sorted(candidates, key=self._time_key)
        best = [r for r in by_quality if r.is_best_shot]

        sections: list[SectionRecord] = []

        cover = self._pick_cover(by_quality, best)
        if cover is not None:
            sections.append(SectionRecord("Cover", "cover", [cover.source_path]))

        cover_path = cover.source_path if cover else None
        highlight_paths = [
            r.source_path
            for r in (best or by_quality)
            if r.source_path != cover_path
        ][: self.highlights_limit]
        self._append(sections, "Highlights", "highlights", highlight_paths)

        # Ceremony = the whole eligible set, in capture order (story backbone).
        self._append(
            sections, "Ceremony", "ceremony", [r.source_path for r in chronological]
        )

        family = [
            r.source_path
            for r in by_quality
            if (r.face_count or 0) >= self.family_min_faces
        ]
        self._append(sections, "Family", "family", family)

        portraits = [
            r.source_path
            for r in by_quality
            if (r.face_count or 0) == self.portrait_face_count
        ]
        self._append(sections, "Portraits", "portraits", portraits)

        closing = [r.source_path for r in chronological[-self.closing_count :]]
        self._append(sections, "Closing", "closing", closing)

        logger.info(
            "Story builder produced %d section(s): %s",
            len(sections),
            ", ".join(s.name for s in sections),
        )
        return sections

    # ----------------------------------------------------------------- #
    # Identity-aware narrative (Phase 2)
    # ----------------------------------------------------------------- #
    def _build_with_identity(
        self, project: AlbumProject, candidates: list[PhotoRecord]
    ) -> list[SectionRecord]:
        """Person sections (Couple/Bride/Groom/Family) + the Phase 1 backbone."""
        persons_present = {r.source_path: set(r.persons) for r in candidates}
        quality_by_path = {r.source_path: (r.quality_score or 0.0) for r in candidates}
        eligible = frozenset(r.source_path for r in candidates)
        ctx = SectionContext(
            persons_present=persons_present,
            quality_by_path=quality_by_path,
            eligible=eligible,
        )

        def section(name, kind, spec) -> None:
            photos = list(build_section(spec, ctx))
            if photos:
                sections.append(SectionRecord(name, kind, photos))

        sections: list[SectionRecord] = []
        by_quality = sorted(candidates, key=self._quality_key)
        chronological = sorted(candidates, key=self._time_key)

        # Cover: the strongest couple shot, else the strongest photo overall.
        couple_photos = list(
            build_section(SectionSpec("Couple", KIND_COUPLE, labels=(ROLE_BRIDE, ROLE_GROOM)), ctx)
        )
        cover = couple_photos[0] if couple_photos else (by_quality[0].source_path if by_quality else None)
        if cover is not None:
            sections.append(SectionRecord("Cover", "cover", [cover]))

        section("Couple", "couple", SectionSpec("Couple", KIND_COUPLE, labels=(ROLE_BRIDE, ROLE_GROOM)))
        section("Bride", "portraits", SectionSpec("Bride", KIND_SOLO, label=ROLE_BRIDE))
        section("Groom", "portraits", SectionSpec("Groom", KIND_SOLO, label=ROLE_GROOM))
        section("Bride Family", "family", SectionSpec("Bride Family", KIND_GROUP, label=TOKEN_FAMILY_BRIDE))
        section("Groom Family", "family", SectionSpec("Groom Family", KIND_GROUP, label=TOKEN_FAMILY_GROOM))
        section("Close Family", "family", SectionSpec("Close Family", KIND_GROUP, label=TOKEN_CLOSE_FAMILY))

        # Phase 1 backbone retained: Highlights, Ceremony, Closing.
        highlights = [r.source_path for r in by_quality if r.source_path != cover][
            : self.highlights_limit
        ]
        self._append(sections, "Highlights", "highlights", highlights)
        self._append(
            sections, "Ceremony", "ceremony", [r.source_path for r in chronological]
        )
        self._append(
            sections, "Closing", "closing",
            [r.source_path for r in chronological[-self.closing_count :]],
        )

        logger.info(
            "Story builder (identity) produced %d section(s): %s",
            len(sections),
            ", ".join(s.name for s in sections),
        )
        return sections

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _pick_cover(
        self, by_quality: list[PhotoRecord], best: list[PhotoRecord]
    ) -> Optional[PhotoRecord]:
        pool = best or by_quality
        if self.cover_prefer_faces:
            with_faces = [r for r in pool if r.faces_detected]
            if with_faces:
                return with_faces[0]
        return pool[0] if pool else None

    @staticmethod
    def _append(
        sections: list[SectionRecord], name: str, kind: str, photos: list[str]
    ) -> None:
        if photos:
            sections.append(SectionRecord(name, kind, photos))

    @staticmethod
    def _quality_key(rec: PhotoRecord):
        # Highest quality first; ties broken by path for determinism.
        return (-(rec.quality_score or 0.0), rec.source_path)

    @staticmethod
    def _time_key(rec: PhotoRecord):
        # ISO timestamps sort chronologically; unknown times sort first.
        return (rec.capture_time or "", rec.source_path)
