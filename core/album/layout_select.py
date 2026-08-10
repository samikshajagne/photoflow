"""
Layout selection policy for PhotoFlow albums (Phase 1).

The layout *engine* (:mod:`core.album.layout`) knows how to place a list of
photos into a spread; this module decides the *policy* — how many photos per
spread for each section, which yields the template style:

- **Cover / hero**  -> 1 per spread  => full-bleed single image
- **Portraits**     -> 2 per spread  => side-by-side
- **Family**        -> 4 per spread  => 2x2 grid
- **mixed** (Highlights / Ceremony / Closing) -> 3 per spread => collage

It reads each photo's aspect ratio (header-only, no full decode) so the engine
can cover-fit correctly, and tags every produced spread with its section so the
:class:`~core.album.project.AlbumProject` keeps a section -> spread mapping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.album.layout import AlbumLayoutEngine, AlbumSpec, PhotoItem
from core.album.pacing import PACING_EDITORIAL, PACING_UNIFORM, available_rhythms
from core.album.project import AlbumProject, SpreadRecord
from utils.logger import get_logger

logger = get_logger(__name__)

# Photos-per-spread by section kind -> the engine's count-based template.
_PER_SPREAD_BY_KIND: dict[str, int] = {
    "cover": 1,       # hero -> full spread
    "couple": 1,      # couple hero -> full spread
    "portraits": 2,   # bride/groom portrait pair, side-by-side
    "family": 4,      # family group -> grid
    "highlights": 3,  # collage
    "ceremony": 3,    # collage
    "closing": 3,     # collage
}
_DEFAULT_PER_SPREAD = 3  # collage default when a section has no explicit count

# Hero sections always get one full-spread photo regardless of density.
_HERO_KINDS = frozenset({"cover", "couple"})

# Density presets scale the (non-hero) photos-per-spread counts, which in turn
# changes how many spreads the album has: fewer photos per spread => more
# spreads (spacious), more photos per spread => fewer spreads (dense).
DENSITY_SPACIOUS = "spacious"
DENSITY_BALANCED = "balanced"
DENSITY_DENSE = "dense"
_DENSITY_MULTIPLIER: dict[str, float] = {
    DENSITY_SPACIOUS: 0.5,
    DENSITY_BALANCED: 1.0,
    DENSITY_DENSE: 1.8,
}
# Per-spread photo cap by density (raises the engine ceiling for dense albums).
_DENSITY_MAX_PER_SPREAD: dict[str, int] = {
    DENSITY_SPACIOUS: 4,
    DENSITY_BALANCED: 4,
    DENSITY_DENSE: 6,
}


class LayoutSelector:
    """
    Applies a per-section layout policy over the album layout engine.

    Args:
        engine: The placement engine. Defaults to one whose per-spread ceiling
            matches ``density``.
        per_spread_by_kind: Override the section-kind -> photos-per-spread map
            (applied before density scaling).
        density: ``"spacious"``, ``"balanced"`` (default), or ``"dense"`` —
            scales non-hero photos-per-spread to control the album's spread
            count. Unknown values fall back to balanced.
        pacing: Narrative rhythm (see :mod:`core.album.pacing`). ``density``
            sets the album's *average* spreads-per-photo; ``pacing`` decides
            whether every spread sits at that average (``"uniform"``) or varies
            around it — dense spreads followed by a single full-bleed image.
            The two are independent because pacing preserves the spread count
            exactly, so changing it never disturbs the page budget. Defaults to
            ``"editorial"``; unknown values fall back to uniform.
    """

    # Auto page budget when the caller doesn't specify one: aim for roughly the
    # middle of a 20–30 spread album so all photos fit in a reasonable book.
    AUTO_TARGET_PAGES = 25

    def __init__(
        self,
        engine: Optional[AlbumLayoutEngine] = None,
        per_spread_by_kind: Optional[dict[str, int]] = None,
        density: str = DENSITY_BALANCED,
        target_pages: Optional[int] = None,
        pacing: str = PACING_EDITORIAL,
    ) -> None:
        # Page budget: >0 packs all photos into ~that many spreads; None/0 uses
        # the auto target so albums don't balloon to one photo per spread.
        self.target_pages = target_pages if (target_pages and target_pages > 0) else None
        self.density = density if density in _DENSITY_MULTIPLIER else DENSITY_BALANCED
        self.pacing = pacing if pacing in available_rhythms() else PACING_UNIFORM
        self.engine = engine or AlbumLayoutEngine(
            max_per_spread=_DENSITY_MAX_PER_SPREAD[self.density]
        )

        base = dict(_PER_SPREAD_BY_KIND)
        if per_spread_by_kind:
            base.update(per_spread_by_kind)

        multiplier = _DENSITY_MULTIPLIER[self.density]
        cap = self.engine.max_per_spread
        self._per_spread = {
            kind: self._scaled(kind, count, multiplier, cap)
            for kind, count in base.items()
        }
        self._default_per_spread = min(
            max(1, round(_DEFAULT_PER_SPREAD * multiplier)), cap
        )

    @staticmethod
    def _scaled(kind: str, count: int, multiplier: float, cap: int) -> int:
        """Density-scaled photos-per-spread; heroes stay at 1, others clamped."""
        if kind in _HERO_KINDS:
            return 1
        return min(max(1, round(count * multiplier)), cap)

    def _per_spread_for(self, kind: str, budget_per: int) -> int:
        """
        Photos-per-spread for one section, combining all three policies.

        Three intents have to coexist here:

        1. **Aesthetics** — each section kind has a natural count (a cover is
           one full-spread image, portraits sit side by side, a family grid
           takes four), scaled by the user's chosen ``density``.
        2. **Page budget** — everything still has to fit in roughly
           ``target_pages`` spreads, so a 200-photo shoot doesn't become an
           88-spread book.
        3. **Heroes** — cover/couple spreads are always a single photo.

        Heroes win outright. Otherwise the aesthetic count is used, but raised
        to ``budget_per`` when packing is needed to stay inside the page
        budget; the budget is allowed to exceed the density cap because
        overflowing the book is worse than a busier spread.

        (Previously this method didn't exist and ``select()`` used
        ``budget_per`` for every non-cover section, which silently made the
        density setting — exposed in the album settings dialog as "Photos per
        spread" — do nothing at all, and left ``_per_spread``/``_scaled``
        computed but unused.)
        """
        if kind in _HERO_KINDS:
            return 1
        aesthetic = self._per_spread.get(kind, self._default_per_spread)
        return max(aesthetic, budget_per)

    def _budget_per_spread(self, project: AlbumProject) -> int:
        """
        Photos-per-spread needed to fit all (non-cover) photos into the page
        budget. Only the cover stays a single-photo spread; every other section
        packs to this count so a 200+ photo shoot doesn't become 88 spreads.
        """
        total = sum(
            len(s.photos) for s in project.sections if s.kind != "cover" and s.photos
        )
        target = self.target_pages or self.AUTO_TARGET_PAGES
        target = max(1, target - 1)  # reserve the cover spread
        if total <= 0:
            return self._default_per_spread
        per = -(-total // target)  # ceil(total / target)
        return max(2, per)

    def select(
        self,
        project: AlbumProject,
        spec: AlbumSpec,
        faces_by_path: Optional[dict[str, tuple[tuple[float, float, float, float], ...]]] = None,
    ) -> list[SpreadRecord]:
        """
        Lay out every section into spreads and return them, section-tagged.

        Spreads are numbered globally in album order. Sections with no
        placeable photos are skipped. Photos-per-spread is driven by the page
        budget (all photos fit in ~``target_pages`` spreads), then varied
        around that average by ``pacing``; the cover stays a single full-spread
        image.

        Args:
            faces_by_path: Optional ``source_path -> relative face boxes`` map
                (each box ``(x, y, w, h)`` in ``[0, 1]``). When supplied, faces
                drive a face-safe cover crop and are stored on each placement so
                the renderer keeps them visible too. Missing/empty entries fall
                back to the historical centered crop.
        """
        faces_by_path = faces_by_path or {}
        spreads: list[SpreadRecord] = []
        global_index = 0

        budget_per = self._budget_per_spread(project)
        # Let the engine pack up to the budget count per spread.
        self.engine.max_per_spread = max(self.engine.max_per_spread, budget_per)

        for section in project.sections:
            items = [
                PhotoItem(
                    path=p,
                    aspect_ratio=self._aspect(p),
                    face_boxes=self._faces_for(p, faces_by_path),
                )
                for p in section.photos
            ]
            if not items:
                continue
            per = self._per_spread_for(section.kind, budget_per)
            # Hero sections are one photo per spread by definition, so there is
            # no rhythm to apply — pacing them would be a no-op at best.
            pacing = PACING_UNIFORM if section.kind in _HERO_KINDS else self.pacing
            for spread in self.engine.layout(
                items, spec, per_spread=per, pacing=pacing
            ):
                spreads.append(
                    SpreadRecord(
                        index=global_index,
                        section=section.name,
                        width_px=spread.width_px,
                        height_px=spread.height_px,
                        placements=[
                            {
                                "path": pl.path,
                                "frame_px": list(pl.frame_px),
                                "crop": list(pl.crop),
                                "fit": pl.fit,
                                "face_boxes": [
                                    list(b) for b in self._faces_for(pl.path, faces_by_path)
                                ],
                            }
                            for pl in spread.placements
                        ],
                    )
                )
                global_index += 1

        logger.info(
            "Layout selection produced %d spread(s) across %d section(s).",
            len(spreads),
            len(project.sections),
        )
        return spreads

    @staticmethod
    def _faces_for(
        path: str,
        faces_by_path: dict[str, tuple[tuple[float, float, float, float], ...]],
    ) -> tuple[tuple[float, float, float, float], ...]:
        """
        Validated relative face boxes for ``path``.

        Filters to well-formed boxes clamped inside ``[0, 1]`` so a stray
        detection can never trip :class:`PhotoItem`'s validation and abort layout.
        """
        boxes: list[tuple[float, float, float, float]] = []
        for box in faces_by_path.get(path, ()) or ():
            try:
                x, y, w, h = (float(v) for v in box)
            except (TypeError, ValueError):
                continue
            if w <= 0 or h <= 0:
                continue
            x = min(max(x, 0.0), 1.0)
            y = min(max(y, 0.0), 1.0)
            w = min(w, 1.0 - x)
            h = min(h, 1.0 - y)
            if w > 0 and h > 0:
                boxes.append((x, y, w, h))
        return tuple(boxes)

    @staticmethod
    def _aspect(path: str) -> float:
        """
        Width/height from the image header, honoring EXIF orientation, so a
        portrait photo reports a portrait (<1) aspect even when its pixels are
        stored landscape with a rotate tag. Falls back to 1.0 if unreadable.
        """
        try:
            from PIL import Image  # lazy; header read only (no full decode)

            with Image.open(path) as img:
                width, height = img.size
                orientation = img.getexif().get(0x0112, 1)  # 0x0112 = Orientation
            if orientation in (5, 6, 7, 8):  # 90°/270° rotations swap the axes
                width, height = height, width
            if width > 0 and height > 0:
                return float(width) / float(height)
        except Exception:  # noqa: BLE001 - unreadable -> safe square fallback
            logger.warning("Could not read dimensions for '%s'; assuming square.", path)
        return 1.0
