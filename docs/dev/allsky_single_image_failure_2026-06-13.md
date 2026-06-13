# All-Sky Single-Image Calibration — 0% Failure Root Cause (2026-06-13)

## Summary

The offline baseline (`allsky_baseline_2026-06-12-before.md`) shows single-image
`calibrate()` failing the post-fit sanity check on **0/130** reference frames. This is the
true face of user symptom #1 ("plate solving is very varied"): single-image auto-calibration
essentially never succeeds on this camera. Only the committed multi-image model works.

Diagnosis was run with `scripts/dev/allsky/diagnose_single_fail.py` on the hand-confirmed
anchor frame `lum_20260116_021511.fits` (3552×3552, trimmed sky_r≈1563).

## Two compounding root causes

### 1. The bright-anchor validation gate rejects even the known-good model

The committed `sample_images/multi_calibration.json` (the "best automated result",
12.7px median RMS over 1165 stars, physically-reasonable params) judged on this frame:

- `validate_lens_polynomial`: **pass** (a3=-47.6, plausible).
- `validate_bright_anchors`: **FAIL** — only 2/6 brightest anchors within 40px
  (missed: Sirius 46px, Arcturus 117px, Betelgeuse 200px, Aldebaran 406px).
- `_brightness_match` @25px: **70 matches** — the overall fit is good.

So the model fits 70 stars to ≤25px but is rejected because 4 of the *brightest 6* are far
off. The brightest winter-evening stars (Sirius, Rigel, Betelgeuse, Aldebaran) sit at **low
altitude**, near the fisheye edge where the radial polynomial is least accurate and where this
global model has its largest residuals. **The gate tests the model precisely where every
fisheye model is weakest.** The Phase 2 tightening (`min_hits=5` of top 6, `max_miss=40px`,
`min_alt=15°`) made this worse: it now rejects the production-quality model.

### 2. The grid search lands in the wrong basin

Grid-search best initial model on this frame:

| param | grid search | known-good multi | Δ |
|---|---|---|---|
| east_left | **False** (photo) | **True** (FITS) | **wrong mirror** |
| a1 | 882 | 1277 | −395 |
| axis_alt | 80.0 | 84.49 | −4.5 |
| axis_az | 135.0 | 280.5 | −145.5 |

The grid picks `east_left=False` because the wrong mirror convention accumulates more
spread-weighted coincidental matches (76 vs 67) in the dense field. With the wrong mirror and
a1 ~70% of true, least_squares then bends `a3` to its bound (the baseline shows a3 pinned at
±25/±100) chasing a false orientation — which `validate_lens_polynomial` correctly rejects.

## Why RMS-looks-fine-but-wrong happens

A wrong orientation over ~4000 catalog stars above the horizon finds many coincidental
density matches with a respectable average residual, while the handful of bright anchors are
far off. That is exactly the failure mode `validate_bright_anchors` was added to catch — but
the gate is now so tight it also rejects the genuinely-good global fit.

## Recommended direction (not yet implemented — needs prioritisation)

1. **Re-design the post-fit acceptance gate.** Validate against the *distribution* of all
   matched-star residuals (e.g. median ≤ X px AND ≥ N matches AND good azimuth spread) rather
   than a hit-count on the brightest-6, which skew low-altitude. If keeping an anchor check,
   weight by altitude or raise `max_miss_px` for anchors below ~30°. Acceptance test: the
   committed `multi_calibration.json` MUST pass the gate on `lum_20260116_021511.fits`.
2. **Fix mirror/scale disambiguation in the grid search.** The spread-weighted match count
   selects the wrong `east_left`. Consider tie-breaking by post-refinement RMS of each mirror
   hypothesis, or seeding `east_left` from FITS metadata when available (NINA writes FITS =
   east-left).
3. **Re-baseline after each change** with `baseline_run.py`; target a meaningful single-image
   success rate, and verify no anchor drift on the confirmed frame.

These are design changes to the calibration core, deliberately out of scope for the
reliability-hardening pass (F1–F14) that this session completed.
