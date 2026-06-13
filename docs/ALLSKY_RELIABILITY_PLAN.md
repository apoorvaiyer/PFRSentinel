# All-Sky Overlay & Calibration Reliability Plan

> **Context:** Produced from a full review of `services/allsky/` and its pipeline integration
> on 2026-06-12 (branch `fix/camera-wedged-roi-recovery`, graph built at `1ce831b`).
> File:line references are as of that review — re-verify before editing.
>
> Companion doc: [`ALLSKY_CALIBRATION_PLAN.md`](ALLSKY_CALIBRATION_PLAN.md) covers the
> calibration *architecture*; this doc covers the defects found in the shipped
> implementation and the order to fix them.

## Symptoms (user-reported)

1. Auto plate solving (calibration) succeeds very inconsistently — "very varied if it works".
2. The overlay is not always aligned with the sky.
3. **Requirement:** the all-sky overlay must appear only on the *visual side* (GUI preview),
   never baked into output images or video (file sink, web `/latest`, Discord, timelapse).

## Findings summary

| # | Finding | Severity | Phase |
|---|---------|----------|-------|
| F1 | Overlay is burned into file/web/Discord outputs; preview shows it only because it displays the output frame. No preview-only path exists. | High (requirement violation) | 1 |
| F2 | `validate_bright_anchors` (`min_alt_deg=10`) demands detections in the 10–13.5° altitude band that the 15%-trimmed detection mask + 20 px border physically exclude — correct solves are rejected depending on where the bright stars sit that night. Gates *every* calibration path. | High | 2 |
| F3 | Background refinement accepts a new model on quality-*rank* upgrade alone (`calibration_service.py:457-463`); rank is driven by frame count/span, so a 15–20 px model overwrites a 3 px model on disk just by running longer. | High | 2 |
| F4 | Calibration is fed the display-processed frame (post auto-brightness 0.5–4.0×, saturation, sharpening, timestamp text) — `image_processor.py:340`. Combined with hard-coded 8-bit thresholds in `estimate_sky_circle` / `detect_stars`, detection results fluctuate frame to frame. | High | 2 |
| F5 | `FisheyeModel` records no image dimensions; the renderer applies `cx/cy/a1` to whatever image it gets. Changing `resize_percent` after calibrating silently misaligns the overlay by that ratio. | High | 3 |
| F6 | Watch mode never feeds the calibration service, and watch-mode "Calibrate Now" falls back to the saved output image (already resized + already overlaid), then resizes it *again* (`allsky_controller.py:139-150, 286-320`). | High | 3 |
| F7 | `_obs_utc` is stamped at *processing* time (`processor.py:157`), not capture time. Sky rotates ~0.25°/min — watch-mode backlog or long exposures shift the overlay. FITS `DATE-OBS` is ignored. | Medium | 3 |
| F8 | Multi-image refinement validates bright anchors against only the **last** buffered frame (`multi_calibrate.py:125-128`) — one cloudy frame rejects a good joint fit; a degenerate one skips validation entirely. | Medium | 2 |
| F9 | Calibration failures logged at DEBUG (`calibration_service.py:337-339`, `image_processor.py:343-344`) — invisible at default level; user just sees "nothing happened". | Medium | 2 |
| F10 | Match tolerances are hard-coded pixels (50/35/40 px) — behaviour changes with `resize_percent`. Triangle fallback seeds `a1` from the 15%-trimmed radius (~15% small). | Medium | 4 |
| F11 | Manual "Calibrate Now" can race a running background refinement: `set_model()` swaps `_model` without cancelling the in-flight worker, whose result can then overwrite the fresh manual calibration. | Medium | 4 |
| F12 | `_save_model` resolves the path by hand via `os.getenv('LOCALAPPDATA')` instead of `app_config.get_config_dir()` (rule violation); save failures are swallowed. | Low | 4 |
| F13 | Refined model's `n_images`/`span_minutes` are read from the live buffer at completion, including frames the fit never saw — quality is attributed to unfitted data. | Low | 4 |
| F14 | Docs drift: CLAUDE.md still describes an RTSP sink that no longer exists; `ALLSKY_CALIBRATION_PLAN.md` lists `calibration_service.py` / `triangle_match.py` as TODO though both ship. | Low | 5 |

---

## Implementation status (2026-06-13 session)

Completing the follow-up review items on top of the already-merged Phase 1–2 work:

| Item | Status | Notes |
|---|---|---|
| F5 (triangle dims) | ✅ Fixed | `triangle_calibrate` now stamps `image_width/height`; previously the fallback saved dims `0,0` and the renderer skipped scaling — reintroducing the resize misalignment. |
| Phase 1 acceptance test | ✅ Added | `tests/test_image_output.py::TestAllSkyOutputCleanliness` — proves `add_overlays` ignores `__allsky_config` (with a non-vacuity guard). Dead `_inject_allsky_metadata` + bake-in branch deleted. |
| F6 (watch frame / disk fallback) | ✅ Fixed | Disk fallback + double-resize removed from `_get_latest_frame`; watch mode caches the clean output frame in `_on_image_processed`. Resolution handled by F5 model-scaling, not a manual pre-resize. |
| F9 (log visibility) | ✅ Done | Per-cooldown WARNING summary of skipped frames added to `CalibrationService.feed_frame`. |
| F10 (tolerances) | ✅ Done | `tol_scale(sky_r)` in `calibration_validate.py`; all 50/35/40 px tolerances now scale from `REF_SKY_R_PX=1563`. Behaviour-neutral at native resolution. Triangle `a1_est` seeds from the untrimmed radius. |
| F12 (save path) | ✅ Resolved | New `app_config.get_calibration_path()` centralises the path; kept at `%LOCALAPPDATA%` per `.claude/rules/allsky.md` (the plan's literal `get_config_dir()` → `%APPDATA%` would contradict that rule and the read path). Save-failure `status_changed` was already present. |
| F14 (docs) | ✅ Done | `ALLSKY_CALIBRATION_PLAN.md` file map / TODOs corrected. |
| F3, F8, F11, F13 | ✅ Verified | Already implemented in the merged Phase 2 work; confirmed during this review. |
| F7 (capture-time `_obs_utc`) | ⏸ Deferred | Still stamped at processing time; unchanged this session. |

### ⚠️ Offline baseline finding (blocker for symptom #1)

`scripts/dev/allsky/baseline_run.py` over the 130-frame `sample_images` set
(`docs/dev/allsky_baseline_2026-06-12-before.md`):
**single-image `calibrate()` fails the post-fit sanity check on 0/130 frames** — including
`lum_20260116_021511.fits`, the frame with hand-confirmed anchors. The grid fit converges to
wrong orientations (anchors 40–500 px off; `a3` pinned at its bounds) and the triangle
fallback does not recover it. This is the true face of user symptom #1 ("plate solving very
varied"): single-image auto-cal essentially never succeeds on this camera; only the committed
multi-image fit does. F10 is behaviour-neutral at native resolution, so it neither caused nor
fixes this. **Root-cause investigation is a separate workstream** (candidate areas: grid
search orientation coverage, the `a3 ∈ [-80, 20]` plausibility bounds vs. this lens, and
whether the Phase 2 anchor gate is too strict for single-image fits).

---

## Phase 1 — Preview-only overlay (requirement)

**Goal:** the all-sky overlay appears in the live-monitoring preview and nowhere else.

The seam already exists: `ImageProcessorWorker` dual-renders when timelapse wants a clean
frame (`image_processor.py:361-376`). Invert the design so the *clean* render is the
canonical output and the all-sky render is preview-only — and render all-sky **after** the
base overlays, on a copy, so `add_overlays()` runs once instead of twice:

### 1.1 Camera mode (`ui/controllers/image_processor.py`)

- Remove `__allsky_config` from the metadata passed to `add_overlays()` for the output
  image (stop calling `_inject_allsky_metadata` into the output path).
- After the base render:
  ```
  output_img  = add_overlays(img, overlays, metadata, ...)          # no all-sky, saved/pushed
  preview_img = render_allsky_overlay(output_img.copy(), cfg, md)   # preview only, when enabled
  ```
- Change `processing_complete = Signal(object, dict, str)` →
  `Signal(object, object, dict, str)` carrying `(preview_img, output_img, metadata, output_path)`.
  Update the relay in `ImageProcessorQt` (`image_processor.py:468, 588-590`) and the consumer
  `_MainWindowOutputMixin._on_image_processed` (`ui/main_window/output.py:184`):
  `live_panel.update_preview(preview_img, …)`; `_push_to_output_servers(output_path, output_img)`.
- Save `output_img` to disk (the file sink is an output, per requirement).
- Timelapse: `img_for_timelapse` becomes `output_img` unless the user has explicitly opted
  in via `timelapse.include_allsky_overlay` (keep the flag as the *only* sanctioned way to
  bake the overlay into anything; render a second all-sky pass for that case only).
  The `needs_timelapse_no_allsky` dual-render block is then deleted.

### 1.2 Watch mode (`services/processor.py` + `ui/controllers/watch_controller.py`)

- `process_image()` stops injecting `__allsky_config` — its returned/saved image is always
  clean. (Camera mode no longer uses the injection either; delete `_inject_allsky_metadata`
  or reduce it to building the config dict for the preview render.)
- `WatchControllerQt._on_file_processed` (runs on the watchdog thread — acceptable for CPU
  work per controller rules) renders the all-sky preview copy before emitting
  `image_processed` (`watch_controller.py:24, 77`), and the main window routes the preview
  copy to `update_preview` and the clean image to the sinks. Mirror the camera-mode signal
  shape: `image_processed = Signal(object, object, str)` `(preview_img, output_img, path)`.

### 1.3 Acceptance

- With all-sky enabled: preview shows the overlay; the saved file, `/latest`, and the next
  Discord post do not. Timelapse frames clean unless `include_allsky_overlay=True`.
- With all-sky disabled: behaviour identical to today, one render pass.
- `tests/test_image_output.py` extended: `process_image` output contains no all-sky pixels
  even when `allsky_overlay.enabled=True` with a valid calibration file.

---

## Phase 2 — Calibration reliability (F2, F3, F4, F8, F9)

**Goal:** the same sky calibrates the same way every night, and a good model is never
silently replaced by a worse one.

### 2.1 Anchor validation vs detection mask (F2)

In `calibration_validate.py:validate_bright_anchors`, derive the altitude floor from the
mask geometry instead of the hard-coded 10°: the usable sky after `trim_fraction=0.15` and
`border_px=20` ends at ~13.5° for an equidistant lens. Either:
- raise `min_alt_deg` default to **15.0**, or (better)
- pass the effective trim through from the caller and compute
  `min_alt_deg = 90 * (trim_fraction + border_px/sky_r) + margin`.

Add a regression test: a synthetic frame whose 6 brightest catalog anchors include two in
the 10–14° band must still validate.

### 2.2 Refinement regression guard (F3)

In `calibration_service.py:_on_refine_done`, replace the rank-only acceptance with:

```python
rms_ok = (not self._model) or model.rms_residual <= self._model.rms_residual * 1.15
improved = rms_ok and (
    CalibrationQuality.rank(new_q) > CalibrationQuality.rank(self._quality)
    or (model.rms_residual < self._model.rms_residual
        and model.n_matches >= self._model.n_matches)
)
```

A rank upgrade may never carry more than a bounded RMS regression (15% chosen so a
genuinely-better multi-image fit with honest residuals, like the 8.8→12.7 px case in the
research notes, is still expressible via the rank path *only when* residuals are close —
revisit the bound against real data). Log rejections at INFO with both RMS values.

### 2.3 Clean calibration input (F4)

Feed `feed_frame()` the clean post-resize/post-stretch frame (`stretched_for_preview` is
already captured at `image_processor.py:228`, same pixel geometry as the render target)
instead of the brightness/saturation/sharpen/timestamp-decorated `img`. The timestamp text
and sharpening halos are active harms to centroiding; per-frame auto-brightness is the main
run-to-run variance source.

### 2.4 Multi-frame anchor validation (F8)

In `multi_calibrate.py`, validate bright anchors against the **best k of N** frames (e.g.
pass if any 2 of the 3 most recent frames validate) rather than only `frames[-1]`.

### 2.5 Logging visibility (F9)

- Frame-detection failures and feed-skip exceptions: DEBUG → WARNING (keep per-frame
  "too few stars" skips at DEBUG to avoid log spam on cloudy nights, but emit a one-line
  WARNING summary per cooldown cycle: "calibration skipped N frames: too few stars").
- `calibration.py:143`: narrow `except (CalibrationError, Exception)` to `CalibrationError`;
  let programming errors surface.

---

## Phase 3 — Geometry & time correctness (F5, F6, F7)

**Goal:** a calibration is valid for exactly one pixel geometry and one instant — make both
explicit.

### 3.1 Resolution-bind the model (F5)

- Add `image_width: int = 0`, `image_height: int = 0` to `FisheyeModel` (dataclass defaults
  → old JSONs forward-migrate via the existing `load()` dict comprehension).
- Set them in `calibrate()`, `triangle_calibrate()`, and `refine_from_detections()`.
- In `overlay_renderer.render_allsky_overlay`, if the target image size differs from the
  model's recorded size, scale a *copy* of the model: `s = w_target / w_model`; `cx, cy,
  a1, a3, a5` all scale by `s` (the radial polynomial is linear in pixel radius). Log at
  INFO the first time a scale ≠ 1 is applied. If aspect ratios differ, skip the overlay and
  WARN — that's a crop, not a resize, and cannot be recovered by scaling.

### 3.2 Watch-mode calibration input (F6)

- Feed the calibration service from the watch pipeline too: after `process_image()`
  produces its clean resized frame, hand it to `feed_frame()` (plumb the service into
  `FileWatcher`/`WatchControllerQt` the same way the camera worker holds it).
- `AllSkyController._get_latest_frame()`: delete the `last_processed_image`-from-disk
  fallback (it is resized *and* overlay-contaminated). If no clean cached frame exists,
  fail with a clear status message ("start capture / wait for a frame, then calibrate").
  Fix the docstring claiming `_cached_raw_image` is set in both modes; set it for watch
  mode instead (cache the clean pre-overlay frame from `process_image`).
- Remove the double `resize_percent` application (`allsky_controller.py:139-150`) once the
  input is guaranteed to be the clean already-resized frame.

### 3.3 Capture-time `_obs_utc` (F7)

- Watch mode: parse FITS `DATE-OBS` (or the sidecar timestamp) in `process_image` and pass
  it through; fall back to file mtime, then `now(UTC)`. Half the exposure duration should
  be added when `EXPTIME` is available (mid-exposure is the correct sky instant).
- Camera mode: stamp `_obs_utc` from the capture timestamp the worker already receives,
  not `datetime.now()` at overlay time.
- Use the same instant for `feed_frame(dt=…)` so calibration and rendering share a clock.

---

## Phase 4 — Hardening (F10, F11, F12, F13)

- **F10:** express match tolerances as fractions of the estimated sky radius
  (e.g. `tol_px = sky_r * 0.065` ≈ 50 px at the reference resolution) in
  `calibration.py`, `triangle_match.py`, `multi_calibrate.py`, `calibration_validate.py`.
  Triangle fallback: seed `a1_est` from the *untrimmed* fit radius
  (`r_fit`, before the 15% trim) — plumb it through or divide by `(1 - trim_fraction)`.
- **F11:** add a generation counter to `CalibrationService`; `set_model()` increments it,
  `_on_refine_done` discards results whose generation is stale.
- **F12:** `_save_model` → resolve via `app_config.get_config_dir()`; on save failure emit
  `status_changed` so the panel shows the divergence instead of swallowing it.
- **F13:** snapshot `n_images`/`span_minutes` when the refine worker is launched, not when
  it completes.

---

## Phase 5 — Docs & tests ✅ Complete (2026-06-12)

- ✅ Updated CLAUDE.md: removed the RTSP sink (stale).
- ✅ Updated `ALLSKY_CALIBRATION_PLAN.md`: marked `calibration_service.py` as built.
- ✅ New tests `tests/test_allsky_calibration_validate.py`:
  - `validate_bright_anchors` altitude-floor regression (`test_altitude_floor_15_excludes_dead_zone`).
- ✅ New tests `tests/test_allsky_reliability.py` (`TestRefineGuard`, `TestFisheyeModelScaling`):
  - `_on_refine_done` guard: worse-RMS rank upgrade rejected; bounded regression accepted.
  - `FisheyeModel` scaling: 50% and 25% scale, positions ratio exactly matches scale factor.
  - Identity scale, default zero-dimensions, stored width/height.
- Deferred: `_obs_utc` propagation from FITS `DATE-OBS` (Phase 3.3, not implemented).
- Deferred: output cleanliness test in `test_image_output.py` (Phase 1 acceptance test still pending).

## Offline validation protocol (do this before AND after Phase 2/4 changes)

Calibration changes must be validated offline — do not rely on "wait for a clear night".
The repo ships a real dataset:

- **`sample_images/lum_20260116_*.fits`** — ~130 consecutive frames spanning 02:00–05:00
  (filenames are **CST**; true UTC = filename **+6 h**). Observer: **lat 38.97, lon −95.24**.
- **Confirmed star↔pixel anchors** for `lum_20260116_021511.fits` (UTC 2026-01-16 08:15:11)
  are tabulated in [`ALLSKY_CALIBRATION_PLAN.md`](ALLSKY_CALIBRATION_PLAN.md) §"Key
  Calibration Research Findings" — Regulus, Procyon, the Big Dipper seven. Any model that
  misses these by >40 px is wrong regardless of its reported RMS.
- Dev tools (all in `scripts/dev/allsky/`, none used by production):
  `allsky_debug.py` (single-frame calibrate + overlay render),
  `validate_calibration.py` (residual report against a cal JSON),
  `allsky_multi_cal.py` (joint multi-frame fit).

**Baseline procedure:** before touching Phase 2 code, run single-image `calibrate()` over
every frame in the set (a ~20-line throwaway script around `allsky_debug.py` logic; pass
the +6 h UTC and lat/lon above). Record per frame: success/failure, n_matches, RMS, and
the validation failure reason. Commit the summary (CSV or markdown) to
`docs/dev/allsky_baseline_<date>.md`. After each Phase 2 change, re-run:

- Success rate must increase (the F2 fix alone should convert a chunk of the failures).
- No frame that previously solved correctly (verified against the anchor table) may flip
  to failed or drift >5 px on the anchors.
- Phase 2.2: replay the run as a sequence through `CalibrationService` thresholds and
  confirm the on-disk model's anchor accuracy never degrades as frames accumulate.

## Compatibility & migration notes

- **Existing calibration JSONs have no `image_width`/`image_height`** (Phase 3.1). The
  dataclass defaults (0) make `load()` forward-migrate; the renderer must treat 0 as
  "unknown — assume target size, never scale, log once at INFO". Do not invalidate
  existing user calibrations.
- **No new config keys are required for Phase 1.** `timelapse.include_allsky_overlay`
  has been **removed** (open decision #2 resolved: never on any output). The
  `allsky_overlay.utc_offset_hours` key remains only for the legacy metadata-datetime path
  used by dev scripts/tests — production uses `_obs_utc` and, after Phase 3.3, capture time.
- **Watch-mode metadata is `{}`** at the main-window boundary today (`capture.py:643-644`)
  — don't assume tokens exist there when reshaping the signals.

## Phase 1 signal-change checklist (complete consumer list at review time)

| Signal | Wire-up | Action |
|---|---|---|
| worker `processing_complete` | `image_processor.py:479` (relay), `window.py:306` → `_on_image_processed` | new `(preview_img, output_img, metadata, path)` shape |
| controller `processing_complete` | `image_processor.py:468, 588-590` | mirror new shape |
| worker `preview_ready` | `image_processor.py:480`, `window.py:307` | unchanged (histogram only) — consider renaming to `histogram_ready` to stop the image arg being mistaken for the preview path again |
| worker `timelapse_ready` | `image_processor.py:482`, `window.py:273` (timelapse controller), `window.py:286` (**meteor controller**) | meteor detection must keep receiving the **clean** frame — overlay graphics would trigger false meteor candidates |
| watch `image_processed` | `watch_controller.py:24, 77`, `capture.py:643` | new `(preview_img, output_img, path)` shape |

Run the app once per mode after rewiring (`/verify`-style: camera sim or watch a folder of
`sample_images/` copies) — Qt signal-arity mismatches fail at connect/emit time, not import time.

## Invariants — do not change while executing this plan

- `fisheye.py:altaz_to_pixel()` is the projection ground truth (see
  `.claude/rules/allsky.md`); nothing in this plan requires touching it. Phase 3.1 scales a
  *copy* of the model's parameters, not the projection math.
- The 50→10 px tightening schedule over 8 iterations in `_iterative_fit` stays as-is
  (Phase 4's tolerance work scales the *endpoints* by resolution, not the schedule shape).
- The layered-confidence design (single-image → background refinement → triangle fallback)
  stays; this plan fixes its gates, it does not restructure it.
- Per-file size caps apply (`calibration.py` hard cap 550; service files 750) — extract
  modules rather than growing files; the hook will block otherwise.
- Run the `pfr-reviewer` subagent + `/pre-commit-check` after each phase.

## Open decisions — **all resolved 2026-06-12**

1. **Phase 2.2 RMS regression bound**: **15%** — implemented.
   Tune further against a real night's logs if drift patterns emerge.
2. **`timelapse.include_allsky_overlay`**: **Removed** — all-sky overlay never
   appears in any output sink (file, web, Discord, timelapse). Preview-only.
   The flag and the second render have been deleted.
3. **Watch-mode auto-calibration**: **Manual-only for now** — watch mode does
   not feed the background `CalibrationService`. Users who want auto-calibration
   must use camera-capture mode.
4. **Preview cost**: no action taken — acceptable for the current frame rate.

## Sequencing & risk notes

- Phases are ordered by user impact; each is independently shippable. Phase 1 and Phase 2
  touch disjoint code and can land as separate branches.
- Phase 1 changes two public signal signatures — update every `connect()` in the same
  commit (`window.py:306`, `capture.py:643-644` were the consumers at review time).
- Phase 2.2's 15% bound is a judgement call: validate against a real night's log before
  and after. Keep the rejected-refinement INFO logs for at least one release to tune it.
- Phase 3.1 scaling assumes uniform resize. Binned/ROI camera frames change aspect or crop —
  the WARN-and-skip path covers that; do not attempt to scale through crops.
- File-size budgets: `calibration_service.py` and `overlay_renderer.py` are near the soft
  cap — Phase 2/3 additions may require extracting (e.g. `cal_quality.py`, model-scaling
  helper in `fisheye.py`) per `.claude/rules/python-general.md`.
