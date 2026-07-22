"""
Procedural brush / torn-edge alpha masks (Phase 5).

The signature painterly cut-out on designed wedding spreads — a photo with a
rough, feathered "torn paper / brush stroke" edge instead of a clean rectangle
— generated entirely in code (no art assets). A low-frequency noise field
perturbs a soft shape boundary, then the result is feathered, giving an organic
irregular edge. The ``seed`` makes each mask deterministic (stable re-renders)
yet varied between slots.

Pure Pillow/NumPy, so it renders and tests without the detection backends.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def brush_mask(
    size: tuple[int, int],
    seed: int = 0,
    *,
    roughness: float = 0.06,
    feather: float = 0.02,
) -> Image.Image:
    """
    Return an ``L`` (grayscale) alpha mask with a rough, feathered edge.

    Args:
        size: ``(width, height)`` in pixels.
        seed: Deterministic variation between calls.
        roughness: How irregular the edge is, as a fraction of the short edge.
        feather: Softness of the edge, as a fraction of the short edge.
    """
    w = max(2, int(size[0]))
    h = max(2, int(size[1]))
    short = min(w, h)
    rng = np.random.default_rng(seed)

    # Base rounded rectangle, inset so the roughened edge stays inside bounds.
    inset = max(1, round(short * roughness * 0.6))
    base = Image.new("L", (w, h), 0)
    ImageDraw.Draw(base).rounded_rectangle(
        [inset, inset, w - 1 - inset, h - 1 - inset],
        radius=round(short * 0.05),
        fill=255,
    )
    # Soft boundary gradient.
    blur_r = max(1, round(short * roughness))
    base_blur = np.asarray(base.filter(ImageFilter.GaussianBlur(blur_r)), dtype=np.float32) / 255.0

    # Low-frequency noise, upscaled smoothly. A coarser grid (fewer cells) gives
    # gentle undulations — a torn-paper edge — instead of a high-frequency splatter.
    ny, nx = max(2, h // 90), max(2, w // 90)
    noise = rng.random((ny, nx)).astype(np.float32)
    noise_img = Image.fromarray((noise * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    noise_arr = np.asarray(noise_img, dtype=np.float32) / 255.0

    # Perturb the boundary by the noise and threshold -> irregular edge. A smaller
    # amplitude keeps the edge subtle rather than a jagged splatter.
    field = base_blur + (noise_arr - 0.5) * roughness * 1.8
    alpha = ((field > 0.5).astype(np.uint8)) * 255
    mask = Image.fromarray(alpha, "L")

    # Feather for a soft, painterly edge.
    return mask.filter(ImageFilter.GaussianBlur(max(1, round(short * feather))))
