"""
Face beautification for passport photos.

Four effects, each independently toggleable via intensity (0..1):
    - **Skin smoothing**: blends a bilateral filter into skin-toned pixels
      only, so eyes/brows/lips/nostrils stay sharp without needing a
      landmark model.
    - **Brightness/color auto-correct**: reuses ``core.auto_edit``'s
      white-balance/exposure/contrast engine (the same one album photos get),
      minus its crop/straighten suggestions -- the passport crop UI already
      owns composition.
    - **Background whitening**: cuts the subject out (via
      ``core.album.face_segmenter``, which prefers a real rembg matte and
      falls back to a head-and-shoulders ellipse) and blends the backdrop
      toward a soft white.
    - **Teeth/eye whitening**: brightens+desaturates bright, low-saturation
      pixels within rough mouth/eye sub-regions.

Deliberately operates on the *already-cropped* passport photo
(``core.passport_photo.crop_and_resize``'s output), not the original source
photo, for two reasons:

1. Speed: a cropped passport photo is a few hundred pixels per side at
   typical print DPI, so even the heavier effects (bilateral filtering,
   rembg background segmentation) run fast enough for live slider preview,
   no matter how large the original camera photo was.
2. Consistency: every correctly-cropped passport photo follows the same
   head-centered composition (see ``core.passport_photo.auto_crop_box``'s
   eye-line/head-height conventions -- head fills ~65% of the frame height,
   eyes sit ~45% down), so one canonical face region (``CANONICAL_FACE_BOX``
   below) works across every photo without threading face-detection
   coordinates through the crop step.

This assumes the crop is roughly passport-style (a head-and-shoulders
portrait). Beautifying a manual crop of something else (a landscape, an
object, ...) just has no visible effect, since the skin/eye/teeth color
heuristics won't find anything matching to act on.

Pure Pillow/NumPy/OpenCV -- no Qt dependency, directly unit-testable.
"""

from __future__ import annotations

import dataclasses

import cv2
import numpy as np
from PIL import Image

from core.auto_edit import AutoEditor
from utils.logger import get_logger

logger = get_logger(__name__)

# Approximate face region within an already-cropped, head-centered passport
# photo -- NOT a detector output, just a fixed rectangle matching
# core.passport_photo's own auto-crop conventions (relative x, y, w, h).
CANONICAL_FACE_BOX: tuple[float, float, float, float] = (0.22, 0.10, 0.56, 0.58)

# Soft white (not pure #fff -- kinder to eyes and to lightly-off-white print
# stock) that "background whitening" blends the backdrop toward.
_WHITEN_BG_COLOR: tuple[int, int, int] = (245, 245, 245)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclasses.dataclass
class BeautifyOptions:
    """
    How much of each beautification effect to apply, as 0..1 intensities.

    ``enabled=False`` short-circuits :func:`beautify` to a no-op regardless
    of the individual intensities, so callers can keep a persistent set of
    slider values around and just flip ``enabled`` on/off (e.g. a UI
    checkbox) without losing the user's chosen mix.
    """

    enabled: bool = False
    skin_smooth: float = 0.0
    auto_correct: float = 0.0
    background_whiten: float = 0.0
    teeth_eye_whiten: float = 0.0
    # Opt-in, and deliberately OFF by default: see _whiten_background's note
    # on why the rembg/BiRefNet matte must never run in an interactive path.
    use_rembg: bool = False

    def __post_init__(self) -> None:
        self.skin_smooth = _clamp01(self.skin_smooth)
        self.auto_correct = _clamp01(self.auto_correct)
        self.background_whiten = _clamp01(self.background_whiten)
        self.teeth_eye_whiten = _clamp01(self.teeth_eye_whiten)

    @classmethod
    def default_on(cls) -> "BeautifyOptions":
        """Sensible one-click defaults: noticeable, but not overdone."""
        return cls(
            enabled=True,
            skin_smooth=0.4,
            auto_correct=0.5,
            background_whiten=0.5,
            teeth_eye_whiten=0.3,
        )


def beautify(image: Image.Image, options: BeautifyOptions) -> Image.Image:
    """
    Apply every enabled effect in ``options`` to a cropped passport photo.

    Returns a new RGB image; ``image`` is never modified in place. A no-op
    (returns ``image`` itself, unchanged) when ``options.enabled`` is False.
    """
    if not options.enabled:
        return image
    out = image.convert("RGB")
    # Order matters: correct color/exposure first (skin-tone/brightness
    # thresholds downstream assume roughly-corrected input), then smooth
    # skin, then whiten teeth/eyes, then the background last (so it doesn't
    # get touched by the skin bilateral filter's ROI falloff).
    if options.auto_correct > 0:
        out = _auto_correct(out, options.auto_correct)
    if options.skin_smooth > 0:
        out = _smooth_skin(out, options.skin_smooth)
    if options.teeth_eye_whiten > 0:
        out = _whiten_teeth_eyes(out, options.teeth_eye_whiten)
    if options.background_whiten > 0:
        out = _whiten_background(out, options.background_whiten, use_rembg=options.use_rembg)
    return out


# --------------------------------------------------------------------------- #
# Brightness / color auto-correction
# --------------------------------------------------------------------------- #
def _auto_correct(image: Image.Image, strength: float) -> Image.Image:
    """White balance + exposure + contrast only -- no crop/straighten."""
    rgb = np.asarray(image, dtype=np.uint8)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    editor = AutoEditor(strength=_clamp01(strength))
    recipe = editor.analyze_array(bgr)
    gentle = dataclasses.replace(recipe, straighten_deg=0.0, crop=None)
    out_bgr = editor.apply_array(bgr, gentle)
    out_rgb = cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(out_rgb, "RGB")


# --------------------------------------------------------------------------- #
# Skin smoothing
# --------------------------------------------------------------------------- #
def _face_roi_px(size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Pixel ROI a bit larger than CANONICAL_FACE_BOX (room for cheeks/chin)."""
    w, h = size
    x, y, fw, fh = CANONICAL_FACE_BOX
    x0 = max(0, int((x - fw * 0.25) * w))
    y0 = max(0, int((y - fh * 0.15) * h))
    x1 = min(w, int((x + fw * 1.25) * w))
    y1 = min(h, int((y + fh * 1.25) * h))
    return x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)


def _skin_mask(rgb_roi: np.ndarray) -> np.ndarray:
    """
    Rough YCrCb skin-tone mask (0..255).

    Excludes eyes/brows/lips/nostrils well enough for a *blended* smoothing
    effect without any landmark data -- it doesn't need to be exact since the
    smoothing amount is also weighted by this mask, so partial/edge pixels
    just get partial smoothing rather than a hard cutoff.
    """
    ycrcb = cv2.cvtColor(rgb_roi, cv2.COLOR_RGB2YCrCb)
    y, cr, cb = cv2.split(ycrcb)
    mask = (
        (cr >= 133) & (cr <= 180) & (cb >= 77) & (cb <= 135) & (y >= 55)
    ).astype(np.uint8) * 255
    return cv2.GaussianBlur(mask, (0, 0), sigmaX=3)


def _ellipse_falloff(shape: tuple[int, int]) -> np.ndarray:
    """1.0 at the ROI's center fading to 0.0 at/past its elliptical edge."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cy, cx = h / 2.0, w / 2.0
    ny = (yy - cy) / (h / 2.0 + 1e-6)
    nx = (xx - cx) / (w / 2.0 + 1e-6)
    return np.clip(1.0 - (nx**2 + ny**2), 0.0, 1.0)


def _smooth_skin(image: Image.Image, strength: float) -> Image.Image:
    rgb = np.asarray(image, dtype=np.uint8)
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = _face_roi_px((w, h))
    roi = rgb[y0:y1, x0:x1]
    if roi.size == 0:
        return image

    smoothed = cv2.bilateralFilter(roi, d=9, sigmaColor=45, sigmaSpace=45)
    skin = _skin_mask(roi).astype(np.float32) / 255.0
    falloff = _ellipse_falloff(roi.shape[:2])
    amount = (skin * falloff * _clamp01(strength))[..., None]

    blended = roi.astype(np.float32) * (1 - amount) + smoothed.astype(np.float32) * amount
    out = rgb.copy()
    out[y0:y1, x0:x1] = np.clip(blended, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")


# --------------------------------------------------------------------------- #
# Teeth / eye whitening
# --------------------------------------------------------------------------- #
def _sub_box_px(size: tuple[int, int], rel_box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """``rel_box`` is (x0, y0, x1, y1) relative to CANONICAL_FACE_BOX, not the frame."""
    w, h = size
    x, y, fw, fh = CANONICAL_FACE_BOX
    rx0, ry0, rx1, ry1 = rel_box
    x0 = int((x + rx0 * fw) * w)
    y0 = int((y + ry0 * fh) * h)
    x1 = int((x + rx1 * fw) * w)
    y1 = int((y + ry1 * fh) * h)
    return max(0, x0), max(0, y0), min(w, max(x1, x0 + 1)), min(h, max(y1, y0 + 1))


# Rough mouth / left-eye / right-eye sub-regions relative to CANONICAL_FACE_BOX.
_TEETH_EYE_REGIONS: tuple[tuple[float, float, float, float], ...] = (
    (0.28, 0.62, 0.72, 0.85),  # mouth
    (0.10, 0.22, 0.42, 0.42),  # left eye
    (0.58, 0.22, 0.90, 0.42),  # right eye
)


def _whiten_teeth_eyes(image: Image.Image, strength: float) -> Image.Image:
    amount_cap = _clamp01(strength)
    if amount_cap <= 0:
        # Skip the HSV<->RGB round-trip entirely at zero strength: it's a
        # no-op effect-wise, but the color-space conversion isn't bit-exact,
        # so doing it anyway would still perturb pixels by rounding noise.
        return image

    rgb = np.asarray(image, dtype=np.uint8)
    h, w = rgb.shape[:2]
    out = rgb.copy()

    for rel_box in _TEETH_EYE_REGIONS:
        x0, y0, x1, y1 = _sub_box_px((w, h), rel_box)
        if x1 <= x0 or y1 <= y0:
            continue
        roi = out[y0:y1, x0:x1]
        hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV).astype(np.float32)
        s, v = hsv[..., 1], hsv[..., 2]
        # Teeth/sclera are bright and comparatively low-saturation; skin and
        # lips usually aren't -- but the boundary is fuzzy for light skin
        # tones, so this stays a soft, blurred mask rather than a hard cut,
        # and is further tapered by an elliptical falloff so the sub-region's
        # rectangular boundary itself is never visible as a hard edge.
        bright_lowsat = ((v > 130) & (s < 90)).astype(np.float32)
        bright_lowsat = cv2.GaussianBlur(bright_lowsat, (0, 0), sigmaX=2)
        falloff = _ellipse_falloff(roi.shape[:2])
        amount = bright_lowsat * falloff * amount_cap

        hsv[..., 1] = np.clip(hsv[..., 1] * (1 - 0.55 * amount), 0, 255)
        hsv[..., 2] = np.clip(hsv[..., 2] + 30 * amount, 0, 255)
        out[y0:y1, x0:x1] = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)

    return Image.fromarray(out, "RGB")


# --------------------------------------------------------------------------- #
# Background whitening
# --------------------------------------------------------------------------- #
# A studio's own head-and-shoulders ellipse (core.album.face_segmenter's
# `cutout_from_faces` ellipse fallback) was tried here first, but it's tuned
# for editorial album cutouts -- generous "shoulders" padding that, on a
# *tight* passport-style crop, covers nearly the whole frame and leaves only
# a thin sliver near the top corners to whiten. Passport backdrops are
# almost always a single flat color that touches the crop's border, so a
# color-similarity-to-the-edges estimate (classic chroma-key-style masking)
# is both simpler and a better fit than guessing at body geometry.
_BG_SIMILARITY_NEAR = 18.0  # RGB distance below this: definitely background
_BG_SIMILARITY_FAR = 70.0  # RGB distance above this: definitely subject


def _background_color_similarity(rgb: np.ndarray) -> np.ndarray:
    """
    Per-pixel "looks like the backdrop" score in ``[0, 1]`` (1 = background).

    Estimates the backdrop color from a thin strip along all four edges
    (the backdrop reliably touches the border in a head-and-shoulders crop),
    then scores every pixel by its color distance to that estimate, with a
    soft linear ramp rather than a hard cutoff so shadow/gradient in a real
    backdrop doesn't produce a hard-edged mask.
    """
    h, w = rgb.shape[:2]
    edge = max(2, min(h, w) // 40)
    border = np.concatenate(
        [
            rgb[:edge, :, :].reshape(-1, 3),
            rgb[-edge:, :, :].reshape(-1, 3),
            rgb[:, :edge, :].reshape(-1, 3),
            rgb[:, -edge:, :].reshape(-1, 3),
        ],
        axis=0,
    ).astype(np.float32)
    reference = np.median(border, axis=0)

    dist = np.sqrt(((rgb.astype(np.float32) - reference) ** 2).sum(axis=-1))
    similarity = 1.0 - np.clip(
        (dist - _BG_SIMILARITY_NEAR) / (_BG_SIMILARITY_FAR - _BG_SIMILARITY_NEAR), 0.0, 1.0
    )
    return cv2.GaussianBlur(similarity.astype(np.float32), (0, 0), sigmaX=2)


def _whiten_background(
    image: Image.Image, strength: float, *, use_rembg: bool = False
) -> Image.Image:
    """
    Blend the backdrop toward soft white.

    ``use_rembg`` is **off by default and must stay that way for anything
    interactive.** When rembg is installed, ``subject_cutout`` runs a full
    BiRefNet neural-net inference (hundreds of ms to seconds, and hundreds of
    MB of ONNX runtime memory) per call. This function is called once per
    slider tick per person, so with rembg on, dragging one slider fired
    dozens of back-to-back inferences -- which in practice hung the app and
    took the whole machine down with it. The color-similarity mask below is
    milliseconds, needs no model, and is a better fit for passport work
    anyway (studio backdrops are flat and touch the crop border). Only pass
    ``use_rembg=True`` from a deliberate, one-shot, non-interactive path.
    """
    rgb_image = image.convert("RGB")
    alpha = None
    if use_rembg:
        from core.album.face_segmenter import subject_cutout

        rgba = subject_cutout(rgb_image)
        if rgba is not None:
            alpha = np.asarray(rgba.getchannel("A"), dtype=np.float32) / 255.0
    if alpha is None:
        rgb_arr = np.asarray(rgb_image, dtype=np.uint8)
        alpha = 1.0 - _background_color_similarity(rgb_arr)  # 1 = subject

    rgb = np.asarray(rgb_image, dtype=np.float32)
    bg = np.full_like(rgb, _WHITEN_BG_COLOR, dtype=np.float32)

    blend = ((1.0 - alpha) * _clamp01(strength))[..., None]
    out = rgb * (1 - blend) + bg * blend
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
