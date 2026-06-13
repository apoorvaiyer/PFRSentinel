# All-Sky Fisheye Calibration — Architecture Plan

> **Context recovery note:** This document captures calibration research conducted Jan–Apr 2026 and the full production plan. Read this before touching any `services/allsky/` calibration code.

---

## The Problem

The all-sky camera uses a fisheye lens. To overlay constellation lines and star labels on each frame, every catalog star's RA/Dec must be projected to an (x, y) pixel position. That projection requires knowing 8 lens/orientation parameters:

| Parameter | Meaning |
|-----------|---------|
| `cx`, `cy` | Optical centre (pixels) |
| `a1`, `a3`, `a5` | Radial polynomial coefficients (`r = a1·θ + a3·θ³ + a5·θ⁵`) |
| `roll` | Camera rotation around optical axis (radians) |
| `axis_alt` | True altitude of optical axis (degrees; 90 = pointing straight up) |
| `axis_az` | Azimuth direction of any tilt (degrees) |

These 8 values are unique to each physical camera + mount. They cannot be guessed; they must be measured from actual star positions in actual images.

The projection formula is implemented in `services/allsky/fisheye.py:FisheyeModel.altaz_to_pixel()`. **This function is the ground truth.** Every calibration script must call it directly — never re-implement the projection formula.

---

## What Is Already Built

### Core model
- **`services/allsky/fisheye.py`** — `FisheyeModel` dataclass with `altaz_to_pixel()`, vectorised `altaz_array_to_pixels()`, and JSON persistence (`save()` / `load()`).
- Calibration JSON stored at `%LOCALAPPDATA%\PFRSentinel\allsky_calibration.json` for production. `sample_images/` JSONs are **test artefacts only**.

### Single-image auto-calibration
- **`services/allsky/calibration.py`** — `calibrate(image, lat_deg, lon_deg, dt)` → `FisheyeModel`
  - Detects stars with OpenCV blob detection
  - Grid-searches `a1 × axis_alt × axis_az × east_left × cx/cy offsets` for best initial model
  - **Phase 2 roll sweep** (added Apr 2026): tries 16 roll angles (0°–337.5°) on the best grid result — catches cameras with physical image rotation
  - Iterative `scipy.optimize.least_squares` refinement with tightening tolerance
  - **Cross-sky coverage warning**: logs if matched stars are clustered in <30° azimuth arc (biased calibration)
  - Raises `CalibrationError` on insufficient matches or high residual

### UI integration (existing)
- **`ui/controllers/allsky_controller.py`** — `CalibrationWorker` (QThread) wraps `calibrate()`. Triggered by "Calibrate Now" button in the AllSky panel. Saves result to `%LOCALAPPDATA%\PFRSentinel\allsky_calibration.json` and updates config.
- **`ui/panels/allsky_settings.py`** — Shows calibration status string (date, n_matches, RMS).

### Multi-image joint calibration (script — not yet wired to production)
- **`scripts/allsky_multi_cal.py`** — Loads N images, detects stars in each, runs joint `least_squares` over all frames simultaneously.
  - Uses `--initial-cal` to seed from an existing model
  - Parses UTC from filename with configurable `--utc-offset`
  - Evenly subsamples up to `--max-images` across the full time window
  - Outputs a single shared `FisheyeModel` valid for all frames

### Debug / manual calibration scripts (testing only)
- **`scripts/allsky_debug.py`** — Full calibration or overlay-only render on a single FITS/image. `--overlay-only --cal <json>` skips recalibration.
- **`scripts/allsky_v5_cal.py`** — Hand-confirmed anchor optimisation (9 confirmed pixel↔star pairs: Regulus, Procyon, 7× BigDipper). Uses Hungarian matching for uncertain BigDipper assignment. Saves `sample_images/bigdipper_v5_calibration.json`.
- **`scripts/allsky_multi_cal.py`** — See above.

---

## Key Calibration Research Findings (Jan 2026, observer lat=38.97° lon=-95.24°)

These numbers are specific to the test camera. Document them here so future sessions don't repeat the discovery work.

### Camera physical properties
- **`east_left = True`** — FITS convention (NINA). East is on the LEFT in the image.
- **`axis_alt ≈ 84.5°`** — Camera is mounted ~5.5° off vertical. V2/V5 forced `axis_alt=90` and compensated with unphysical polynomial values. The 3-hour multi-image calibration correctly measured the tilt.
- **`a3 ≈ -47` to `-50`** — Physically reasonable polynomial correction. Values outside roughly `[-60, +10]` indicate the optimizer is compensating for a wrong orientation rather than measuring real lens distortion.

### Calibration quality comparison

| Method | RMS | n stars | Notes |
|--------|-----|---------|-------|
| V2 (wrong formula) | 4.4 px | 7 | BigDipper only, simplified projection — wrong for cross-sky |
| V5 (confirmed anchors) | 8.8 px | 9 | Regulus+Procyon+BigDipper, correct formula, axis_alt forced 90° |
| Multi-image 24 min | 13.9 px | 634 | Too little sky diversity, drifted to wrong local minimum |
| **Multi-image 3 hr** | **12.7 px** | **1165** | Best automated result; physically reasonable parameters |

**V5 has lower RMS because it was tuned to 9 confirmed stars. Multi-3h is more accurate for the overall sky.** For production, multi-3h is the better model despite the higher reported RMS.

### Confirmed pixel↔star pairs (test image `lum_20260116_021511.fits`, UTC 2026-01-16 08:15:11)
These are useful for validating future calibrations. Detection numbers are from `allsky_debug.py` output.

| Star | Alt° | Az° | Pixel (x, y) | Det# |
|------|------|-----|-------------|------|
| Regulus | 62.1 | 163.6 | (1160.5, 2274.4) | #3 |
| Procyon | 47.0 | 226.0 | (1991.3, 2458.4) | #5 |
| Alkaid | 45.3 | 55.3 | (769.6, 1028.4) | #8 |
| Mizar | 49.5 | 47.6 | (916.2, 1006.7) | #9 |
| Alioth | 53.7 | 45.6 | (982.5, 1056.2) | #15 |
| Megrez | 58.7 | 41.7 | (1077.8, 1118.2) | #55 |
| Phecda | 62.6 | 46.2 | (1072.7, 1216.0) | #18 |
| Merak | 67.7 | 31.7 | (1242.8, 1243.3) | #17 |
| Dubhe | 63.7 | 23.1 | (1303.3, 1137.1) | #21 |
| Sirius | 21.3 | 224.6 | ≈(2320, 2869) | #24 (uncertain — crowded region) |

---

## Production Architecture: Layered Confidence Pipeline

The goal for any fresh install: **show a rough overlay on the first clear image; silently improve accuracy over the first 30–60 minutes; never require user intervention.**

```
Image arrives (watcher or camera mode)
        │
        ▼
[Layer 1]  Single-image auto-cal  ─────────────────────────────────────────
        │  calibration.py:calibrate()                                       │
        │  Grid search + roll sweep + iterative least_squares               │
        │  Works on first image, no prior knowledge needed                  │
        │  Result: "Preliminary" quality (~15–20 px RMS)                    │
        │  Stored to %LOCALAPPDATA%\PFRSentinel\allsky_calibration.json     │
        │  Overlay rendered immediately using this model                    │
        ▼
[Layer 2]  Background accumulation service  ────────────────────────────────
        │  NEW: services/allsky/calibration_service.py                      │
        │  Runs as a background thread (already fits QThread pattern)       │
        │  Hooks into the image processing pipeline (watcher + camera)      │
        │  Queues arriving images; doesn't block main pipeline              │
        │  Thresholds:                                                      │
        │    ≥10 images  → joint refinement → "Acceptable" (~12 px)        │
        │    ≥20 images spanning ≥30 min → "Good" (~10 px)                │
        │    ≥40 images spanning ≥60 min → "Excellent" (~7 px)            │
        │  On each quality upgrade: saves new JSON, emits Qt signal        │
        ▼
[Layer 3]  Triangle hash matching (fallback)  ──────────────────────────────
           NEW: services/allsky/triangle_match.py                           │
           Only invoked when Layer 1 returns < min_matches/2 stars          │
           Works with zero prior knowledge of camera orientation            │
           Pre-computed hash index of catalog star triplet shapes           │
           Each matching triplet → pose hypothesis → verify against others  │
           Astrometry.net algorithm, adapted for fisheye polynomial model   │
```

### Calibration Quality Levels

```python
# To add to services/allsky/fisheye.py or a new services/allsky/cal_quality.py
class CalibrationQuality(str, Enum):
    NONE        = "none"         # No calibration file
    PRELIMINARY = "preliminary"  # Single image, ≥8 matches, RMS ≤ 20 px
    ACCEPTABLE  = "acceptable"   # ≥3 images, ≥30 matches, RMS ≤ 15 px
    GOOD        = "good"         # ≥10 images, ≥100 matches, RMS ≤ 12 px
    EXCELLENT   = "excellent"    # ≥20 images, ≥60 min span, RMS ≤ 8 px

def model_quality(model: FisheyeModel, n_images: int = 1,
                  span_minutes: float = 0.0) -> CalibrationQuality:
    if not model.is_valid():
        return CalibrationQuality.NONE
    if n_images >= 20 and span_minutes >= 60 and model.rms_residual <= 8.0:
        return CalibrationQuality.EXCELLENT
    if n_images >= 10 and model.n_matches >= 100 and model.rms_residual <= 12.0:
        return CalibrationQuality.GOOD
    if n_images >= 3 and model.n_matches >= 30 and model.rms_residual <= 15.0:
        return CalibrationQuality.ACCEPTABLE
    return CalibrationQuality.PRELIMINARY
```

---

## What Needs to Be Built (Priority Order)

### 1. ~~`services/allsky/calibration_service.py` — Background accumulation~~ ✅ Built
Background accumulation service, `CalibrationQuality` enum, and `model_quality()` are all
implemented in `services/allsky/calibration_service.py`. `n_images` and `span_minutes` are
stored in `FisheyeModel` and persisted in the calibration JSON. UI quality badge in
`ui/panels/allsky_settings.py` shows none/preliminary/acceptable/good/excellent.

### 2. ~~`services/allsky/triangle_match.py` — Pattern hash fallback~~ ✅ Built
Geometric triangle-hash matching is implemented in `services/allsky/triangle_match.py`
and wired into `calibration.py` — it is invoked automatically when the grid search matches
too few stars or fails the post-fit sanity check. It detects the brightest stars, builds
detected triplets, looks up matching catalog triplets via a runtime-built `TriangleIndex`,
solves each pose hypothesis (Wahba), scores with a binomial false-positive probability, and
feeds the best hypothesis to the shared `_iterative_fit` refiner. The index is built per-call
(fast enough); the bundled `allsky_star_index.bin` precompute was not needed.

> **Reliability note (2026-06-12):** the offline baseline
> (`docs/dev/allsky_baseline_2026-06-12-before.md`) shows single-image grid+triangle
> calibration currently fails the post-fit sanity check on 100% of the reference
> `sample_images` frames — including the hand-confirmed anchor frame. Production accuracy
> comes from the committed multi-image fit, not single-image. Root-cause investigation is
> tracked separately.

---

## Production vs Test Paths

| Context | Image source | Cal file location | UTC source |
|---------|-------------|-------------------|-----------|
| **Production** | `watcher.py` (Watch mode) or `zwo_camera.py` (Camera mode) | `%LOCALAPPDATA%\PFRSentinel\allsky_calibration.json` | System clock / FITS header |
| **Testing** | `sample_images/*.fits` via `scripts/allsky_debug.py` | `sample_images/*_calibration.json` | `--utc` CLI arg (`+6h` offset, filenames are CST) |

**The `sample_images/` calibration JSONs must never be referenced in production code.** The only production path is through `app_config.get_config_dir()` / `os.getenv('LOCALAPPDATA')`.

The `scripts/allsky_*.py` tools are **developer/validation tools only**. They are not called by the production app. Production calibration runs through `ui/controllers/allsky_controller.py`.

---

## Integration Checklist for New Sessions

Before making calibration changes, verify:

- [ ] Projection formula changes → update `fisheye.py:altaz_to_pixel()` only; all other code calls this method
- [ ] New calibration scripts → call `FisheyeModel.altaz_to_pixel()`, never reimplement the formula
- [ ] Calibration file paths → always via `app_config.get_config_dir()` in production; never hardcoded `sample_images/`
- [ ] Background threads → use Qt signals/slots to communicate with UI; no direct GUI calls from threads
- [ ] `calibration.py` line count → hard cap 550 lines (currently ~491). Split into `calibration_grid.py` + `calibration_fit.py` if needed
- [ ] New parameters in `FisheyeModel` → add to `__dataclass_fields__` with a sensible default; `load()` uses dict comprehension so old JSONs forward-migrate automatically

---

## File Map Quick Reference

```
services/allsky/
├── fisheye.py             FisheyeModel — THE projection ground truth
├── calibration.py         Single-image auto-cal (grid + roll sweep + least_squares)
├── calibration_service.py Background accumulation + multi-image refinement (built)
├── triangle_match.py      Hash-based fallback for grid-search failures (built)
├── calibration_validate.py Post-fit validation + resolution-independent tolerances
├── coords.py              radec_to_altaz(), atmospheric_refraction()
├── catalogs.py            get_bright_stars() — BSC5 catalog
├── star_centroid.py       detect_stars(), estimate_sky_circle()
└── overlay_renderer.py    render_allsky_overlay() — production entry point

ui/controllers/allsky_controller.py    CalibrationWorker + AllSkyController
ui/panels/allsky_settings.py           quality badge (built)

scripts/dev/allsky/  (dev/test only — not called by production app)
├── allsky_debug.py         Single-image calibration + overlay render CLI
├── allsky_v5_cal.py        Manual anchor optimisation (confirmed pixel↔star pairs)
├── allsky_multi_cal.py     Multi-image joint calibration CLI
├── validate_calibration.py Residual/component checks against the sample set
└── baseline_run.py         Offline single-image success-rate baseline

%LOCALAPPDATA%\PFRSentinel\
└── allsky_calibration.json   Production calibration (read/write by app)
```

(The bundled `allsky_star_index.bin` triangle precompute described earlier was not
needed — the index builds fast enough per-call.)
