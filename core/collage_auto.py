"""
Smart auto-build for collages: pick the photos, or group them by person.

Two jobs a studio would otherwise do by hand:

* :func:`select_best_photos` -- point it at a folder and it ranks every photo
  with PhotoFlow's existing quality pipeline (blur/sharpness, exposure,
  contrast, subject-aware sharpness on faces) and returns the best N, optionally
  spread across the shoot so a collage doesn't end up as six near-identical
  frames from one burst.
* :func:`group_photos_by_person` -- clusters faces into people so you can build
  *one collage per guest*, which is the kind of thing no competitor's collage
  tool does and which reuses the album side's identity work.

Everything degrades gracefully. MediaPipe, an embedding backend and SciPy are
all optional; without them the functions still return sensible results (quality
ranking without face awareness, or a single "everyone" group) rather than
raising. That matters because these are convenience features -- they must never
be the reason a studio can't build a collage.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional, Sequence, Union

from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]

# Default spread: when picking N photos, consider this multiple of N by quality
# first, then spread the final choice across the shoot's timeline.
_CANDIDATE_MULTIPLIER = 3


class CollageAutoError(Exception):
    """Raised when auto-selection cannot proceed (bad folder, no images)."""


@dataclasses.dataclass
class ScoredPhoto:
    """One candidate photo with everything auto-build needs to know."""

    path: Path
    quality: float
    face_count: int
    face_boxes: tuple[tuple[float, float, float, float], ...] = ()

    @property
    def has_face(self) -> bool:
        return self.face_count > 0


def score_folder(
    folder: PathLike,
    config=None,
    limit: int = 0,
    require_faces: bool = False,
) -> list[ScoredPhoto]:
    """
    Score every image in ``folder`` with the existing quality pipeline.

    Args:
        folder: Directory to scan (non-recursive behaviour follows
            ``ImageScanner``'s configuration).
        config: An ``AppConfig``; loaded via ``utils.config.load_config`` if
            omitted.
        limit: Stop after this many images (``0`` = no cap). Useful for a quick
            pass over a huge folder.
        require_faces: Drop photos with no detected face. Ignored when face
            detection is unavailable, since it would otherwise drop everything.

    Returns:
        Scored photos in folder order (not yet ranked).

    Raises:
        CollageAutoError: if the folder is missing or contains no images.
    """
    from utils.config import load_config

    root = Path(folder)
    if not root.is_dir():
        raise CollageAutoError(f"Not a folder: {root}")
    config = config or load_config()

    from core.blur_detector import BlurDetector
    from core.quality_scorer import QualityScorer
    from core.scanner import ImageScanner

    paths = ImageScanner.from_config(config).scan(root)
    if limit:
        paths = paths[:limit]
    if not paths:
        raise CollageAutoError(f"No supported images found in {root}")

    blur = BlurDetector.from_config(config)
    scorer = QualityScorer.from_config(config)
    detector = _maybe_face_detector(config)

    scored: list[ScoredPhoto] = []
    faces_ever_found = False
    for path in paths:
        try:
            blur_score = blur.detect(path).blur_score
        except Exception as exc:  # noqa: BLE001 - skip unreadable files
            logger.info("Auto-collage: skipping '%s' (%s).", path, exc)
            continue

        regions: tuple = ()
        if detector is not None:
            try:
                regions = tuple(detector.detect(path).regions)
            except Exception as exc:  # noqa: BLE001 - faces are optional
                logger.debug("Auto-collage: face detection failed on %s: %s", path, exc)
        faces_ever_found = faces_ever_found or bool(regions)

        try:
            result = scorer.score(
                path,
                blur_score=blur_score,
                faces_detected=bool(regions),
                face_count=len(regions),
                face_regions=regions,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("Auto-collage: could not score '%s' (%s).", path, exc)
            continue

        scored.append(
            ScoredPhoto(
                path=path,
                quality=float(result.quality_score),
                face_count=len(regions),
                face_boxes=regions,
            )
        )

    if require_faces and faces_ever_found:
        scored = [s for s in scored if s.has_face]
    elif require_faces:
        logger.warning(
            "Auto-collage: face detection found nothing at all (MediaPipe "
            "missing?), so 'require faces' was ignored rather than discarding "
            "every photo."
        )

    if not scored:
        raise CollageAutoError(f"No usable images in {root}")
    return scored


def _maybe_face_detector(config):
    """A FaceDetector, or ``None`` when the backend isn't installed."""
    try:
        from core.face_detector import FaceDetector

        return FaceDetector.from_config(config)
    except Exception as exc:  # noqa: BLE001 - optional dependency
        logger.info("Auto-collage: face detection unavailable (%s).", exc)
        return None


def select_best_photos(
    folder: PathLike,
    count: int = 9,
    config=None,
    require_faces: bool = False,
    spread: bool = True,
    limit: int = 0,
) -> list[ScoredPhoto]:
    """
    Pick the ``count`` best photos from ``folder``.

    Ranks by quality, then -- when ``spread`` is set -- takes the best photo
    from each of ``count`` equal slices of the (filename-ordered) shoot instead
    of the global top N. Without that, a collage tends to fill up with several
    near-identical frames from whichever burst happened to be sharpest.

    Raises:
        CollageAutoError: for a bad folder, no usable images, or ``count < 1``.
    """
    if count < 1:
        raise CollageAutoError(f"count must be >= 1, got {count}")

    scored = score_folder(folder, config=config, limit=limit, require_faces=require_faces)
    if len(scored) <= count:
        return sorted(scored, key=lambda s: -s.quality)

    if not spread:
        return sorted(scored, key=lambda s: -s.quality)[:count]

    # Keep a strong shortlist, then spread the final pick across the shoot.
    shortlist = sorted(scored, key=lambda s: -s.quality)[
        : max(count, count * _CANDIDATE_MULTIPLIER)
    ]
    shortlist_in_order = [s for s in scored if s in shortlist]

    chosen: list[ScoredPhoto] = []
    total = len(shortlist_in_order)
    for slot in range(count):
        start = (slot * total) // count
        end = max(start + 1, ((slot + 1) * total) // count)
        window = shortlist_in_order[start:end]
        if not window:
            continue
        best = max(window, key=lambda s: s.quality)
        if best not in chosen:
            chosen.append(best)

    # Top up from the shortlist if slices collided.
    for candidate in shortlist:
        if len(chosen) >= count:
            break
        if candidate not in chosen:
            chosen.append(candidate)
    return chosen[:count]


def group_photos_by_person(
    photos: Sequence[ScoredPhoto],
    config=None,
    min_photos: int = 3,
    embed_backend=None,
) -> dict[str, list[ScoredPhoto]]:
    """
    Group photos by who appears in them, for one-collage-per-guest.

    Returns a mapping of ``"Person 1"``-style labels to their photos, keeping
    only groups with at least ``min_photos``. Falls back to a single
    ``{"Everyone": [...]}`` group when face embedding isn't available -- so
    callers get something usable instead of an exception.

    Args:
        photos: Scored photos (their ``face_boxes`` are reused, avoiding a
            second detection pass).
        config: An ``AppConfig``; loaded if omitted.
        min_photos: Drop people who appear in fewer photos than this.
        embed_backend: An embedding backend callable. Defaults to trying
            SFace (permissively licensed) and then InsightFace.
    """
    from utils.config import load_config

    config = config or load_config()
    backend = embed_backend or _default_embed_backend()
    if backend is None:
        logger.warning(
            "Auto-collage: no face-embedding backend available, so photos "
            "can't be grouped by person; returning one combined group."
        )
        return {"Everyone": list(photos)}

    try:
        from core.face_embedder import FaceEmbedder
        from core.person_cluster import FaceRef, PersonClusterer
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auto-collage: clustering unavailable (%s).", exc)
        return {"Everyone": list(photos)}

    embedder = FaceEmbedder.from_config(config, embed_backend=backend)
    refs: list[FaceRef] = []
    owner: dict[tuple[str, int], ScoredPhoto] = {}

    for photo in photos:
        if not photo.face_boxes:
            continue
        try:
            embeddings = embedder.embed(photo.path, list(photo.face_boxes))
        except Exception as exc:  # noqa: BLE001 - one bad photo shouldn't stop the run
            logger.debug("Auto-collage: embedding failed for %s: %s", photo.path, exc)
            continue
        for emb in embeddings:
            key = (str(photo.path), int(emb.face_index))
            owner[key] = photo
            refs.append(
                FaceRef(
                    image_path=str(photo.path),
                    face_index=int(emb.face_index),
                    vector=emb.vector,
                )
            )

    if not refs:
        return {"Everyone": list(photos)}

    try:
        clusters = PersonClusterer(
            distance_max=_threshold_for(backend), min_cluster_size=1
        ).cluster(refs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auto-collage: clustering failed (%s).", exc)
        return {"Everyone": list(photos)}

    groups: dict[str, list[ScoredPhoto]] = {}
    for index, cluster in enumerate(clusters, start=1):
        members: list[ScoredPhoto] = []
        for face in cluster.faces:
            photo = owner.get((face.image_path, face.face_index))
            if photo is not None and photo not in members:
                members.append(photo)
        if len(members) >= min_photos:
            groups[f"Person {index}"] = sorted(members, key=lambda s: -s.quality)

    return groups or {"Everyone": list(photos)}


def _default_embed_backend():
    """Prefer the permissively-licensed SFace backend, then InsightFace."""
    try:
        from core.sface_backend import build_sface_backend

        return build_sface_backend()
    except Exception as exc:  # noqa: BLE001
        logger.info("Auto-collage: SFace backend unavailable (%s).", exc)
    try:
        import insightface  # noqa: F401

        from core.insightface_backend import build_insightface_backend

        return build_insightface_backend()
    except Exception as exc:  # noqa: BLE001
        logger.info("Auto-collage: InsightFace backend unavailable (%s).", exc)
    return None


def _threshold_for(backend) -> float:
    """
    Clustering threshold matching the backend in use.

    The cosine threshold belongs to the embedding model, not to clustering, so
    picking the wrong one silently merges or fragments people (see
    ``core.person_cluster``).
    """
    from core.person_cluster import distance_max_for_backend

    name = getattr(backend, "__module__", "") or ""
    if "sface" in name:
        return distance_max_for_backend("sface")
    return distance_max_for_backend("arcface")


def to_collage_photos(photos: Sequence[ScoredPhoto], max_dim: int = 0) -> list:
    """
    Load ``photos`` into :class:`core.collage.CollagePhoto` objects.

    ``max_dim`` (when > 0) downscales each image on load, which is what a UI
    preview should use to stay responsive and bounded in memory.
    """
    from PIL import Image

    from core.collage import CollagePhoto

    loaded = []
    for scored in photos:
        try:
            with Image.open(scored.path) as opened:
                opened.load()
                image = opened.convert("RGB")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Auto-collage: could not open '%s' (%s).", scored.path, exc)
            continue
        if max_dim > 0:
            image.thumbnail((max_dim, max_dim))
        loaded.append(
            CollagePhoto(image=image, face_boxes=scored.face_boxes, path=scored.path)
        )
    return loaded
