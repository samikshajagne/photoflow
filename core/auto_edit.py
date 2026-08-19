"""
Non-destructive auto-correction engine for PhotoFlow.

This module computes a small set of *adjustment parameters* -- a
:class:`EditRecipe` -- that describe how an image should be corrected, without
ever touching the original. A recipe is JSON-serializable so it can be stored
as a sidecar alongside the source file; it can also be rendered onto a decoded
copy of the image for preview or export via :meth:`AutoEditor.apply`.

The signals the engine computes are deliberately conservative:

- **White balance** via the gray-world assumption: per-channel gains that pull
  each channel's mean toward the overall gray mean, neutralizing color casts.
- **Exposure** as a brightness multiplier that nudges the frame's mean luma
  toward a target.
- **Contrast** as a gentle normalization toward a reference standard deviation,
  giving flat images a modest boost.
- **Straighten** as a small leveling rotation (conservative: 0.0 unless a
  reliable near-horizontal tilt can be estimated).
- **Crop** as a face-aware rule-of-thirds suggestion that keeps every supplied
  face box fully inside the frame, or ``None`` for the full frame.

The public entry point is :class:`AutoEditor`. Originals are never modified:
:meth:`~AutoEditor.analyze` reads the image read-only to derive a recipe, and
:meth:`~AutoEditor.apply` returns a brand-new array.

Scope: this module performs *only* auto-correction. Blur detection, quality
scoring, face detection, organization, and persistence live elsewhere.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence, Union

import cv2
import numpy as np

from utils.logger import get_logger

if TYPE_CHECKING:
    from utils.config import AppConfig

logger = get_logger(__name__)

PathLike = Union[str, Path]

# Default target mean luma (normalized 0..1) that exposure aims for. Mid-tone.
DEFAULT_TARGET_BRIGHTNESS: float = 0.5
# Default maximum absolute leveling rotation the engine will ever propose.
# Kept small so a mis-estimated tilt can't visibly skew a good photo.
DEFAULT_MAX_STRAIGHTEN_DEG: float = 3.0

# How much of each computed correction is actually applied (0 = none, 1 = full).
# A gentle default means already-decent photos are only nudged, not overhauled.
DEFAULT_EDIT_STRENGTH: float = 0.5

# Dead-zones: when a raw correction is within this of "no change", skip it
# entirely, so well-exposed / neutral / level photos are left untouched.
_GAIN_DEADZONE: float = 0.06        # ±6% channel gain
_EXPOSURE_DEADZONE: float = 0.12    # ±12% brightness
_CONTRAST_DEADZONE: float = 0.08    # ±8% contrast
_STRAIGHTEN_DEADZONE_DEG: float = 1.0

# Clamp ranges keeping each adjustment in a tight, non-destructive band. These
# are deliberately narrow so the auto-correction can never blow out or crush an
# image, or neutralize an intentional colour mood (e.g. a warm Haldi frame).
_GAIN_MIN: float = 0.85
_GAIN_MAX: float = 1.18
_EXPOSURE_MIN: float = 0.8
_EXPOSURE_MAX: float = 1.4
_CONTRAST_MIN: float = 0.92
_CONTRAST_MAX: float = 1.15

# Reference standard deviation (normalized 0..1) contrast normalizes toward.
_CONTRAST_REFERENCE_STD: float = 0.22
# Comfortable relative margin added around the union of face boxes when
# proposing a crop, expressed as a fraction of the union's size.
_FACE_CROP_MARGIN: float = 0.35


class AutoEditError(Exception):
    """Raised when auto-editing cannot proceed (bad path/args, unreadable image)."""


@dataclasses.dataclass(frozen=True)
class EditRecipe:
    """
    A non-destructive set of corrections to apply to an image.

    All fields are plain numbers (or a tuple of them) so the recipe is fully
    JSON-serializable via :meth:`as_dict` and round-trippable via
    :meth:`from_dict`.

    Attributes:
        white_balance_gains: Per-channel ``(R, G, B)`` multipliers applied to
            the decoded image. ``(1.0, 1.0, 1.0)`` leaves color untouched.
        exposure: Brightness multiplier. ``1.0`` is no change; ``>1`` brightens,
            ``<1`` darkens.
        contrast: Contrast multiplier around mid-gray. ``1.0`` is no change;
            ``>1`` increases contrast, ``<1`` flattens.
        straighten_deg: Leveling rotation in degrees. ``0.0`` is no rotation.
            A positive value rotates the image counter-clockwise to level it.
        crop: Relative ``(x, y, w, h)`` crop rectangle with each component in
            ``[0, 1]`` (fractions of width/height). ``None`` means the full
            frame is kept.
    """

    white_balance_gains: tuple[float, float, float]
    exposure: float
    contrast: float
    straighten_deg: float
    crop: Optional[tuple[float, float, float, float]]

    @classmethod
    def identity(cls) -> "EditRecipe":
        """Return a no-op recipe that leaves an image unchanged."""
        return cls(
            white_balance_gains=(1.0, 1.0, 1.0),
            exposure=1.0,
            contrast=1.0,
            straighten_deg=0.0,
            crop=None,
        )

    def as_dict(self) -> dict:
        """
        Return a JSON-serializable dict representation.

        The inverse of :meth:`from_dict`: ``EditRecipe.from_dict(r.as_dict())``
        equals ``r``.
        """
        return {
            "white_balance_gains": [float(g) for g in self.white_balance_gains],
            "exposure": float(self.exposure),
            "contrast": float(self.contrast),
            "straighten_deg": float(self.straighten_deg),
            "crop": None if self.crop is None else [float(c) for c in self.crop],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EditRecipe":
        """
        Build a recipe from a dict produced by :meth:`as_dict`.

        Raises:
            AutoEditError: if a required key is missing or malformed.
        """
        try:
            gains = tuple(float(g) for g in d["white_balance_gains"])
            if len(gains) != 3:
                raise ValueError("white_balance_gains must have 3 elements")
            raw_crop = d["crop"]
            if raw_crop is None:
                crop: Optional[tuple[float, float, float, float]] = None
            else:
                crop = tuple(float(c) for c in raw_crop)
                if len(crop) != 4:
                    raise ValueError("crop must have 4 elements")
            return cls(
                white_balance_gains=(gains[0], gains[1], gains[2]),
                exposure=float(d["exposure"]),
                contrast=float(d["contrast"]),
                straighten_deg=float(d["straighten_deg"]),
                crop=crop,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AutoEditError(f"Malformed recipe dict: {exc}") from exc


class AutoEditor:
    """
    Computes and renders non-destructive auto-corrections for an image.

    The editor is stateless between calls, so a single instance can analyze and
    apply recipes for many images.

    Args:
        target_brightness: Desired mean luma (normalized ``0..1``) that the
            exposure adjustment aims for. Must satisfy ``0 < target < 1``.
        max_straighten_deg: Maximum absolute leveling rotation (degrees) the
            engine will ever propose. Must be ``>= 0``.

    Raises:
        AutoEditError: if any argument is out of range.
    """

    def __init__(
        self,
        target_brightness: float = DEFAULT_TARGET_BRIGHTNESS,
        max_straighten_deg: float = DEFAULT_MAX_STRAIGHTEN_DEG,
        strength: float = DEFAULT_EDIT_STRENGTH,
    ) -> None:
        if not 0.0 < target_brightness < 1.0:
            raise AutoEditError(
                f"target_brightness must be in (0, 1), got {target_brightness}"
            )
        if max_straighten_deg < 0:
            raise AutoEditError(
                f"max_straighten_deg must be >= 0, got {max_straighten_deg}"
            )

        self.target_brightness = float(target_brightness)
        self.max_straighten_deg = float(max_straighten_deg)
        # How much of each correction to apply (blended toward "no change").
        self.strength = _clamp(float(strength), 0.0, 1.0)

    @classmethod
    def from_config(cls, config: "AppConfig") -> "AutoEditor":
        """
        Build an editor from a validated :class:`~utils.config.AppConfig`.

        The config carries no auto-edit-specific keys, so this simply returns a
        default-configured instance; the signature exists for parity with the
        other engines.
        """
        return cls()

    # ------------------------------------------------------------------ #
    # Analysis
    # ------------------------------------------------------------------ #
    def analyze(
        self,
        image_path: PathLike,
        face_regions: Sequence[tuple[float, float, float, float]] = (),
    ) -> EditRecipe:
        """
        Inspect an image (read-only) and derive an :class:`EditRecipe`.

        Args:
            image_path: Path to the image to analyze.
            face_regions: Relative ``(x, y, w, h)`` face boxes in ``[0, 1]``.
                When non-empty, the proposed crop is guaranteed to contain all
                of them; when empty, ``crop`` is ``None`` (full frame).

        Returns:
            An :class:`EditRecipe` describing the suggested corrections.

        Raises:
            AutoEditError: if the image is missing, empty, or undecodable.
        """
        bgr = self._load_bgr(image_path)
        recipe = self.analyze_array(bgr, face_regions=face_regions)
        logger.info(
            "Auto-edit analyze '%s': gains=(%.2f,%.2f,%.2f) exposure=%.2f "
            "contrast=%.2f straighten=%.2f crop=%s",
            image_path,
            recipe.white_balance_gains[0],
            recipe.white_balance_gains[1],
            recipe.white_balance_gains[2],
            recipe.exposure,
            recipe.contrast,
            recipe.straighten_deg,
            recipe.crop,
        )
        return recipe

    def analyze_array(
        self,
        bgr: np.ndarray,
        face_regions: Sequence[tuple[float, float, float, float]] = (),
    ) -> EditRecipe:
        """
        Same as :meth:`analyze`, but takes an already-decoded BGR array.

        Lets callers who already have pixels in memory (e.g. a UI preview
        working on an in-memory crop) skip the disk round-trip that
        :meth:`analyze` does via :meth:`_load_bgr`.
        """
        gains = self._gray_world_gains(bgr)
        exposure = self._exposure_multiplier(bgr)
        contrast = self._contrast_multiplier(bgr)
        straighten = self._straighten_degrees(bgr)
        crop = self._face_aware_crop(bgr.shape, face_regions)
        return EditRecipe(
            white_balance_gains=gains,
            exposure=exposure,
            contrast=contrast,
            straighten_deg=straighten,
            crop=crop,
        )

    def _gray_world_gains(self, bgr: np.ndarray) -> tuple[float, float, float]:
        """
        Gray-world white-balance gains as ``(R, G, B)`` multipliers.

        Each channel's gain is the overall gray mean divided by that channel's
        mean, clamped to ``[_GAIN_MIN, _GAIN_MAX]``. A neutral image yields
        gains near ``1.0``; a color-cast image yields gains that neutralize it
        (the dominant channel is scaled down, the deficient ones up).
        """
        # OpenCV decodes BGR; means come back in B, G, R order.
        means = bgr.reshape(-1, 3).mean(axis=0)
        gray_mean = float(means.mean())
        b_mean, g_mean, r_mean = (float(m) for m in means)

        def gain(channel_mean: float) -> float:
            if channel_mean <= 1e-6:
                return _GAIN_MAX
            raw = gray_mean / channel_mean
            softened = _soften(raw, self.strength, _GAIN_DEADZONE)
            return _clamp(softened, _GAIN_MIN, _GAIN_MAX)

        # Return in R, G, B order per the dataclass contract.
        return (gain(r_mean), gain(g_mean), gain(b_mean))

    def _exposure_multiplier(self, bgr: np.ndarray) -> float:
        """
        Brightness multiplier that moves mean luma toward ``target_brightness``.

        Computes the normalized (``0..1``) mean luma and returns
        ``target / current``, clamped to ``[_EXPOSURE_MIN, _EXPOSURE_MAX]``. A
        dark image yields ``>1``; a bright image yields ``<1``.
        """
        luma = self._luma(bgr)
        mean_luma = float(luma.mean()) / 255.0
        if mean_luma <= 1e-6:
            return _EXPOSURE_MAX
        raw = self.target_brightness / mean_luma
        softened = _soften(raw, self.strength, _EXPOSURE_DEADZONE)
        return _clamp(softened, _EXPOSURE_MIN, _EXPOSURE_MAX)

    def _contrast_multiplier(self, bgr: np.ndarray) -> float:
        """
        Gentle contrast multiplier normalizing toward a reference std-dev.

        Returns ``reference_std / current_std`` (both normalized ``0..1``),
        clamped to ``[_CONTRAST_MIN, _CONTRAST_MAX]``. Flat images (low std) get
        a modest boost; already-punchy images are nudged down slightly.
        """
        luma = self._luma(bgr)
        std = float(luma.std()) / 255.0
        if std <= 1e-6:
            return _CONTRAST_MAX
        raw = _CONTRAST_REFERENCE_STD / std
        softened = _soften(raw, self.strength, _CONTRAST_DEADZONE)
        return _clamp(softened, _CONTRAST_MIN, _CONTRAST_MAX)

    def _straighten_degrees(self, bgr: np.ndarray) -> float:
        """
        Conservative leveling estimate from dominant near-horizontal lines.

        Uses a probabilistic Hough transform on detected edges and takes the
        median tilt of lines that are within ~15 degrees of horizontal. When no
        reliable estimate exists, returns ``0.0``. The result is clamped to
        ``[-max_straighten_deg, max_straighten_deg]``.
        """
        if self.max_straighten_deg <= 0:
            return 0.0

        gray = self._luma(bgr)
        edges = cv2.Canny(gray, 50, 150)
        min_dim = min(gray.shape[:2])
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180.0,
            threshold=80,
            minLineLength=max(20, min_dim // 4),
            maxLineGap=10,
        )
        if lines is None:
            return 0.0

        angles: list[float] = []
        for line in lines:
            # cv2.HoughLinesP's return shape has changed across OpenCV
            # versions: classically (N, 1, 4) -- each line wrapped in an
            # extra dimension -- but some builds (observed on
            # opencv-python-headless 5.x) instead return the squeezed
            # (N, 4) shape. Indexing `line[0]` in the squeezed case yields a
            # single numpy.int32 coordinate rather than the (x1, y1, x2, y2)
            # quad, and unpacking that scalar raises "TypeError: 'numpy.int32'
            # object is not iterable". Flattening first makes this robust to
            # either shape, on any OpenCV version.
            x1, y1, x2, y2 = (float(v) for v in np.asarray(line).reshape(-1))
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            # Fold to [-90, 90]; keep only near-horizontal lines.
            if angle > 90.0:
                angle -= 180.0
            elif angle < -90.0:
                angle += 180.0
            if abs(angle) <= 15.0:
                angles.append(angle)

        if not angles:
            return 0.0

        # Median tilt; rotating by +tilt levels the lines back to horizontal.
        tilt = float(np.median(angles))
        if abs(tilt) < _STRAIGHTEN_DEADZONE_DEG:
            return 0.0  # too small to be worth risking a wrong rotation
        tilt *= self.strength
        return _clamp(tilt, -self.max_straighten_deg, self.max_straighten_deg)

    def _face_aware_crop(
        self,
        shape: tuple[int, ...],
        regions: Sequence[tuple[float, float, float, float]],
    ) -> Optional[tuple[float, float, float, float]]:
        """
        Propose a relative crop that contains every face box, or ``None``.

        With no faces, returns ``None`` (keep the full frame). Otherwise
        computes the union of the face boxes, pads it with a comfortable margin,
        and biases the composition toward the rule of thirds (placing the faces'
        centroid near the nearest third line) while guaranteeing every input
        box stays fully inside the returned rectangle. The result is clamped to
        ``[0, 1]``.
        """
        if not regions:
            return None

        # Union of all face boxes in relative coordinates, clamped to frame.
        xs0 = [max(0.0, float(r[0])) for r in regions]
        ys0 = [max(0.0, float(r[1])) for r in regions]
        xs1 = [min(1.0, float(r[0]) + float(r[2])) for r in regions]
        ys1 = [min(1.0, float(r[1]) + float(r[3])) for r in regions]
        ux0, uy0 = min(xs0), min(ys0)
        ux1, uy1 = max(xs1), max(ys1)
        uw, uh = ux1 - ux0, uy1 - uy0

        # Comfortable margin around the union.
        mx = uw * _FACE_CROP_MARGIN
        my = uh * _FACE_CROP_MARGIN
        cx0 = ux0 - mx
        cy0 = uy0 - my
        cx1 = ux1 + mx
        cy1 = uy1 + my

        # Rule-of-thirds bias: shift the crop so the faces' centroid lands near
        # the nearest third line, but never so far that a face leaves the crop.
        cw = cx1 - cx0
        ch = cy1 - cy0
        face_cx = 0.5 * (ux0 + ux1)
        face_cy = 0.5 * (uy0 + uy1)
        target_cx = _nearest_third(face_cx)
        target_cy = _nearest_third(face_cy)
        # Desired crop origin so the centroid sits at the target third.
        dx0 = face_cx - target_cx * cw
        dy0 = face_cy - target_cy * ch
        # Constrain the shift so the union stays inside the crop.
        cx0 = _clamp(dx0, ux1 - cw, ux0)
        cy0 = _clamp(dy0, uy1 - ch, uy0)
        cx1 = cx0 + cw
        cy1 = cy0 + ch

        # Final clamp to the frame; if the crop hit a frame edge, slide it back
        # in rather than shrinking past a face.
        cx0, cx1 = _slide_into_unit(cx0, cx1)
        cy0, cy1 = _slide_into_unit(cy0, cy1)

        return (cx0, cy0, cx1 - cx0, cy1 - cy0)

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def apply(self, image_path: PathLike, recipe: EditRecipe) -> np.ndarray:
        """
        Render ``recipe`` onto a decoded copy of the image.

        Adjustments are applied in a sensible order: white balance -> exposure
        -> contrast -> straighten (rotate) -> crop. The original file is never
        modified; a new BGR ``uint8`` array is returned.

        Args:
            image_path: Path to the source image.
            recipe: The :class:`EditRecipe` to render.

        Returns:
            A BGR ``uint8`` :class:`numpy.ndarray` with the recipe applied.

        Raises:
            AutoEditError: if the image is missing/empty/undecodable or the
                recipe is not an :class:`EditRecipe`.
        """
        bgr = self._load_bgr(image_path)
        return self.apply_array(bgr, recipe)

    def apply_array(self, bgr: np.ndarray, recipe: EditRecipe) -> np.ndarray:
        """
        Same as :meth:`apply`, but takes an already-decoded BGR array.

        Raises:
            AutoEditError: if ``recipe`` is not an :class:`EditRecipe`.
        """
        if not isinstance(recipe, EditRecipe):
            raise AutoEditError(f"recipe must be an EditRecipe, got {type(recipe)!r}")

        bgr = bgr.astype(np.float32)

        # White balance: gains are (R, G, B); the array is BGR.
        r_gain, g_gain, b_gain = recipe.white_balance_gains
        bgr[:, :, 0] *= b_gain
        bgr[:, :, 1] *= g_gain
        bgr[:, :, 2] *= r_gain

        # Exposure: simple brightness multiplier.
        bgr *= recipe.exposure

        # Contrast: scale around mid-gray (127.5).
        bgr = (bgr - 127.5) * recipe.contrast + 127.5

        out = np.clip(bgr, 0, 255).astype(np.uint8)

        # Straighten: rotate about the center, keeping the original canvas size.
        if recipe.straighten_deg != 0.0:
            height, width = out.shape[:2]
            center = (width / 2.0, height / 2.0)
            matrix = cv2.getRotationMatrix2D(center, recipe.straighten_deg, 1.0)
            out = cv2.warpAffine(
                out,
                matrix,
                (width, height),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )

        # Crop: relative (x, y, w, h) -> pixel slice.
        if recipe.crop is not None:
            out = self._apply_crop(out, recipe.crop)

        return out

    @staticmethod
    def _apply_crop(
        image: np.ndarray, crop: tuple[float, float, float, float]
    ) -> np.ndarray:
        """Slice ``image`` by a relative ``(x, y, w, h)`` rectangle in ``[0, 1]``."""
        height, width = image.shape[:2]
        rx, ry, rw, rh = crop
        x0 = int(round(_clamp(rx, 0.0, 1.0) * width))
        y0 = int(round(_clamp(ry, 0.0, 1.0) * height))
        x1 = int(round(_clamp(rx + rw, 0.0, 1.0) * width))
        y1 = int(round(_clamp(ry + rh, 0.0, 1.0) * height))
        # Guard against degenerate (zero-size) crops.
        x1 = max(x1, x0 + 1)
        y1 = max(y1, y0 + 1)
        return image[y0:y1, x0:x1]

    # ------------------------------------------------------------------ #
    # Image measurements
    # ------------------------------------------------------------------ #
    @staticmethod
    def _luma(bgr: np.ndarray) -> np.ndarray:
        """Single-channel ``uint8`` luma (grayscale) view of a BGR image."""
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    def _load_bgr(self, image_path: PathLike) -> np.ndarray:
        """
        Decode an image to a 3-channel BGR ``uint8`` array.

        Reads raw bytes and decodes via ``cv2.imdecode`` so non-ASCII paths work
        across platforms.

        Raises:
            AutoEditError: if the path is missing, the file is empty, or the
                bytes cannot be decoded as an image.
        """
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            raise AutoEditError(f"Image does not exist: {path}")

        try:
            raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        except OSError as exc:
            raise AutoEditError(f"Failed to read image '{path}': {exc}") from exc
        if raw.size == 0:
            raise AutoEditError(f"Image file is empty: {path}")

        bgr = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if bgr is None:
            raise AutoEditError(
                f"Could not decode image (corrupt or unsupported): {path}"
            )
        return bgr


def _clamp(value: float, low: float, high: float) -> float:
    """Constrain ``value`` to the inclusive range ``[low, high]``."""
    return max(low, min(high, value))


def _soften(value: float, strength: float, deadzone: float) -> float:
    """
    Gentle a multiplicative correction toward 1.0 (no change).

    Values within ``deadzone`` of 1.0 snap to 1.0 (leave the image alone);
    larger ones are scaled toward 1.0 by ``strength`` (0 = no correction,
    1 = full). So a raw 1.6× exposure at strength 0.5 becomes 1.3×.
    """
    delta = value - 1.0
    if abs(delta) <= deadzone:
        return 1.0
    return 1.0 + delta * strength


def _nearest_third(value: float) -> float:
    """Return whichever rule-of-thirds line (1/3 or 2/3) is nearest ``value``."""
    return 1.0 / 3.0 if abs(value - 1.0 / 3.0) <= abs(value - 2.0 / 3.0) else 2.0 / 3.0


def _slide_into_unit(low: float, high: float) -> tuple[float, float]:
    """
    Slide the interval ``[low, high]`` so it fits within ``[0, 1]``.

    Preserves the interval's width when possible (the caller has already sized
    it no larger than the frame); only translates it. If the interval is wider
    than the unit frame it is clamped to ``[0, 1]``.
    """
    width = high - low
    if width >= 1.0:
        return 0.0, 1.0
    if low < 0.0:
        return 0.0, width
    if high > 1.0:
        return 1.0 - width, 1.0
    return low, high
