#!/usr/bin/env python3
"""
Grid-search all-sky fisheye calibration.

Unlike iterative calibration (which needs a good seed), this tool exhaustively
searches the 3-D orientation space (axis_alt, axis_az, roll) to find the camera
orientation that gives the most star-pixel matches.  It then refines all model
parameters with a local optimizer.

Immune to the local-minimum problem; requires no prior knowledge of orientation.

Usage:
    python scripts/allsky_gridcal.py sample_images/lum_20260116_021511.fits \\
        --lat 52 --lon -1 --utc "2026-01-16 02:15:10"

    # Override sky-circle if auto-detection is unreliable (equipment obstruction):
    python scripts/allsky_gridcal.py ... --cx 1640 --cy 1775 --sky-r 1400
"""
import os, sys, re, math, json, argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Vectorised batch projection  (axis_alt, axis_az, roll) × catalog stars
# ---------------------------------------------------------------------------

def _project_batch(alt_r, az_r, cx, cy, a1, a3, a5,
                   aa_r, at_r, roll_r):
    """
    Project N stars for G grid points simultaneously.

    alt_r, az_r       : (N,) radians
    aa_r, at_r, roll_r: (G,) radians  (axis_az, axis_alt, roll)

    Returns px (G,N), py (G,N), valid (G,N)
    """
    # ENU unit vectors for each star: broadcast as (1, N)
    vx = (np.cos(alt_r) * np.sin(az_r))[None]
    vy = (np.cos(alt_r) * np.cos(az_r))[None]
    vz = np.sin(alt_r)[None]

    # Grid angles → (G, 1) for broadcasting
    aa = aa_r[:, None]
    tilt = (np.pi / 2.0) - at_r[:, None]   # zenith tilt
    rl = roll_r[:, None]

    # Rotate by axis_az
    ca, sa = np.cos(-aa), np.sin(-aa)
    vx2 = ca * vx - sa * vy
    vy2 = sa * vx + ca * vy

    # Rotate by tilt
    ct, st = np.cos(-tilt), np.sin(-tilt)
    vy3 = ct * vy2 - st * vz
    vz3 = st * vy2 + ct * vz

    # Rotate by roll
    cr, sr = np.cos(-rl), np.sin(-rl)
    vx4 = cr * vx2 - sr * vy3
    vy4 = sr * vx2 + cr * vy3
    vz4 = vz3

    # Fisheye polynomial
    theta = np.arctan2(np.sqrt(vx4 ** 2 + vy4 ** 2), vz4)
    t2 = theta * theta
    r_px = a1 * theta + a3 * t2 * theta + a5 * t2 * t2 * theta
    phi = np.arctan2(vx4, vy4)

    px = cx - r_px * np.sin(phi)
    py = cy - r_px * np.cos(phi)

    valid = (alt_r[None] >= np.radians(5.0)) & (r_px >= 0)
    return px, py, valid


def _score_batch(px, py, valid, det_xy, radius_sq):
    """
    Count how many (G×N) projected positions land within sqrt(radius_sq)
    of any detected star.  Returns integer scores array (G,).
    """
    G, N = px.shape
    # (G, N, 1) vs (1, 1, D): only ~500×80×50 = 2 M elements — fine
    dx = px[:, :, None] - det_xy[None, None, :, 0]
    dy = py[:, :, None] - det_xy[None, None, :, 1]
    dist2 = dx * dx + dy * dy
    matched = (dist2.min(axis=2) < radius_sq) & valid
    return matched.sum(axis=1).astype(np.int32)


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------

def grid_search(alt_r, az_r, cx, cy, a1, det_xy,
                alt_lo=70.0, alt_hi=90.0, alt_step=2.0,
                az_lo=None, az_hi=None, az_step=5.0,
                roll_lo=-90.0, roll_hi=90.0, roll_step=5.0,
                radius_px=30.0, batch=500, label='coarse'):

    alt_vals  = np.arange(alt_lo,   alt_hi  + 1e-9, alt_step)
    if az_lo is not None and az_hi is not None:
        az_vals = np.arange(az_lo, az_hi + 1e-9, az_step)
    else:
        az_vals   = np.arange(0.0,  360.0, az_step)
    roll_vals = np.arange(roll_lo,  roll_hi + 1e-9, roll_step)

    # Build flat grid
    AA, AZ, RL = np.meshgrid(alt_vals, az_vals, roll_vals, indexing='ij')
    g_alt  = AA.ravel()
    g_az   = AZ.ravel()
    g_roll = RL.ravel()
    G_total = len(g_alt)

    print(f"  {label}: {len(alt_vals)}x{len(az_vals)}x{len(roll_vals)} = {G_total} pts")

    best_score = 0
    best_idx   = 0
    scores_all = np.zeros(G_total, dtype=np.int32)
    radius_sq  = radius_px ** 2

    for start in range(0, G_total, batch):
        end = min(start + batch, G_total)
        aa_r  = np.radians(g_az[start:end])
        at_r  = np.radians(g_alt[start:end])
        roll_r = np.radians(g_roll[start:end])

        px, py, valid = _project_batch(alt_r, az_r, cx, cy, a1, 0.0, 0.0,
                                        aa_r, at_r, roll_r)
        sc = _score_batch(px, py, valid, det_xy, radius_sq)
        scores_all[start:end] = sc

        top = sc.max() if len(sc) else 0
        if top > best_score:
            best_score = top
            best_idx   = start + sc.argmax()

    print(f"  best score = {best_score}  "
          f"axis_alt={g_alt[best_idx]:.1f}  "
          f"axis_az={g_az[best_idx]:.1f}  "
          f"roll={g_roll[best_idx]:.1f}")

    return g_alt[best_idx], g_az[best_idx], g_roll[best_idx], best_score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fits(path):
    from astropy.io import fits
    with fits.open(path) as hdul:
        data = hdul[0].data.astype(np.float32)
        hdr  = hdul[0].header
    if data.ndim == 3 and data.shape[0] in (1, 3, 4) and data.shape[0] < data.shape[1]:
        data = np.transpose(data, (1, 2, 0))
    if data.ndim == 3:
        data = data[..., :3].mean(axis=2)
    return np.flipud(data), hdr


def _parse_time(fits_path, hdr, utc_arg):
    from astropy.time import Time
    if utc_arg:
        return Time(utc_arg, scale='utc')
    if hdr:
        for key in ('DATE-OBS', 'DATE_OBS', 'DATETIME', 'DATE'):
            val = hdr.get(key)
            if val:
                try:
                    return Time(str(val).strip(), scale='utc')
                except Exception:
                    pass
    m = re.search(r'(\d{8})_(\d{6})', os.path.basename(fits_path))
    if m:
        ds, ts = m.group(1), m.group(2)
        iso = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}T{ts[:2]}:{ts[2:4]}:{ts[4:6]}"
        return Time(iso, format='isot', scale='utc')
    return Time.now()


def _detect_stars(img_data, max_n=60):
    from services.allsky.star_centroid import detect_stars, estimate_sky_circle
    from PIL import Image
    p1, p99 = np.percentile(img_data, 1), np.percentile(img_data, 99)
    stretched = np.clip((img_data - p1) / max(p99 - p1, 1) * 255, 0, 255).astype(np.uint8)
    pil = Image.fromarray(np.stack([stretched] * 3, axis=-1), 'RGB')
    cx, cy, r = estimate_sky_circle(pil)
    stars = detect_stars(pil, max_stars=max_n, sky_cx=cx, sky_cy=cy, sky_radius=r)
    return stars, cx, cy, r


def _catalog_altaz(lat, lon, dt, max_mag=4.5, min_alt=8.0):
    from services.allsky.catalogs import get_bright_stars
    from services.allsky.coords   import radec_to_altaz
    from datetime import timezone

    # Convert astropy Time → naive datetime (UTC) then to aware
    dt_py = dt.to_datetime()
    if dt_py.tzinfo is None:
        dt_py = dt_py.replace(tzinfo=timezone.utc)

    catalog = get_bright_stars(max_mag=max_mag)
    result  = []
    for s in catalog:
        alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], lat, lon, dt_py, refraction=True)
        alt, az = float(alt), float(az)
        if alt > min_alt:
            result.append((alt, az, s))
    result.sort(key=lambda x: x[2]['vmag'])
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Grid-search fisheye calibration (no seed needed)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument('fits_file')
    ap.add_argument('--lat',   type=float, default=52.0)
    ap.add_argument('--lon',   type=float, default=-1.0)
    ap.add_argument('--utc',   default=None, help='UTC datetime, e.g. "2026-01-16 02:15:10"')
    ap.add_argument('--cx',    type=float, default=None, help='Override optical centre X px')
    ap.add_argument('--cy',    type=float, default=None, help='Override optical centre Y px')
    ap.add_argument('--sky-r', type=float, default=None, help='Override sky circle radius px')
    ap.add_argument('--out',   default=None, help='Output calibration JSON path')
    ap.add_argument('--mag-limit', type=float, default=4.5)
    ap.add_argument('--max-stars', type=int,   default=60)
    args = ap.parse_args()

    fits_path = os.path.abspath(args.fits_file)
    out_path  = args.out or os.path.join(
        os.path.dirname(fits_path), 'multi_calibration.json')

    # ------------------------------------------------------------------
    print(f"Loading {os.path.basename(fits_path)} ...")
    data, hdr = _load_fits(fits_path)
    img_h, img_w = data.shape
    print(f"  Size: {img_w}x{img_h}")

    obs_time = _parse_time(fits_path, hdr, args.utc)
    print(f"  Time: {obs_time.isot} UTC")

    # ------------------------------------------------------------------
    print("\nDetecting stars ...")
    detected, sky_cx, sky_cy, sky_r = _detect_stars(data, max_n=args.max_stars)
    print(f"  Detected {len(detected)} candidates")
    print(f"  Sky circle: centre=({sky_cx:.0f}, {sky_cy:.0f})  r={sky_r:.0f} px")

    cx  = args.cx    if args.cx    is not None else sky_cx
    cy  = args.cy    if args.cy    is not None else sky_cy
    r0  = args.sky_r if args.sky_r is not None else sky_r
    a1  = r0 / (math.pi / 2.0)
    print(f"  Using optical centre: ({cx:.0f}, {cy:.0f})")
    print(f"  a1 estimate from sky circle: {a1:.1f} px/rad")
    print(f"  plate scale: {206265/a1:.1f} arcsec/px")

    # Detected star pixel array
    det_xy = np.array([[s[0], s[1]] for s in detected], dtype=np.float64)

    # ------------------------------------------------------------------
    print("\nLoading catalog ...")
    cat = _catalog_altaz(args.lat, args.lon, obs_time, max_mag=args.mag_limit)
    print(f"  {len(cat)} catalog stars above 8 deg (mag <= {args.mag_limit})")
    cat_use = cat[:120]   # use the 120 brightest
    alt_r = np.radians([c[0] for c in cat_use])
    az_r  = np.radians([c[1] for c in cat_use])

    # ------------------------------------------------------------------
    print("\nPhase 1 - coarse grid search (5 deg steps) ...")
    b_alt, b_az, b_roll, b_score = grid_search(
        alt_r, az_r, cx, cy, a1, det_xy,
        alt_lo=72.0, alt_hi=90.0, alt_step=3.0,
        az_step=5.0, roll_lo=-180.0, roll_hi=180.0, roll_step=5.0,
        radius_px=35.0, batch=500, label='coarse',
    )

    # ------------------------------------------------------------------
    print("\nPhase 2 - fine grid search (0.5 deg steps around coarse best) ...")
    b_alt2, b_az2, b_roll2, b_score2 = grid_search(
        alt_r, az_r, cx, cy, a1, det_xy,
        alt_lo=max(72.0, b_alt - 8.0), alt_hi=min(90.0, b_alt + 8.0), alt_step=0.5,
        az_lo=b_az - 20.0, az_hi=b_az + 20.0, az_step=1.0,
        roll_lo=b_roll - 20.0, roll_hi=b_roll + 20.0, roll_step=0.5,
        radius_px=20.0, batch=500, label='fine',
    )
    b_alt2, b_az2, b_roll2, b_score2 = grid_search(
        alt_r, az_r, cx, cy, a1, det_xy,
        alt_lo=max(72.0, b_alt2 - 4.0), alt_hi=min(90.0, b_alt2 + 4.0), alt_step=0.25,
        az_lo=b_az2 - 8.0, az_hi=b_az2 + 8.0, az_step=0.5,
        roll_lo=b_roll2 - 8.0, roll_hi=b_roll2 + 8.0, roll_step=0.25,
        radius_px=15.0, batch=500, label='fine-2',
    )

    print(f"\nGrid solution:")
    print(f"  axis_alt = {b_alt2:.2f} deg")
    print(f"  axis_az  = {b_az2:.2f} deg")
    print(f"  roll     = {b_roll2:.2f} deg")

    # ------------------------------------------------------------------
    print("\nPhase 3 - local optimizer refinement (all 8 params) ...")
    from scipy.optimize import least_squares
    from services.allsky.calibration import _params_to_model, _brightness_match
    from services.allsky.calibration import CalibrationError

    from datetime import timezone
    dt_py = obs_time.to_datetime().replace(tzinfo=timezone.utc)

    # Build initial matches with the grid solution
    from services.allsky.fisheye import FisheyeModel
    seed = FisheyeModel(
        cx=cx, cy=cy, a1=a1, a3=0.0, a5=0.0,
        roll=math.radians(b_roll2),
        axis_alt=b_alt2, axis_az=b_az2 % 360.0,
    )

    # Build catalog altaz list in the format _brightness_match expects
    from services.allsky.catalogs import get_bright_stars
    from services.allsky.coords   import radec_to_altaz
    catalog  = get_bright_stars(max_mag=args.mag_limit + 1.0)
    cat_full = []
    for s in catalog:
        alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], args.lat, args.lon, dt_py, refraction=True)
        alt, az = float(alt), float(az)
        if alt > 5.0:
            cat_full.append((s, alt, az))
    cat_full.sort(key=lambda x: x[0]['vmag'])
    above = [(s, a, z) for s, a, z in cat_full]

    matches = _brightness_match(detected, above, seed, tol_px=40.0)
    print(f"  Initial matches with grid solution: {len(matches)}")

    if len(matches) < 6:
        print("  WARNING: Too few matches for local refinement - saving grid solution.")
        seed.n_matches    = len(matches)
        seed.rms_residual = 999.0
        seed.save(out_path)
        print(f"\nCalibration saved: {out_path}")
        return

    params0 = np.array([
        cx, cy, a1, 0.0, 0.0,
        math.radians(b_roll2), b_alt2, b_az2 % 360.0,
    ])

    def residuals(p):
        m = _params_to_model(p)
        res = []
        for (dx, dy), _s, (alt, az) in matches:
            xy = m.altaz_to_pixel(alt, az)
            if xy is None:
                res.extend([30.0, 30.0])
            else:
                res.extend([dx - xy[0], dy - xy[1]])
        return res

    result = least_squares(
        residuals, params0,
        bounds=(
            [cx - 150, cy - 150,  300, -500, -500, -math.pi, 60.0, -180.0],
            [cx + 150, cy + 150, 2000,  500,  500,  math.pi, 90.0,  540.0],
        ),
        method='trf', max_nfev=20000, ftol=1e-6,
    )
    p_final = result.x.copy()
    p_final[7] = p_final[7] % 360.0
    model = _params_to_model(p_final)

    # Tighten: re-match at smaller tolerance and re-optimise once
    matches2 = _brightness_match(detected, above, model, tol_px=20.0)
    if len(matches2) >= 6:
        params1 = np.array([
            model.cx, model.cy, model.a1, model.a3, model.a5,
            model.roll, model.axis_alt, model.axis_az,
        ])
        def res2(p):
            m = _params_to_model(p)
            res = []
            for (dx, dy), _s, (alt, az) in matches2:
                xy = m.altaz_to_pixel(alt, az)
                if xy is None:
                    res.extend([20.0, 20.0])
                else:
                    res.extend([dx - xy[0], dy - xy[1]])
            return res
        r2 = least_squares(
            res2, params1,
            bounds=(
                [model.cx-100, model.cy-100, 300, -500, -500, -math.pi, 60.0, -180.0],
                [model.cx+100, model.cy+100, 2000, 500,  500,  math.pi, 90.0,  540.0],
            ),
            method='trf', max_nfev=20000, ftol=1e-7,
        )
        pf = r2.x.copy()
        pf[7] = pf[7] % 360.0
        model = _params_to_model(pf)
        matches = matches2

    # Compute RMS
    residuals_px = []
    for (dx, dy), _s, (alt, az) in matches:
        xy = model.altaz_to_pixel(alt, az)
        if xy is not None:
            residuals_px.append(math.hypot(dx - xy[0], dy - xy[1]))
    rms = float(np.median(residuals_px)) if residuals_px else 999.0

    model.n_matches    = len(matches)
    model.rms_residual = rms

    # ------------------------------------------------------------------
    print(f"\n=== GRID CALIBRATION RESULT ===")
    print(f"  Optical centre : ({model.cx:.1f}, {model.cy:.1f}) px")
    print(f"  a1             : {model.a1:.1f} px/rad")
    print(f"  a3, a5         : {model.a3:.2f}, {model.a5:.4f}")
    print(f"  Roll           : {math.degrees(model.roll):.2f} deg")
    print(f"  axis_alt       : {model.axis_alt:.3f} deg")
    print(f"  axis_az        : {model.axis_az:.3f} deg")
    print(f"  Plate scale    : {206265/model.a1:.1f} arcsec/px")
    print(f"  Matches        : {model.n_matches}")
    print(f"  Median RMS     : {model.rms_residual:.2f} px")

    model.save(out_path)
    print(f"\nCalibration saved: {out_path}")
    print(f"\nGenerate overlay with:")
    print(f"  python scripts/allsky_debug.py {args.fits_file} \\")
    print(f"    --lat {args.lat} --lon {args.lon} \\")
    print(f'    --utc "{obs_time.isot[:19]}" \\')
    print(f"    --cal {out_path} --overlay-only --max-mag 3.0 --stars 40")


if __name__ == '__main__':
    main()
