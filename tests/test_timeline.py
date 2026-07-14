"""
Unit tests for core.timeline.

Segmentation is pure and tested with explicit datetimes. Capture-time reading
is tested via the mtime fallback (writing real EXIF is environment-fragile).
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.timeline import (
    EventSegment,
    TimedPhoto,
    build_timeline,
    read_capture_time,
    segment_events,
)


def _img(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((16, 16, 3), 127, np.uint8))
    return path


def _timed(path: str, base: datetime, minutes: float) -> TimedPhoto:
    return TimedPhoto(path=path, captured_at=base + timedelta(minutes=minutes))


# --------------------------------------------------------------------------- #
# Capture time
# --------------------------------------------------------------------------- #
def test_read_capture_time_falls_back_to_mtime(tmp_path: Path):
    path = _img(tmp_path / "a.jpg")
    when = datetime(2026, 6, 1, 10, 30, 0)
    os.utime(path, (when.timestamp(), when.timestamp()))

    got = read_capture_time(path)
    assert isinstance(got, datetime)
    assert abs((got - when).total_seconds()) < 2


def test_read_capture_time_missing_file_is_epoch():
    got = read_capture_time("/no/such/file.jpg")
    assert got == datetime.fromtimestamp(0)


# --------------------------------------------------------------------------- #
# Segmentation
# --------------------------------------------------------------------------- #
def test_empty_segmentation():
    assert segment_events([]) == []


def test_single_event_when_gaps_are_small():
    base = datetime(2026, 6, 1, 9, 0, 0)
    photos = [_timed(f"/p/{i}.jpg", base, i) for i in range(5)]  # 1 min apart
    events = segment_events(photos, gap_seconds=45 * 60)
    assert len(events) == 1
    assert events[0].index == 0
    assert len(events[0].photos) == 5


def test_splits_on_large_gap():
    base = datetime(2026, 6, 1, 9, 0, 0)
    morning = [_timed(f"/m/{i}.jpg", base, i) for i in range(3)]       # 9:00-9:02
    evening = [_timed(f"/e/{i}.jpg", base, 180 + i) for i in range(2)]  # ~12:00
    events = segment_events(morning + evening, gap_seconds=45 * 60)

    assert len(events) == 2
    assert [e.index for e in events] == [0, 1]
    assert len(events[0].photos) == 3
    assert len(events[1].photos) == 2
    # Chronological start/end bounds.
    assert events[0].start <= events[0].end < events[1].start


def test_unsorted_input_is_sorted_before_segmenting():
    base = datetime(2026, 6, 1, 9, 0, 0)
    photos = [
        _timed("/p/late.jpg", base, 200),
        _timed("/p/early.jpg", base, 0),
        _timed("/p/mid.jpg", base, 1),
    ]
    events = segment_events(photos, gap_seconds=45 * 60)
    assert len(events) == 2
    assert events[0].photos == ("/p/early.jpg", "/p/mid.jpg")
    assert events[1].photos == ("/p/late.jpg",)


def test_negative_gap_raises():
    with pytest.raises(ValueError):
        segment_events([], gap_seconds=-1)


def test_build_timeline_sorts_chronologically(tmp_path: Path):
    older = _img(tmp_path / "older.jpg")
    newer = _img(tmp_path / "newer.jpg")
    t_old = datetime(2026, 6, 1, 8, 0, 0).timestamp()
    t_new = datetime(2026, 6, 1, 12, 0, 0).timestamp()
    os.utime(older, (t_old, t_old))
    os.utime(newer, (t_new, t_new))

    timeline = build_timeline([str(newer), str(older)])
    assert [Path(t.path).name for t in timeline] == ["older.jpg", "newer.jpg"]
    assert all(isinstance(t, TimedPhoto) for t in timeline)
