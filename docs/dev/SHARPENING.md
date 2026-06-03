# Cosmetic Star Sharpening

An optional single-pass unsharp mask applied to each frame **before overlay rendering**, so overlay text and lines remain unaffected.

> **Important:** This is cosmetic enhancement only. It increases edge contrast to make slightly trailed stars appear crisper. It does **not** recover detail lost to star trailing, atmospheric seeing, or focus errors.

---

## How it works

A Gaussian blur is subtracted from the original (Pillow's `ImageFilter.UnsharpMask`). Pixels that are already saturated (any channel > 250, e.g. the Moon or a bright planet) are restored from the original so sharpening haloes are not added to clipped regions.

One pass only — no stacking, no PSF estimation, no spatially varying kernels.

---

## Enabling

Enabled by default for new installs. To toggle, edit `sharpening.enabled` in `%APPDATA%\PFRSentinel\config.json`. The section is present in `DEFAULT_CONFIG` and will be created automatically on first launch:

```json
"sharpening": {
    "enabled": true,
    "radius": 1.5,
    "amount": 80,
    "threshold": 3
}
```

---

## Parameters

| Key | Type | Default | Range | Notes |
|---|---|---|---|---|
| `enabled` | bool | `true` | — | Master switch. Enabled by default. |
| `radius` | float | `1.5` | 0.5 – 3.0 | Gaussian blur radius in pixels. Wider radii affect larger features. Keep ≤ 2 for star sharpening; larger values can halo star cores. |
| `amount` | int | `80` | 0 – 500 | Sharpening strength on Pillow's internal 0–500 scale. 80 is subtle (≈ 30 % in most editors). 150–200 is clearly visible. Values above 250 will look processed. |
| `threshold` | int | `3` | 0 – 20 | Minimum pixel-value difference required before sharpening is applied. Protects the flat dark-sky background from noise amplification. Set to 0 only if you also reduce `amount`. |

### Tuning guide

Start with the defaults. If improvement is too subtle, raise `amount` in steps of 20. If halos appear around stars or the image looks over-processed, reduce `amount` or increase `threshold`. Only increase `radius` if you want to sharpen galaxy arms or nebula filaments rather than point stars.

---

## Pipeline position

```
resize
  → auto-stretch (MTF)
  → auto-brightness
  → saturation adjustment
  → timestamp corner
  → ★ sharpening (this module)     ← here
  → ML model tokens
  → star detection tokens
  → overlay rendering
  → output (file / web / Discord / RTSP)
```

Because sharpening runs before overlay rendering, overlay text and drawn lines are always crisp regardless of the sharpening settings.

---

## Code reference

| Path | Role |
|---|---|
| [services/sharpening.py](../../services/sharpening.py) | `apply_unsharp_mask()` — standalone function |
| [services/config.py](../../services/config.py) | `DEFAULT_CONFIG["sharpening"]` defaults |
| [ui/controllers/image_processor.py](../../ui/controllers/image_processor.py) | Pipeline call site |
| [tests/test_sharpening.py](../../tests/test_sharpening.py) | Unit tests |
