"""
Album export + retouch round-trip for PhotoFlow (Phase 5).

PhotoFlow is the selection / organization / auto-edit / layout brain; beauty
retouching is handed off to an external editor. The bridge between them is a
**tool-agnostic JSON project manifest**: the single source of truth describing
the album spec, the ordered sections, every spread's placements (pixel frame +
source crop), the auto-edit recipe per photo, and a per-asset retouch status.

Assets are referenced by **link** (path), never copied or flattened, so the
album stays editable and retouch can happen on the linked files. The round-trip
is therefore simple: export the manifest with links, retouch the linked files
externally, mark each asset ``done`` (and ``relink`` if the retoucher saved to a
new path), then a downstream adapter renders the final album.

The JSON manifest is deliberately format-neutral. Concrete adapters (InDesign
IDML, Affinity, PSD, an album tool's folder structure) implement
:class:`LayoutExporter` over the same manifest; :class:`JsonProjectExporter` is
the reference implementation shipped here.
"""

from __future__ import annotations

import abc
import dataclasses
import json
from pathlib import Path
from typing import Iterable, Optional, Union

from core.album.layout import AlbumSpec, Spread
from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "album_project.json"

# Retouch lifecycle for an asset.
RETOUCH_NONE = "none"      # no beauty retouch needed
RETOUCH_NEEDED = "needed"  # flagged, awaiting the external editor
RETOUCH_DONE = "done"      # retouched file is in place
_RETOUCH_STATES = {RETOUCH_NONE, RETOUCH_NEEDED, RETOUCH_DONE}


class AlbumExportError(Exception):
    """Raised when an album project cannot be built, written, or updated."""


def build_manifest(
    spec: AlbumSpec,
    sections: Iterable[tuple[str, Iterable[str]]],
    spreads: Iterable[Spread],
    recipes_by_path: Optional[dict[str, object]] = None,
    retouch_needed: Optional[Iterable[str]] = None,
) -> dict:
    """
    Assemble the project manifest (a plain, JSON-serializable dict).

    Args:
        spec: The album page/spread specification.
        sections: ``(name, ordered_photo_paths)`` pairs in album order.
        spreads: Laid-out spreads (from the layout engine).
        recipes_by_path: Optional map of photo path -> object with ``as_dict()``
            (an auto-edit ``EditRecipe``), recorded per asset.
        retouch_needed: Photo paths flagged for beauty retouch.

    Returns:
        The manifest dict (also what :func:`export_album` writes).
    """
    recipes_by_path = recipes_by_path or {}
    needed = set(retouch_needed or ())

    section_list = [
        {"name": name, "photos": list(photos)} for name, photos in sections
    ]
    spread_list = [_spread_to_dict(s) for s in spreads]

    # Assets = every distinct photo referenced by a section or a spread.
    referenced: list[str] = []
    seen: set[str] = set()
    for entry in section_list:
        for photo in entry["photos"]:
            if photo not in seen:
                seen.add(photo)
                referenced.append(photo)
    for spread in spread_list:
        for placement in spread["placements"]:
            photo = placement["path"]
            if photo not in seen:
                seen.add(photo)
                referenced.append(photo)

    assets = []
    for path in referenced:
        recipe = recipes_by_path.get(path)
        assets.append(
            {
                "path": path,
                "linked_path": path,
                "retouch_status": RETOUCH_NEEDED if path in needed else RETOUCH_NONE,
                "edit_recipe": recipe.as_dict() if recipe is not None else None,
            }
        )

    return {
        "version": MANIFEST_VERSION,
        "spec": _spec_to_dict(spec),
        "sections": section_list,
        "spreads": spread_list,
        "assets": assets,
    }


def export_album(
    out_dir: PathLike,
    spec: AlbumSpec,
    sections: Iterable[tuple[str, Iterable[str]]],
    spreads: Iterable[Spread],
    recipes_by_path: Optional[dict[str, object]] = None,
    retouch_needed: Optional[Iterable[str]] = None,
) -> Path:
    """Build the manifest and write it under ``out_dir``; return the file path."""
    manifest = build_manifest(spec, sections, spreads, recipes_by_path, retouch_needed)
    return JsonProjectExporter().export(manifest, out_dir)


def load_manifest(path: PathLike) -> dict:
    """Read a manifest file (the file itself, or its containing directory)."""
    p = Path(path)
    if p.is_dir():
        p = p / MANIFEST_FILENAME
    if not p.is_file():
        raise AlbumExportError(f"Manifest not found: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AlbumExportError(f"Malformed manifest '{p}': {exc}") from exc


def update_retouch_status(manifest: dict, path: str, status: str) -> dict:
    """
    Set the retouch status of one asset (mutates and returns ``manifest``).

    Raises if the status is unknown or the asset isn't in the manifest.
    """
    if status not in _RETOUCH_STATES:
        raise AlbumExportError(
            f"Unknown retouch status '{status}'; expected one of {sorted(_RETOUCH_STATES)}"
        )
    for asset in manifest.get("assets", []):
        if asset["path"] == path:
            asset["retouch_status"] = status
            return manifest
    raise AlbumExportError(f"No asset with path '{path}' in manifest")


def relink(manifest: dict, path: str, new_linked_path: str) -> dict:
    """
    Point an asset at a new linked file (e.g. the retouched export) without
    changing its identity ``path``. Mutates and returns ``manifest``.
    """
    for asset in manifest.get("assets", []):
        if asset["path"] == path:
            asset["linked_path"] = new_linked_path
            return manifest
    raise AlbumExportError(f"No asset with path '{path}' in manifest")


def pending_retouch(manifest: dict) -> list[str]:
    """Asset paths still flagged ``needed`` (not yet retouched)."""
    return [
        a["path"]
        for a in manifest.get("assets", [])
        if a.get("retouch_status") == RETOUCH_NEEDED
    ]


class LayoutExporter(abc.ABC):
    """Adapter that renders a project manifest to a concrete editable target."""

    @abc.abstractmethod
    def export(self, manifest: dict, out_dir: PathLike) -> Path:
        """Write the project to ``out_dir`` and return the primary artifact path."""


class JsonProjectExporter(LayoutExporter):
    """Reference adapter: writes the manifest as ``album_project.json``."""

    def export(self, manifest: dict, out_dir: PathLike) -> Path:
        out = Path(out_dir)
        try:
            out.mkdir(parents=True, exist_ok=True)
            target = out / MANIFEST_FILENAME
            target.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except OSError as exc:
            raise AlbumExportError(f"Failed to write project to '{out}': {exc}") from exc
        logger.info(
            "Exported album project (%d section(s), %d spread(s), %d asset(s)) to '%s'.",
            len(manifest.get("sections", [])),
            len(manifest.get("spreads", [])),
            len(manifest.get("assets", [])),
            target,
        )
        return target


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #
def _spec_to_dict(spec: AlbumSpec) -> dict:
    data = dataclasses.asdict(spec)
    # Include computed pixel dimensions so a renderer needn't recompute them.
    data["spread_width_px"] = spec.spread_width_px
    data["spread_height_px"] = spec.spread_height_px
    return data


def _spread_to_dict(spread: Spread) -> dict:
    return {
        "index": spread.index,
        "width_px": spread.width_px,
        "height_px": spread.height_px,
        "placements": [
            {
                "path": p.path,
                "frame_px": list(p.frame_px),
                "crop": list(p.crop),
                "fit": getattr(p, "fit", "cover"),
            }
            for p in spread.placements
        ],
    }
