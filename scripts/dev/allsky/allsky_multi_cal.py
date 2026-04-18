"""
Multi-image joint fisheye calibration.

Fits a single shared lens model across a sequence of images from the same
fixed camera.  Because different images capture different sky positions
(Earth's rotation moves stars ~15°/hour), a 20-minute sequence alone
constrains the radial polynomial across all zenith angles.

Why this is better than single-image calibration
-------------------------------------------------
* Stars at low altitude in one image are near-zenith in an image taken
  several hours later — the polynomial is constrained at all θ values.
* Roll + axis_az ambiguity (degenerate at axis_alt=90 in a single image)
  is broken when stars have traced measurable arcs across the frame.
* More total matches → smaller residuals, less over-fitting.

Minimum for useful improvement: 3 images from the same night, ≥30 min apart.
Best: images spanning 4+ hours, or images from multiple clear nights.

Usage
-----
    # Sequential images from one night (filenames are CST → add 6h for UTC):
    python scripts/allsky_multi_cal.py sample_images/ \\
        --pattern "lum_20260116_*.fits" \\
        --lat 38.9717 --lon -95.2353 --utc-offset 6 \\
        --out sample_images/multi_calibration.json

    # Start from a known calibration instead of auto-detecting:
    python scripts/allsky_multi_cal.py sample_images/ \\
        --pattern "lum_2026*.fits" \\
        --lat 38.9717 --lon -95.2353 --utc-offset 6 \\
        --initial-cal sample_images/bigdipper_v5_calibration.json \\
        --out sample_images/multi_calibration.json --render
"""

import argparse
import fnmatch
import json
import logging
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from scipy.optimize import least_squares

from services.allsky.fisheye import FisheyeModel
from services.allsky.catalogs import get_bright_stars
from services.allsky.coords import radec_to_altaz
from services.allsky.star_centroid import detect_stars, estimate_sky_circle
from services.allsky.calibration import _brightness_match, _params_to_model

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ---------------------------------------------------------------------------
# Filename → UTC parsing
# ---------------------------------------------------------------------------

_FNAME_RE = re.compile(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})')


def filename_to_utc(path: str, utc_offset_hours: float) -> Optional[datetime]:
    """
    Parse YYYYMMDD_HHMMSS from a filename and apply a UTC offset.
    e.g.  lum_20260116_021511.fits + offset=+6  →  2026-01-16 08:15:11 UTC
    """
    m = _FNAME_RE.search(os.path.basename(path))
    if not m:
        return None
    yr, mo, dy, hh, mm, ss = (int(x) for x in m.groups())
    local_dt = datetime(yr, mo, dy, hh, mm, ss)
    return (local_dt + timedelta(hours=utc_offset_hours)).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Image loading (FITS + standard formats)
# ---------------------------------------------------------------------------

def load_image(path: str):
    """Load image as PIL RGB.  Returns None on failure."""
    from PIL import Image
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.fits', '.fit', '.fts'):
        try:
            from astropy.io import fits as af
            with af.open(path) as hdu:
                data = hdu[0].data
            if data is None:
                return None
            if data.ndim == 3 and data.shape[0] in (1, 3, 4):
                data = np.moveaxis(data, 0, -1)
            if data.dtype != np.uint8:
                flat = data.flatten().astype(np.float32)
                lo = float(np.percentile(flat, 1))
                hi = float(np.percentile(flat, 99))
                data = ((data.astype(np.float32) - lo) / max(hi - lo, 1) * 255
                        ).clip(0, 255).astype(np.uint8)
            if data.ndim == 2:
                return Image.fromarray(data).convert('RGB')
            if data.shape[2] == 1:
                return Image.fromarray(data[:, :, 0]).convert('RGB')
            return Image.fromarray(data[:, :, :3]).convert('RGB')
        except Exception as e:
            print(f"  FITS load failed ({path}): {e}")
            return None
    try:
        return Image.open(path).convert('RGB')
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-image data preparation
# ---------------------------------------------------------------------------

def prepare_image(path: str, utc: datetime, lat: float, lon: float,
                  max_mag: float = 6.5) -> Optional[dict]:
    """
    Load image, detect stars, compute catalog alt/az for this UTC.
    Returns a dict with keys: path, utc, detected, above_horizon, sky_r
    or None if the image can't be processed.
    """
    print(f"  Loading {os.path.basename(path)} …", end=" ", flush=True)
    img = load_image(path)
    if img is None:
        print("FAILED (could not load)")
        return None

    sky_cx, sky_cy, sky_r = estimate_sky_circle(img)
    detected = detect_stars(img, max_stars=200,
                            sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r)
    print(f"{len(detected)} stars detected")

    catalog = get_bright_stars(max_mag=max_mag)
    above_horizon = []
    for s in catalog:
        alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], lat, lon, utc)
        if float(alt) > 3.0:
            above_horizon.append((s, float(alt), float(az)))
    above_horizon.sort(key=lambda x: x[0]['vmag'])

    return dict(path=path, utc=utc, detected=detected,
                above_horizon=above_horizon, sky_r=sky_r,
                sky_cx=sky_cx, sky_cy=sky_cy)


# ---------------------------------------------------------------------------
# Joint residual function
# ---------------------------------------------------------------------------

def _collect_matches(params, all_frames: List[dict], east_left: bool,
                     tol_px: float) -> Tuple[np.ndarray, np.ndarray,
                                             np.ndarray, np.ndarray]:
    """
    Match detected stars against catalog in every frame, return four arrays:
        det_x, det_y  — detected pixel positions
        cat_alt, cat_az — matching catalog alt/az

    Matches are fixed at the time of calling (the model is not re-evaluated
    inside the subsequent optimisation step), so the residual vector has a
    constant shape that least_squares can handle.
    """
    model = _params_to_model(params, east_left)
    det_x, det_y, cat_alt, cat_az = [], [], [], []
    for frame in all_frames:
        matches = _brightness_match(
            frame['detected'], frame['above_horizon'], model, tol_px=tol_px
        )
        for (dx, dy), _star, (alt, az) in matches:
            det_x.append(dx);  det_y.append(dy)
            cat_alt.append(alt); cat_az.append(az)
    return (np.array(det_x), np.array(det_y),
            np.array(cat_alt), np.array(cat_az))


def _fixed_residuals(params, det_x, det_y, cat_alt, cat_az, east_left):
    """
    Vectorised residuals over a fixed set of (detected, catalog) pairs.
    Returns a 1-D array of length 2 × n_matches so least_squares sees a
    constant shape regardless of how the model parameters change.
    """
    model = _params_to_model(params, east_left)
    px, py, vis = model.altaz_array_to_pixels(cat_alt, cat_az)
    rx = np.where(vis, det_x - px, 30.0)
    ry = np.where(vis, det_y - py, 30.0)
    return np.concatenate([rx, ry])


def joint_rms(params, all_frames: List[dict], east_left: bool) -> float:
    det_x, det_y, cat_alt, cat_az = _collect_matches(
        params, all_frames, east_left, tol_px=25.0
    )
    if len(det_x) == 0:
        return 999.0
    model = _params_to_model(params, east_left)
    px, py, vis = model.altaz_array_to_pixels(cat_alt, cat_az)
    sq = ((det_x[vis] - px[vis]) ** 2 + (det_y[vis] - py[vis]) ** 2)
    return float(np.sqrt(sq.mean())) if len(sq) > 0 else 999.0


# ---------------------------------------------------------------------------
# Main calibration loop
# ---------------------------------------------------------------------------

def multi_calibrate(
    frames: List[dict],
    initial_model: FisheyeModel,
    n_iterations: int = 6,
) -> Tuple[FisheyeModel, float]:
    """
    Iteratively refine a shared lens model across all frames.

    Each iteration:
      1. Find brightness-matches in every frame with current model
      2. Run joint least_squares over all matches
      3. Tighten match tolerance as fit improves
    """
    east_left = initial_model.east_left
    # Normalise axis_az to [0, 360) so it stays within the optimiser bounds.
    axis_az_norm = initial_model.axis_az % 360.0
    params = np.array([
        initial_model.cx, initial_model.cy,
        initial_model.a1, initial_model.a3, initial_model.a5,
        initial_model.roll, initial_model.axis_alt, axis_az_norm,
    ])

    bounds_lo = [50,   50,    50,  -1e4, -1e6, -math.pi,  45.0,    0.0]
    bounds_hi = [4000, 4000, 2000,  1e4,  1e6,  math.pi,  90.0,  360.0]

    total_matches = 0
    rms = joint_rms(params, frames, east_left)
    print(f"  Initial joint RMS: {rms:.2f} px")

    for iteration in range(n_iterations):
        tol = max(10.0, 50.0 - iteration * 7.0)

        # Fix matches at the START of this iteration; residual shape stays
        # constant throughout the least_squares call.
        det_x, det_y, cat_alt, cat_az = _collect_matches(
            params, frames, east_left, tol_px=tol
        )
        total_matches = len(det_x)
        if total_matches < 6:
            print(f"  Iteration {iteration}: too few matches ({total_matches}), stopping")
            break

        try:
            result = least_squares(
                _fixed_residuals, params,
                args=(det_x, det_y, cat_alt, cat_az, east_left),
                bounds=(bounds_lo, bounds_hi),
                method='trf',
                max_nfev=12000,
                ftol=1e-7,
                xtol=1e-7,
            )
            params = result.x
        except Exception as e:
            print(f"  Iteration {iteration}: least_squares failed — {e}")
            break

        rms = joint_rms(params, frames, east_left)
        print(f"  Iteration {iteration}: {total_matches} matches across "
              f"{len(frames)} frames, RMS={rms:.2f} px, tol={tol:.0f} px")

        if rms < 2.0:
            break

    model = _params_to_model(params, east_left)
    model.rms_residual = round(rms, 4)
    model.n_matches = total_matches
    return model, rms


# ---------------------------------------------------------------------------
# Per-frame report
# ---------------------------------------------------------------------------

def per_frame_report(model: FisheyeModel, frames: List[dict]) -> None:
    """Print per-image match count and RMS after final fit."""
    print(f"\n{'Image':<35} {'Matches':>8} {'RMS':>8}")
    print("-" * 55)
    for frame in frames:
        matches = _brightness_match(
            frame['detected'], frame['above_horizon'], model, tol_px=20.0
        )
        if not matches:
            print(f"  {os.path.basename(frame['path']):<33} {'0':>8} {'n/a':>8}")
            continue
        sq = []
        for (dx, dy), _star, (alt, az) in matches:
            xy = model.altaz_to_pixel(alt, az)
            if xy is not None:
                sq.append((dx - xy[0]) ** 2 + (dy - xy[1]) ** 2)
        rms = math.sqrt(sum(sq) / len(sq)) if sq else 999.0
        print(f"  {os.path.basename(frame['path']):<33} {len(matches):>8} {rms:>7.2f}px")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Multi-image joint fisheye calibration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("directory", help="Directory containing images")
    parser.add_argument("--pattern", default="*.fits",
                        help="Glob pattern to filter images (default: *.fits)")
    parser.add_argument("--lat",        type=float, required=True)
    parser.add_argument("--lon",        type=float, required=True)
    parser.add_argument("--utc-offset", type=float, default=0.0,
                        help="Hours to ADD to filename time to get UTC "
                             "(e.g. 6 for CST, 5 for CDT, 0 if filenames are UTC)")
    parser.add_argument("--initial-cal", default=None,
                        help="Path to starting calibration JSON. "
                             "If omitted, auto-calibrates on the first image.")
    parser.add_argument("--out", required=True,
                        help="Output calibration JSON path")
    parser.add_argument("--max-images", type=int, default=20,
                        help="Maximum images to use (default 20; pick evenly spaced)")
    parser.add_argument("--render", action="store_true",
                        help="Render a debug overlay on each image after calibration")
    parser.add_argument("--max-mag", type=float, default=6.5)
    args = parser.parse_args()

    # --- Collect files ---
    all_files = sorted(
        os.path.join(args.directory, f)
        for f in os.listdir(args.directory)
        if fnmatch.fnmatch(f.lower(), args.pattern.lower())
    )
    if not all_files:
        sys.exit(f"No files matching {args.pattern!r} in {args.directory}")

    # Evenly subsample if too many
    if len(all_files) > args.max_images:
        step = len(all_files) / args.max_images
        all_files = [all_files[int(i * step)] for i in range(args.max_images)]

    print(f"\n{len(all_files)} images selected:")
    for f in all_files:
        utc = filename_to_utc(f, args.utc_offset)
        print(f"  {os.path.basename(f)}  UTC={utc}")

    # --- Prepare frames ---
    print("\n--- Detecting stars ---")
    frames = []
    for path in all_files:
        utc = filename_to_utc(path, args.utc_offset)
        if utc is None:
            print(f"  Skipping {path} — cannot parse UTC from filename")
            continue
        frame = prepare_image(path, utc, args.lat, args.lon, args.max_mag)
        if frame is not None:
            frames.append(frame)

    if len(frames) < 2:
        sys.exit("Need at least 2 images with detectable stars.")

    total_detections = sum(len(f['detected']) for f in frames)
    print(f"\n{len(frames)} frames ready, {total_detections} total star detections")

    # --- Initial model ---
    if args.initial_cal:
        model = FisheyeModel.load(args.initial_cal)
        print(f"\nInitial model loaded: {model}")
    else:
        print("\n--- Auto-calibrating on first image for initial model ---")
        from services.allsky.calibration import calibrate
        first = frames[0]
        img0 = load_image(first['path'])
        try:
            model = calibrate(
                img0, lat_deg=args.lat, lon_deg=args.lon, dt=first['utc'],
                min_matches=6,
            )
            print(f"Auto-cal succeeded: {model}")
        except Exception as e:
            sys.exit(f"Auto-calibration on first image failed: {e}\n"
                     "Use --initial-cal to provide a starting model.")

    # --- Joint optimisation ---
    print(f"\n--- Joint optimisation over {len(frames)} frames ---")
    final_model, rms = multi_calibrate(frames, model)

    print(f"\n--- Final model (joint RMS = {rms:.2f} px) ---")
    print(f"  cx={final_model.cx:.2f}  cy={final_model.cy:.2f}")
    print(f"  a1={final_model.a1:.2f}  a3={final_model.a3:.4f}  "
          f"a5={final_model.a5:.6f}")
    print(f"  roll={math.degrees(final_model.roll):.3f}°  "
          f"axis_alt={final_model.axis_alt:.3f}°  "
          f"axis_az={final_model.axis_az:.3f}°")
    print(f"  east_left={final_model.east_left}")

    per_frame_report(final_model, frames)

    # --- Save ---
    final_model.calibrated_at = datetime.now(timezone.utc).isoformat()
    final_model.save(args.out)
    print(f"\nSaved → {args.out}")

    # --- Optional render ---
    if args.render:
        print("\n--- Rendering overlays ---")
        from scripts.allsky_debug import _load_image as _dbg_load  # noqa: F401
        import subprocess
        for frame in frames:
            stem = os.path.splitext(frame['path'])[0] + '_multi'
            utc_str = frame['utc'].strftime('%Y-%m-%d %H:%M:%S')
            cmd = [
                sys.executable, 'scripts/allsky_debug.py', frame['path'],
                '--overlay-only', '--cal', args.out,
                '--lat', str(args.lat), '--lon', str(args.lon),
                '--utc', utc_str,
                '--out', stem, '--stars', '15', '--con-width', '2',
            ]
            subprocess.run(cmd, check=False)
            print(f"  → {stem}_3_overlay.jpg")


if __name__ == '__main__':
    main()
