"""Cosmetic star sharpening via a single-pass unsharp mask.

This is purely cosmetic enhancement — it increases edge contrast to make
slightly trailed stars appear crisper. It does NOT recover detail lost to
star trailing or atmospheric seeing.

Typical call in the pipeline (before overlay rendering):

    from services.sharpening import apply_unsharp_mask
    img = apply_unsharp_mask(img, radius=1.5, amount=80, threshold=3)
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def apply_unsharp_mask(
    frame: "Image.Image | np.ndarray",
    radius: float = 1.5,
    amount: int = 80,
    threshold: int = 3,
) -> "Image.Image | np.ndarray":
    """Apply a single-pass unsharp mask to improve perceived star sharpness.

    Pixels where any channel exceeds 250 in the original (saturated regions
    such as the Moon or bright planets) are restored from the original so
    sharpening haloes are not added to already-clipped areas.

    The input is never modified in place; a copy is always returned.

    Args:
        frame: Input frame as PIL Image (RGB or RGBA) or numpy uint8 array.
        radius: Gaussian blur radius in pixels. Wider radii affect larger
            features; keep <= 2 for star sharpening. Default 1.5.
        amount: Sharpening strength on Pillow's 0-500 scale. 80 is a subtle
            improvement roughly equivalent to 30 % sharpening in most editors.
            Default 80.
        threshold: Minimum pixel difference (0-255) to trigger sharpening.
            Higher values protect the flat dark-sky background from noise
            amplification. Default 3.

    Returns:
        Sharpened copy in the same type as the input (PIL Image or ndarray).
    """
    input_is_array = isinstance(frame, np.ndarray)

    if input_is_array:
        pil_img = Image.fromarray(frame)
    else:
        pil_img = frame.copy()

    sharpened_pil = pil_img.filter(
        ImageFilter.UnsharpMask(radius=radius, percent=amount, threshold=threshold)
    )

    # Restore saturated pixels from the original so bright objects (Moon,
    # planets) are not ringed by sharpening haloes.
    orig_arr = np.asarray(pil_img, dtype=np.uint8)
    sharp_arr = np.asarray(sharpened_pil, dtype=np.uint8)

    # sat_mask is True wherever any channel > 250 in the ORIGINAL frame.
    # Shape: (H, W, 1) — broadcasts across all channels.
    sat_mask = np.any(orig_arr > 250, axis=2, keepdims=True)
    result_arr = np.where(sat_mask, orig_arr, sharp_arr).astype(np.uint8)

    if input_is_array:
        return result_arr

    return Image.fromarray(result_arr, mode=pil_img.mode)
