"""
Photo content analysis for subject-aware album layout (WS 3.2.1).

Classifies each candidate photo into a *composition type* — portrait, group,
detail, landscape, etc. — from cheap signals already available after the analysis
pipeline: the relative face boxes and the image aspect ratio. No pixel decode is
required for the classification itself, so this is fast enough to run over a whole
shoot; an optional image loader can refine subject isolation when wanted.

The result feeds :mod:`core.album.slot_matcher`, which assigns photos to spread
slots by how well their composition matches each slot's ideal.
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Sequence, Tuple

RelRect = Tuple[float, float, float, float]

# Composition type constants.
PORTRAIT = "portrait"
FULL_BODY = "full_body"
GROUP = "group"
LARGE_GROUP = "large_group"
DETAIL = "detail"
ENVIRONMENTAL = "environmental"
LANDSCAPE = "landscape"

COMPOSITION_TYPES = frozenset(
    {PORTRAIT, FULL_BODY, GROUP, LARGE_GROUP, DETAIL, ENVIRONMENTAL, LANDSCAPE}
)

# Orientation constants.
ORIENT_PORTRAIT = "portrait"
ORIENT_LANDSCAPE = "landscape"
ORIENT_SQUARE = "square"

# Tuning thresholds (fractions of image area / counts).
_PORTRAIT_MIN_FACE = 0.08     # one face this large -> a portrait
_ENVIRONMENTAL_MIN_FACE = 0.02  # smaller but present -> subject in a scene
_FULL_BODY_MAX_FACE = 0.06    # small face + tall frame -> full-body portrait
_LARGE_GROUP_MIN = 5          # this many faces -> a large group
_SQUARE_TOL = 0.05            # |aspect - 1| within this -> "square"


@dataclasses.dataclass(frozen=True)
class PhotoContent:
    """
    Structured description of a photo's composition.

    Attributes:
        face_count: Number of detected faces.
        dominant_face_frac: Largest face's area as a fraction of the image (0–1).
        face_centroids: ``(cx, cy)`` centroid of each face, in ``[0, 1]``.
        composition_type: One of :data:`COMPOSITION_TYPES`.
        orientation: ``"portrait"``, ``"landscape"`` or ``"square"``.
        aspect_ratio: width / height.
    """

    face_count: int
    dominant_face_frac: float
    face_centroids: Tuple[Tuple[float, float], ...]
    composition_type: str
    orientation: str
    aspect_ratio: float


def orientation_of(aspect_ratio: float) -> str:
    """Classify an aspect ratio into portrait / landscape / square."""
    if aspect_ratio <= 0:
        return ORIENT_SQUARE
    if abs(aspect_ratio - 1.0) <= _SQUARE_TOL:
        return ORIENT_SQUARE
    return ORIENT_LANDSCAPE if aspect_ratio > 1.0 else ORIENT_PORTRAIT


def classify_composition(
    face_boxes: Sequence[RelRect], aspect_ratio: float
) -> str:
    """
    Classify composition from face boxes + aspect ratio (roadmap WS 3.2.1 rules).

    - 0 faces                -> ``landscape``
    - 5+ faces               -> ``large_group``
    - 2–4 faces              -> ``group``
    - 1 large face           -> ``portrait``
    - 1 small face, tall     -> ``full_body``
    - 1 mid/small face       -> ``environmental``
    - 1 tiny face            -> ``detail``
    """
    n = len(face_boxes)
    if n == 0:
        return LANDSCAPE
    if n >= _LARGE_GROUP_MIN:
        return LARGE_GROUP
    if n >= 2:
        return GROUP

    # Exactly one face: size + orientation decide the flavour.
    frac = _box_area(face_boxes[0])
    orient = orientation_of(aspect_ratio)
    if frac >= _PORTRAIT_MIN_FACE:
        return PORTRAIT
    if frac <= _FULL_BODY_MAX_FACE and orient == ORIENT_PORTRAIT:
        return FULL_BODY
    if frac >= _ENVIRONMENTAL_MIN_FACE:
        return ENVIRONMENTAL
    return DETAIL


def analyze(aspect_ratio: float, face_boxes: Sequence[RelRect] = ()) -> PhotoContent:
    """
    Build a :class:`PhotoContent` from an aspect ratio and relative face boxes.

    ``face_boxes`` come straight from the pipeline's cached detections. This does
    no image decode; pass the header-only aspect ratio the layout selector
    already computes.
    """
    boxes = tuple(face_boxes)
    dominant = max((_box_area(b) for b in boxes), default=0.0)
    centroids = tuple((b[0] + b[2] / 2.0, b[1] + b[3] / 2.0) for b in boxes)
    return PhotoContent(
        face_count=len(boxes),
        dominant_face_frac=dominant,
        face_centroids=centroids,
        composition_type=classify_composition(boxes, aspect_ratio),
        orientation=orientation_of(aspect_ratio),
        aspect_ratio=aspect_ratio,
    )


def _box_area(box: RelRect) -> float:
    """Relative area of a face box, clamped to ``[0, 1]``."""
    _, _, w, h = box
    return max(0.0, min(1.0, w)) * max(0.0, min(1.0, h))
