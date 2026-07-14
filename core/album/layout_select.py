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
_DEFAULT_PER_SPREAD = 3

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
    """

    def __init__(
        self,
        engine: Optional[AlbumLayoutEngine] = None,
        per_spread_by_kind: Optional[dict[str, int]] = None,
        density: str = DENSITY_BALANCED,
    ) -> None:
        self.density = density if density in _DENSITY_MULTIPLIER else DENSITY_BALANCED
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

    def select(self, project: AlbumProject, spec: AlbumSpec) -> list[SpreadRecord]:
        """
        Lay out every section into spreads and return them, section-tagged.

        Spreads are numbered globally in album order. Sections with no
        placeable photos are skipped.
        """
        spreads: list[SpreadRecord] = []
        global_index = 0

        for section in project.sections:
            items = [
                PhotoItem(path=p, aspect_ratio=self._aspect(p), face_boxes=())
                for p in section.photos
            ]
            if not items:
                continue
            per = self._per_spread.get(section.kind, self._default_per_spread)
            for spread in self.engine.layout(items, spec, per_spread=per):
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
    def _aspect(path: str) -> float:
        """Width/height from the image header; falls back to 1.0 if unreadable."""
        try:
            from PIL import Image  # lazy; header read only (no full decode)

            with Image.open(path) as img:
                width, height = img.size
            if width > 0 and height > 0:
                return float(width) / float(height)
        except Exception:  # noqa: BLE001 - unreadable -> safe square fallback
            logger.warning("Could not read dimensions for '%s'; assuming square.", path)
        return 1.0
