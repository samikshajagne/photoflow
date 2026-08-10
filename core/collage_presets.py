"""
Named collage presets: save a look once, reuse it every time.

A studio settles on two or three house styles and then wants them back with one
click rather than re-dialling six controls per job. A preset captures every
*visual* choice (layout, theme, size, spacing/border/corners, background, shape,
title text, print marks) but deliberately **not** the photos -- it's a style,
not a document.

Stored as one JSON file so presets are portable between machines and readable by
hand. Unknown keys in a stored preset are ignored on load and missing ones fall
back to defaults, so a preset written by an older build keeps working.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Optional, Union

from utils.logger import get_logger
from utils.paths import user_data_dir

logger = get_logger(__name__)

PathLike = Union[str, Path]

# Presets live in the per-user writable data directory, NOT beside the
# application. Two distinct problems that solves: a relative path would depend
# on the working directory (a desktop shortcut and a terminal run would
# disagree), and an installed copy under Program Files isn't writable at all,
# so saving a preset would fail for every real customer. See utils.paths.
DEFAULT_PRESET_FILE = user_data_dir() / "collage_presets.json"
_SCHEMA_VERSION = 1


class PresetError(Exception):
    """Raised when presets cannot be read, written or parsed."""


@dataclasses.dataclass
class CollagePreset:
    """
    A reusable collage style.

    Every field is a plain scalar or tuple so the whole thing round-trips
    through JSON without custom encoders.
    """

    name: str
    layout: Optional[str] = None  # None = "Auto (pick for me)"
    theme: str = "Classic White"
    size_preset: str = "Instagram Square (1080x1080)"
    spacing: int = 14
    border: int = 0
    corner: int = 6
    background_style: str = "solid"
    background_color: tuple[int, int, int] = (255, 255, 255)
    background_color2: tuple[int, int, int] = (225, 232, 240)
    background_darken: float = 0.25
    shape: str = "none"
    shape_text: str = ""
    title: str = ""
    title_position: str = "bottom-center"
    title_size: float = 0.06
    title_color: tuple[int, int, int] = (255, 255, 255)
    bleed_frac: float = 0.0
    trim_marks: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        # JSON has no tuples; store colours as lists and restore on load.
        for key in ("background_color", "background_color2", "title_color"):
            data[key] = list(data[key])
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CollagePreset":
        """
        Build a preset from stored data, tolerating old/partial files.

        Unknown keys are dropped and missing ones keep their defaults, so a
        preset saved by a different build still loads.
        """
        if not isinstance(data, dict):
            raise PresetError(f"Preset must be an object, got {type(data).__name__}")
        name = str(data.get("name") or "").strip()
        if not name:
            raise PresetError("Preset is missing a name")

        fields = {f.name for f in dataclasses.fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in fields:
                continue
            if key in ("background_color", "background_color2", "title_color"):
                try:
                    r, g, b = (int(c) for c in value)
                    value = (r, g, b)
                except Exception:  # noqa: BLE001 - keep the default instead
                    logger.warning("Preset %r: bad colour for %s; using default.", name, key)
                    continue
            kwargs[key] = value
        kwargs["name"] = name
        return cls(**kwargs)


def load_presets(path: PathLike = DEFAULT_PRESET_FILE) -> list[CollagePreset]:
    """
    Read all presets from ``path``.

    A missing file is not an error -- it just means no presets yet, so this
    returns ``[]``. Individual malformed entries are skipped with a warning
    rather than failing the whole load.

    Raises:
        PresetError: if the file exists but isn't readable/valid JSON, or its
            top level isn't the expected shape.
    """
    file = Path(path)
    if not file.exists():
        return []
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PresetError(f"Could not read presets from '{file}': {exc}") from exc

    if isinstance(raw, dict):
        entries = raw.get("presets", [])
    elif isinstance(raw, list):  # tolerate a bare list
        entries = raw
    else:
        raise PresetError(f"Unexpected preset file structure in '{file}'")

    presets: list[CollagePreset] = []
    for entry in entries:
        try:
            presets.append(CollagePreset.from_dict(entry))
        except PresetError as exc:
            logger.warning("Skipping a malformed preset in '%s': %s", file, exc)
    return presets


def save_presets(
    presets: list[CollagePreset], path: PathLike = DEFAULT_PRESET_FILE
) -> Path:
    """
    Write ``presets`` to ``path``, replacing whatever was there.

    Writes to a temporary file and moves it into place, so an interrupted save
    can't leave a half-written file that loses every stored preset.
    """
    file = Path(path)
    payload = {
        "version": _SCHEMA_VERSION,
        "presets": [preset.to_dict() for preset in presets],
    }
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(file.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(file)
    except OSError as exc:
        raise PresetError(f"Could not save presets to '{file}': {exc}") from exc
    return file


def upsert_preset(
    preset: CollagePreset, path: PathLike = DEFAULT_PRESET_FILE
) -> list[CollagePreset]:
    """
    Add ``preset``, replacing any existing one with the same name.

    Returns the full updated list, so callers can refresh a dropdown without
    re-reading the file.
    """
    presets = [p for p in load_presets(path) if p.name != preset.name]
    presets.append(preset)
    presets.sort(key=lambda p: p.name.lower())
    save_presets(presets, path)
    return presets


def delete_preset(name: str, path: PathLike = DEFAULT_PRESET_FILE) -> list[CollagePreset]:
    """Remove the preset called ``name`` (a no-op if absent)."""
    presets = load_presets(path)
    remaining = [p for p in presets if p.name != name]
    if len(remaining) != len(presets):
        save_presets(remaining, path)
    return remaining
