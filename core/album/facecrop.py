"""
Shared face-safe cover-crop geometry.

Both the layout *engine* (:mod:`core.album.layout`, which pre-computes a crop for
each placement) and the designed-template *renderer* (:mod:`core.album.template`,
which crops each photo to fill a shaped slot) must crop a source photo to a target
aspect ratio without slicing through faces. This module holds that geometry once,
as pure functions over relative coordinates, so the two paths can never drift apart.

All rectangles are relative ``(x, y, w, h)`` with every value in ``[0, 1]`` of the
source image. Face boxes are the relative bounding boxes produced by face
detection (:class:`core.face_detector.FaceResult` regions).
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

RelRect = Tuple[float, float, float, float]

_EPS = 1e-6

# How far to expand each face box before protecting it, as fractions of the face
# box's own size: headroom above, torso below, and a little horizontal slack. Kept
# identical to the layout engine's historical values so behaviour is unchanged.
_PAD_UP = 0.45
_PAD_DOWN = 1.4
_PAD_SIDE = 0.25


def pad_face_boxes(
    face_boxes: Tuple[RelRect, ...],
    up: float = _PAD_UP,
    down: float = _PAD_DOWN,
    side: float = _PAD_SIDE,
) -> Tuple[RelRect, ...]:
    """
    Expand face boxes into head-and-shoulders "safe" regions, clamped to ``[0, 1]``.

    Adds headroom above each face and generous room below (torso) plus a little
    horizontal slack. Keeping this larger region inside the crop avoids the classic
    "forehead/chin/legs cut off" look.
    """
    padded: list[RelRect] = []
    for x, y, w, h in face_boxes:
        nx = max(0.0, x - w * side)
        ny = max(0.0, y - h * up)
        nx2 = min(1.0, x + w * (1.0 + side))
        ny2 = min(1.0, y + h * (1.0 + down))
        padded.append((nx, ny, nx2 - nx, ny2 - ny))
    return tuple(padded)


def face_safe_offset(
    default_offset: float,
    crop_size: float,
    face_boxes: Tuple[RelRect, ...],
    axis: int,
    pull_low: Optional[bool] = None,
) -> float:
    """
    Pick the crop-window offset on one axis so faces stay visible.

    Args:
        default_offset: The centered offset to use when there are no faces or no
            shifting is needed.
        crop_size: The crop window's extent on this axis, in ``[0, 1]``.
        face_boxes: Source-relative rectangles to keep inside the window (already
            padded, if head-and-shoulders protection is wanted).
        axis: 0 for x, 1 for y.
        pull_low: When ``True`` bias toward the low end, ``False`` toward the high
            end, ``None`` to center on the faces' bounding span.

    Returns:
        An offset in ``[0, max(0, 1 - crop_size)]`` that contains the faces' span
        when it fits, else centers on it.
    """
    max_offset = max(0.0, 1.0 - crop_size)
    if not face_boxes or crop_size >= 1.0 - _EPS:
        return min(max(default_offset, 0.0), max_offset)

    starts = [box[axis] for box in face_boxes]
    ends = [box[axis] + box[axis + 2] for box in face_boxes]
    lo = min(starts)
    hi = max(ends)
    span = hi - lo

    if span <= crop_size + _EPS:
        # All faces fit: the window may sit anywhere keeping [lo, hi] inside.
        low_bound = max(0.0, hi - crop_size)
        high_bound = min(max_offset, lo)
        if low_bound > high_bound:
            low_bound, high_bound = (
                min(low_bound, high_bound),
                max(low_bound, high_bound),
            )
        if pull_low is True:
            offset = low_bound
        elif pull_low is False:
            offset = high_bound
        else:
            offset = (low_bound + high_bound) / 2.0
    else:
        # Faces can't all fit: center the window on their bounding span.
        center = (lo + hi) / 2.0
        offset = center - crop_size / 2.0

    return min(max(offset, 0.0), max_offset)


def face_safe_cover_crop(
    photo_ar: float,
    frame_ar: float,
    face_boxes: Tuple[RelRect, ...] = (),
    pull_low_x: Optional[bool] = None,
) -> RelRect:
    """
    Relative cover-fit crop of a ``photo_ar`` photo into a ``frame_ar`` slot,
    shifted to keep (padded) faces visible.

    "Cover" fills the slot entirely, cropping whichever dimension overflows. A
    wide-vs-slot photo keeps full height and a horizontal slice; a tall photo keeps
    full width and a vertical slice. The window is then shifted within ``[0, 1]`` to
    keep every padded face box inside when possible.

    Args:
        photo_ar: Source photo aspect ratio (width / height).
        frame_ar: Target slot aspect ratio (width / height).
        face_boxes: Relative face rectangles over the source (unpadded).
        pull_low_x: Horizontal bias (e.g. pull away from a spread gutter): ``True``
            toward the left/outer edge, ``False`` toward the right, ``None`` center.

    Returns:
        ``(crop_x, crop_y, crop_w, crop_h)`` in source-relative units.
    """
    photo_ar = photo_ar if photo_ar > 0 else 1.0
    frame_ar = frame_ar if frame_ar > 0 else 1.0

    if photo_ar > frame_ar:
        crop_h = 1.0
        crop_w = frame_ar / photo_ar
    else:
        crop_w = 1.0
        crop_h = photo_ar / frame_ar

    crop_w = min(crop_w, 1.0)
    crop_h = min(crop_h, 1.0)

    crop_x = (1.0 - crop_w) / 2.0
    crop_y = (1.0 - crop_h) / 2.0

    safe = pad_face_boxes(tuple(face_boxes))
    crop_x = face_safe_offset(crop_x, crop_w, safe, axis=0, pull_low=pull_low_x)
    crop_y = face_safe_offset(crop_y, crop_h, safe, axis=1, pull_low=None)

    return (crop_x, crop_y, crop_w, crop_h)


# --------------------------------------------------------------------------- #
# Landmark-based face box (Implementation Plan — Component 4)
# --------------------------------------------------------------------------- #
Point = Tuple[float, float]

# Face proportions relative to the inter-eye distance, used to grow the 5 points
# into a full head box (crown to chin, ear to ear). Human faces are ~2x the
# eye-span wide and the crown/chin sit roughly these multiples away.
_FACE_WIDTH_PER_EYE = 2.2
_CROWN_PER_EYE = 1.5      # above the eye line
_CHIN_PER_EYE = 2.6       # below the eye line


def face_box_from_landmarks(landmarks: Sequence[Point]) -> Optional[RelRect]:
    """
    Derive a full-head face box from 5-point landmarks, or ``None`` if unusable.

    The landmarks are ``[left_eye, right_eye, nose_tip, mouth_left, mouth_right]``
    in relative ``[0, 1]`` coordinates (as produced by the Vision Brain). Using
    the **eye midpoint** as the anchor and the inter-eye distance as the scale
    gives a far more reliable face centre than a raw detector box — and the box
    is grown to include the crown and chin so a cover crop never clips the top of
    the head or the jaw.

    Returns a relative ``(x, y, w, h)`` clamped to ``[0, 1]``.
    """
    if not landmarks or len(landmarks) < 2:
        return None
    (lx, ly), (rx, ry) = landmarks[0], landmarks[1]
    cx = (lx + rx) / 2.0
    cy = (ly + ry) / 2.0
    eye_dist = ((rx - lx) ** 2 + (ry - ly) ** 2) ** 0.5
    if eye_dist <= 0:
        return None

    half_w = eye_dist * _FACE_WIDTH_PER_EYE / 2.0
    top = cy - eye_dist * _CROWN_PER_EYE
    bottom = cy + eye_dist * _CHIN_PER_EYE

    x0 = max(0.0, cx - half_w)
    y0 = max(0.0, top)
    x1 = min(1.0, cx + half_w)
    y1 = min(1.0, bottom)
    w = x1 - x0
    h = y1 - y0
    if w <= 0 or h <= 0:
        return None
    return (x0, y0, w, h)
