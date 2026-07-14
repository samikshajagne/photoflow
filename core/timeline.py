"""
Capture-time reading and ceremony segmentation for PhotoFlow albums (Phase 1).

The album sequences photos chronologically and splits them into events
("ceremonies"): haldi, mehndi, the ceremony itself, the reception, and so on.
Weddings are naturally separated by gaps in time, so this module:

1. reads each photo's capture time (EXIF ``DateTimeOriginal``, falling back to
   the file's modification time when EXIF is absent), and
2. segments a set of timed photos into events wherever the gap between
   consecutive shots exceeds a threshold.

It performs no image analysis -- just timestamps and grouping -- so it stays
light and is trivially testable. EXIF reading is isolated so segmentation can be
tested with explicit datetimes.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Union

from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]

# EXIF tag id for DateTimeOriginal ("when the photo was taken").
_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"

# Default gap (seconds) above which a new event/ceremony begins. 45 minutes is a
# reasonable default for distinct wedding events; tune per workflow.
DEFAULT_EVENT_GAP_SECONDS: float = 45 * 60


@dataclasses.dataclass(frozen=True)
class TimedPhoto:
    """A photo paired with its capture time."""

    path: str
    captured_at: datetime


@dataclasses.dataclass(frozen=True)
class EventSegment:
    """
    A contiguous run of photos with no large time gap -- one ceremony/event.

    Attributes:
        index: Zero-based event order.
        photos: Photo paths in chronological order.
        start: Capture time of the first photo.
        end: Capture time of the last photo.
    """

    index: int
    photos: tuple[str, ...]
    start: datetime
    end: datetime


def read_capture_time(path: PathLike) -> datetime:
    """
    Return a photo's capture time.

    Prefers EXIF ``DateTimeOriginal``; falls back to the file's modification
    time when EXIF is missing or unreadable, so every file gets a usable
    timestamp.
    """
    exif_time = _read_exif_datetime(Path(path))
    if exif_time is not None:
        return exif_time
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        # Last resort: epoch, so the photo still sorts deterministically.
        return datetime.fromtimestamp(0)
    return datetime.fromtimestamp(mtime)


def _read_exif_datetime(path: Path) -> Optional[datetime]:
    """Read EXIF DateTimeOriginal, or ``None`` if unavailable/unparseable."""
    try:
        from PIL import Image  # lazy: keep Pillow import out of module load
    except ImportError:  # pragma: no cover - Pillow is a hard dep elsewhere
        return None
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            value = exif.get(_EXIF_DATETIME_ORIGINAL)
            if value is None:
                # Some files store it only in the Exif IFD.
                ifd = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
                value = ifd.get(_EXIF_DATETIME_ORIGINAL)
            if not value:
                return None
            try:
                return datetime.strptime(str(value).strip(), _EXIF_DATETIME_FORMAT)
            except ValueError:
                # EXIF timestamp is present but malformed; this is worth noting
                # because the mtime fallback can scramble event ordering.
                logger.debug(
                    "Malformed EXIF DateTimeOriginal '%s' for '%s'; "
                    "falling back to file mtime.",
                    value,
                    path,
                )
                return None
    except Exception:  # noqa: BLE001 - any decode/IO issue -> mtime fallback
        return None


def build_timeline(paths: Iterable[PathLike]) -> list[TimedPhoto]:
    """Read capture times for ``paths`` and return them sorted chronologically."""
    timed = [TimedPhoto(path=str(p), captured_at=read_capture_time(p)) for p in paths]
    timed.sort(key=lambda t: (t.captured_at, t.path))
    return timed


def segment_events(
    timed_photos: Iterable[TimedPhoto],
    gap_seconds: float = DEFAULT_EVENT_GAP_SECONDS,
) -> list[EventSegment]:
    """
    Split timed photos into events wherever the gap exceeds ``gap_seconds``.

    Args:
        timed_photos: Photos with capture times (any order; sorted internally).
        gap_seconds: Minimum gap between consecutive photos that starts a new
            event. Must be >= 0.

    Returns:
        Events in chronological order. Empty input yields ``[]``.

    Raises:
        ValueError: if ``gap_seconds`` is negative.
    """
    if gap_seconds < 0:
        raise ValueError(f"gap_seconds must be >= 0, got {gap_seconds}")

    ordered = sorted(timed_photos, key=lambda t: (t.captured_at, t.path))
    if not ordered:
        return []

    gap = timedelta(seconds=gap_seconds)
    segments: list[EventSegment] = []
    current: list[TimedPhoto] = [ordered[0]]

    for previous, photo in zip(ordered, ordered[1:]):
        if photo.captured_at - previous.captured_at > gap:
            segments.append(_freeze_segment(len(segments), current))
            current = [photo]
        else:
            current.append(photo)
    segments.append(_freeze_segment(len(segments), current))

    logger.info(
        "Segmented %d photo(s) into %d event(s) (gap=%.0fs).",
        len(ordered),
        len(segments),
        gap_seconds,
    )
    return segments


def _freeze_segment(index: int, photos: list[TimedPhoto]) -> EventSegment:
    return EventSegment(
        index=index,
        photos=tuple(p.path for p in photos),
        start=photos[0].captured_at,
        end=photos[-1].captured_at,
    )
