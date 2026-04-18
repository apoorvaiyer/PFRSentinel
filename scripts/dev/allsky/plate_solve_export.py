#!/usr/bin/env python3
"""
Export a crop from a FITS all-sky frame for external plate solving.

Automatically finds the region with the most bright catalog stars, crops it,
embeds an approximate WCS header, and prints ready-to-use parameters for
AstrometryNet and ASTAP.

Usage:
    python scripts/plate_solve_export.py sample_images/lum_20260116_021511.fits
    python scripts/plate_solve_export.py sample_images/lum_20260116_021511.fits \\
        --lat 52.0 --lon -1.0 --crop 400 --cal path/to/calibration.json
    python scripts/plate_solve_export.py ... --center-optical   # force zenith crop
"""
import os
import sys
import json
import math
import argparse
import re
import numpy as np

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ascii_name(name: str) -> str:
    """Replace non-ASCII chars (Greek letters etc.) with ASCII equivalents."""
    table = str.maketrans({
        '\u03b1': 'alf', '\u03b2': 'bet', '\u03b3': 'gam', '\u03b4': 'del',
        '\u03b5': 'eps', '\u03b6': 'zet', '\u03b7': 'eta', '\u03b8': 'tet',
        '\u03b9': 'iot', '\u03ba': 'kap', '\u03bb': 'lam', '\u03bc': 'mu',
        '\u03bd': 'nu',  '\u03be': 'xi',  '\u03bf': 'omi', '\u03c0': 'pi',
        '\u03c1': 'rho', '\u03c3': 'sig', '\u03c4': 'tau', '\u03c5': 'ups',
        '\u03c6': 'phi', '\u03c7': 'chi', '\u03c8': 'psi', '\u03c9': 'ome',
        '\u00b9': '1',   '\u00b2': '2',   '\u00b3': '3',
    })
    result = name.translate(table)
    return result.encode('ascii', 'replace').decode('ascii')


def _deg_to_hms(deg: float) -> str:
    deg = deg % 360
    h = int(deg / 15)
    rem = (deg / 15 - h) * 60
    m = int(rem)
    s = (rem - m) * 60
    return f"{h:02d}h {m:02d}m {s:04.1f}s"


def _deg_to_dms(deg: float) -> str:
    sign = '+' if deg >= 0 else '-'
    d_abs = abs(deg)
    d = int(d_abs)
    rem = (d_abs - d) * 60
    m = int(rem)
    s = (rem - m) * 60
    return f"{sign}{d:02d}d {m:02d}m {s:04.1f}s"


def _parse_obs_time(fits_path: str, hdr):
    """Return astropy Time from FITS header or filename."""
    from astropy.time import Time

    if hdr is not None:
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
        from astropy.time import Time
        return Time(iso, format='isot', scale='utc')

    from astropy.time import Time
    return Time.now()


def _zenith_radec(obs_time, lat_deg: float, lon_deg: float):
    """Return (ra_deg, dec_deg) of the zenith."""
    from astropy.coordinates import EarthLocation, SkyCoord, AltAz
    import astropy.units as u
    loc = EarthLocation(lat=lat_deg * u.deg, lon=lon_deg * u.deg)
    frame = AltAz(obstime=obs_time, location=loc)
    z = SkyCoord(alt=90 * u.deg, az=0 * u.deg, frame=frame)
    eq = z.icrs
    return float(eq.ra.deg), float(eq.dec.deg)


def _load_fits_2d(fits_path: str):
    """Return (data_2d_float32_pil_ydown, header). y=0 is TOP (PIL convention)."""
    from astropy.io import fits

    with fits.open(fits_path) as hdul:
        data = hdul[0].data.astype(np.float32)
        hdr = hdul[0].header

    if data.ndim == 3 and data.shape[0] in (1, 3, 4) and data.shape[0] < data.shape[1]:
        data = np.transpose(data, (1, 2, 0))
    if data.ndim == 3:
        data = data[..., :3].mean(axis=2)

    # FITS row-0 = bottom; flip to PIL y-down (row-0 = top)
    data = np.flipud(data)
    return data, hdr


def _stretch_uint8(data: np.ndarray) -> np.ndarray:
    """Percentile stretch float32 → uint8."""
    p1, p99 = np.percentile(data, 1), np.percentile(data, 99)
    if p99 > p1:
        out = (data - p1) / (p99 - p1) * 255.0
    else:
        out = data.copy()
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Fisheye inverse: pixel → (alt_deg, az_deg)
# ---------------------------------------------------------------------------

def _pixel_to_altaz(px: float, py: float, model) -> tuple:
    """
    Invert the fisheye model to find the (alt, az) that projects to (px, py).
    Returns (alt_deg, az_deg).
    """
    dx = px - model.cx
    dy = model.cy - py          # positive dy = upward in image = North direction (roll=0)

    r_px = math.sqrt(dx * dx + dy * dy)
    if r_px < 0.5:
        return float(model.axis_alt), float(model.axis_az)

    phi = math.atan2(dx, dy)    # angle in camera plane; phi=0 → North when roll=0

    # Numerically invert polynomial r = a1*θ + a3*θ³ + a5*θ⁵
    a1, a3, a5 = model.a1, model.a3, model.a5
    theta = r_px / a1           # linear seed
    for _ in range(30):
        t2 = theta * theta
        r_est = a1 * theta + a3 * t2 * theta + a5 * t2 * t2 * theta
        dr    = a1 + 3 * a3 * t2 + 5 * a5 * t2 * t2
        if abs(dr) < 1e-12:
            break
        step = (r_est - r_px) / dr
        theta -= step
        theta = max(1e-6, min(math.pi * 0.99, theta))
        if abs(step) < 1e-8:
            break

    # Unit vector in camera frame (after roll)
    vx4 = math.sin(theta) * math.sin(phi)
    vy4 = math.sin(theta) * math.cos(phi)
    vz4 = math.cos(theta)

    # Inverse roll  (forward was R_z(-roll_r); inverse is R_z(+roll_r))
    roll_r = model.roll
    cr, sr = math.cos(roll_r), math.sin(roll_r)
    vx3 =  cr * vx4 - sr * vy4
    vy3 =  sr * vx4 + cr * vy4
    vz3 = vz4

    # Inverse tilt  (forward was R_x(-tilt); inverse is R_x(+tilt))
    tilt = math.radians(90.0 - model.axis_alt)
    ct, st = math.cos(tilt), math.sin(tilt)
    vx2 = vx3
    vy2 =  ct * vy3 - st * vz3
    vz2 =  st * vy3 + ct * vz3

    # Inverse azimuth  (forward was R_z(-az); inverse is R_z(+az))
    az_r = math.radians(model.axis_az)
    ca, sa = math.cos(az_r), math.sin(az_r)
    vx =  ca * vx2 - sa * vy2
    vy =  sa * vx2 + ca * vy2
    vz = vz2

    alt_deg = math.degrees(math.asin(max(-1.0, min(1.0, vz))))
    az_deg  = math.degrees(math.atan2(vx, vy)) % 360.0
    return alt_deg, az_deg


def _pixel_to_radec(px: float, py: float, model, lat_deg: float, lon_deg: float, dt) -> tuple:
    """Return (ra_deg, dec_deg) of an image pixel."""
    from services.allsky.coords import altaz_to_radec
    alt, az = _pixel_to_altaz(px, py, model)
    ra, dec = altaz_to_radec(alt, az, lat_deg, lon_deg, dt, refraction=True)
    return float(ra), float(dec)


# ---------------------------------------------------------------------------
# Find the image region with the most bright catalog stars
# ---------------------------------------------------------------------------

def _find_best_crop_center(model, catalog, dt, lat_deg, lon_deg, img_w, img_h,
                            crop_size: int, min_alt: float = 25.0):
    """
    Slide a crop_size window over the image and return the centre (x, y)
    that contains the most bright catalog stars, plus the star list.

    min_alt restricts candidate crop centres to sky positions above that altitude
    (avoids extreme fisheye distortion near the horizon).
    """
    from services.allsky.coords import radec_to_altaz

    half = crop_size // 2

    # Max pixel radius to search — anything beyond ~60 deg from optical axis
    # will have significant fisheye distortion; use axis_alt=90 so distance ≈ 90-alt
    max_r_px = model.a1 * math.radians(90.0 - min_alt) * 1.4   # generous

    visible = []
    for star in catalog:
        alt, az = radec_to_altaz(
            star['ra_deg'], star['dec_deg'], lat_deg, lon_deg, dt, refraction=True
        )
        alt, az = float(alt), float(az)
        if alt < 5.0:
            continue
        xy = model.altaz_to_pixel(alt, az)
        if xy is None:
            continue
        px, py = float(xy[0]), float(xy[1])
        if not (half <= px < img_w - half and half <= py < img_h - half):
            continue
        visible.append({
            'star': star,
            'px': px, 'py': py,
            'alt': alt, 'az': az,
        })

    if not visible:
        return int(model.cx), int(model.cy), []

    # Only use stars above min_alt as candidate crop centres (less distortion)
    candidates = [s for s in visible if s['alt'] >= min_alt]
    if not candidates:
        candidates = visible   # fall back to all visible

    best_cx, best_cy, best_stars = int(model.cx), int(model.cy), []
    best_score = 0
    for candidate in candidates:
        cx, cy = candidate['px'], candidate['py']
        in_window = [
            s for s in visible
            if abs(s['px'] - cx) < half and abs(s['py'] - cy) < half
        ]
        # Weight by brightness; penalise crop centres far from optical axis
        r_from_center = math.hypot(cx - model.cx, cy - model.cy)
        dist_penalty  = max(0.5, 1.0 - r_from_center / (max_r_px * 1.5))
        score = dist_penalty * sum(max(0.1, 5.0 - s['star']['vmag']) for s in in_window)
        if score > best_score:
            best_score = score
            best_cx, best_cy = int(round(cx)), int(round(cy))
            best_stars = in_window

    return best_cx, best_cy, best_stars


# ---------------------------------------------------------------------------
# Diagnostic: annotate crop with predicted catalog star positions
# ---------------------------------------------------------------------------

def _generate_diagnostic(crop_data, crop_x0, crop_y0, best_stars, model,
                          cal, out_dir, stem, crop_size):
    """Save annotated JPEG showing predicted catalog star positions."""
    from PIL import Image, ImageDraw, ImageFont

    gray_u8 = _stretch_uint8(crop_data)
    # Boost faint stars a bit
    boosted = np.clip(gray_u8.astype(np.float32) * 1.8, 0, 255).astype(np.uint8)
    rgb = np.stack([boosted] * 3, axis=-1)
    img = Image.fromarray(rgb, 'RGB')
    draw = ImageDraw.Draw(img)

    try:
        font    = ImageFont.truetype("arial.ttf", 11)
        font_sm = ImageFont.truetype("arial.ttf", 9)
    except Exception:
        font = font_sm = ImageFont.load_default()

    ch, cw = crop_data.shape

    # Draw catalog star predictions (yellow)
    for s in best_stars:
        px = int(round(s['px'] - crop_x0))
        py = int(round(s['py'] - crop_y0))
        vmag = s['star']['vmag']
        r = max(5, int(12 - vmag * 1.2))
        r = min(r, 16)
        # Visible in crop?
        if -r <= px < cw + r and -r <= py < ch + r:
            draw.ellipse([px - r, py - r, px + r, py + r],
                         outline=(255, 220, 0), width=2)
            raw = (s['star'].get('name') or '').strip()
            name = _ascii_name(raw)
            if name and 0 <= px < cw and 0 <= py < ch:
                draw.text((px + r + 2, py - 6), f"{name} {vmag:.1f}",
                          fill=(255, 220, 0), font=font)

    # Draw optical centre as green crosshair
    oc_x = int(round(cal['cx'])) - crop_x0
    oc_y = int(round(cal['cy'])) - crop_y0
    if 0 <= oc_x < cw and 0 <= oc_y < ch:
        draw.ellipse([oc_x - 10, oc_y - 10, oc_x + 10, oc_y + 10],
                     outline=(0, 255, 0), width=2)
        draw.line([oc_x - 10, oc_y, oc_x + 10, oc_y], fill=(0, 255, 0), width=1)
        draw.line([oc_x, oc_y - 10, oc_x, oc_y + 10], fill=(0, 255, 0), width=1)

    draw.text((5,  5), "Yellow = catalog stars predicted by calibration",
              fill=(255, 220, 0), font=font_sm)
    draw.text((5, 18), "Green  = optical centre (zenith pointing)",
              fill=(0, 255, 0), font=font_sm)

    out_path = os.path.join(out_dir, f"{stem}_crop_{crop_size}px_diagnostic.jpg")
    img.save(out_path, quality=92)
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description='Export FITS crop for plate solving')
    ap.add_argument('fits_file',       help='Path to FITS all-sky image')
    ap.add_argument('--cal',           default=None,  help='Calibration JSON (auto-detected)')
    ap.add_argument('--lat',           type=float, default=52.0, help='Observer latitude N (default 52.0)')
    ap.add_argument('--lon',           type=float, default=-1.0, help='Observer longitude E (default -1.0)')
    ap.add_argument('--crop',          type=int,   default=400,  help='Crop size px (default 400)')
    ap.add_argument('--out',           default=None, help='Output directory')
    ap.add_argument('--center-optical', action='store_true',
                    help='Force crop at optical centre (zenith) instead of auto star-dense region')
    ap.add_argument('--mag-limit',     type=float, default=5.5,
                    help='Catalog magnitude limit for star search (default 5.5)')
    args = ap.parse_args()

    fits_path = os.path.abspath(args.fits_file)
    if not os.path.exists(fits_path):
        print(f"ERROR: File not found: {fits_path}")
        sys.exit(1)

    out_dir = args.out or os.path.dirname(fits_path)
    stem    = os.path.splitext(os.path.basename(fits_path))[0]

    # -----------------------------------------------------------------------
    # 1. Load calibration
    # -----------------------------------------------------------------------
    cal_path = args.cal
    if cal_path is None:
        for candidate in [
            os.path.join(os.path.dirname(fits_path), f"{stem}_debug_calibration.json"),
            os.path.join(os.path.dirname(fits_path), f"{stem}_verify_calibration.json"),
        ]:
            if os.path.exists(candidate):
                cal_path = candidate
                break

    if cal_path is None or not os.path.exists(cal_path):
        print("ERROR: Calibration JSON not found.")
        print("  Run:  python scripts/allsky_debug.py <fits_file>  first.")
        sys.exit(1)

    with open(cal_path) as fh:
        cal = json.load(fh)

    from services.allsky.fisheye import FisheyeModel
    model = FisheyeModel.load(cal_path)

    cx   = float(cal['cx'])
    cy   = float(cal['cy'])
    a1   = float(cal['a1'])
    roll = float(cal.get('roll', 0.0))
    rms  = float(cal.get('rms_residual', 0.0))
    n_m  = int(cal.get('n_matches', 0))

    print(f"Calibration : {os.path.basename(cal_path)}")
    print(f"  Optical centre : ({cx:.1f}, {cy:.1f}) px")
    print(f"  a1             : {a1:.2f}")
    print(f"  Roll           : {math.degrees(roll):.2f} deg")
    print(f"  RMS residual   : {rms:.2f} px  ({n_m} matches)")

    # -----------------------------------------------------------------------
    # 2. Load FITS
    # -----------------------------------------------------------------------
    print(f"\nLoading {os.path.basename(fits_path)} ...")
    data, hdr = _load_fits_2d(fits_path)
    img_h, img_w = data.shape
    print(f"  Size : {img_w} x {img_h} px")

    obs_time = _parse_obs_time(fits_path, hdr)
    print(f"  Time : {obs_time.isot} UTC")

    # -----------------------------------------------------------------------
    # 3. Plate scale
    # -----------------------------------------------------------------------
    plate_scale_arcsec = 206265.0 / a1
    plate_scale_deg    = plate_scale_arcsec / 3600.0
    print(f"  Plate scale : {plate_scale_arcsec:.2f} arcsec/px at optical centre")

    # -----------------------------------------------------------------------
    # 4. Find best crop centre (star-dense region)
    # -----------------------------------------------------------------------
    from datetime import datetime, timezone
    dt = obs_time.to_datetime().replace(tzinfo=timezone.utc)

    from services.allsky.catalogs import get_bright_stars
    catalog = get_bright_stars(max_mag=args.mag_limit)

    if args.center_optical:
        crop_cx, crop_cy = int(round(cx)), int(round(cy))
        print(f"\nCrop centre : optical axis ({crop_cx}, {crop_cy})  [forced]")
        # Still build the star list for that crop
        from services.allsky.coords import radec_to_altaz
        half = args.crop // 2
        best_stars = []
        for star in catalog:
            alt, az = radec_to_altaz(
                star['ra_deg'], star['dec_deg'], args.lat, args.lon, dt, refraction=True
            )
            alt, az = float(alt), float(az)
            if alt < 5.0:
                continue
            xy = model.altaz_to_pixel(alt, az)
            if xy is None:
                continue
            px, py = float(xy[0]), float(xy[1])
            if abs(px - crop_cx) < half and abs(py - crop_cy) < half:
                best_stars.append({'star': star, 'px': px, 'py': py, 'alt': alt})
    else:
        crop_cx, crop_cy, best_stars = _find_best_crop_center(
            model, catalog, dt, args.lat, args.lon, img_w, img_h, args.crop
        )
        if not args.center_optical and (crop_cx, crop_cy) == (int(round(cx)), int(round(cy))):
            print(f"\nCrop centre : optical axis ({crop_cx}, {crop_cy})  [auto]")
        else:
            print(f"\nCrop centre : ({crop_cx}, {crop_cy})  [auto: star-dense region]")

    print(f"  Stars (mag<{args.mag_limit:.1f}) in crop : {len(best_stars)}")
    if len(best_stars) < 8:
        print("  WARNING: Fewer than 8 bright stars in this crop.")
        print("  Plate solve may struggle. Try --mag-limit 6.5 for more stars.")

    # -----------------------------------------------------------------------
    # 5. RA/Dec of crop centre
    # -----------------------------------------------------------------------
    ra_cen_deg, dec_cen_deg = _pixel_to_radec(
        crop_cx, crop_cy, model, args.lat, args.lon, dt
    )
    print(f"  Crop centre RA/Dec : {_deg_to_hms(ra_cen_deg)} / {_deg_to_dms(dec_cen_deg)}")
    print(f"                       {ra_cen_deg:.4f} deg / {dec_cen_deg:.4f} deg")

    # -----------------------------------------------------------------------
    # 6. Extract crop (PIL y-down space)
    # -----------------------------------------------------------------------
    half = args.crop // 2
    x0   = max(0, crop_cx - half)
    y0   = max(0, crop_cy - half)
    x1   = min(img_w, crop_cx + half)
    y1   = min(img_h, crop_cy + half)

    crop  = data[y0:y1, x0:x1]
    ch, cw = crop.shape
    fov_deg = args.crop * plate_scale_deg
    print(f"  Crop size  : {cw} x {ch} px  (FOV ~{fov_deg:.2f} deg)")

    # -----------------------------------------------------------------------
    # 7. Print predicted star positions in crop
    # -----------------------------------------------------------------------
    best_stars.sort(key=lambda s: s['star']['vmag'])
    print(f"\n  {'Name':<16} {'Vmag':>5}  {'Alt':>6}  {'Crop (x,y)':>12}")
    for s in best_stars[:20]:
        raw_name = (s['star'].get('name') or '').strip()
        if not raw_name:
            raw_name = f"{s['star'].get('bayer','')} {s['star'].get('const','')}".strip()
        name = _ascii_name(raw_name)[:16]
        cpx  = int(round(s['px'] - x0))
        cpy  = int(round(s['py'] - y0))
        print(f"  {name:<16} {s['star']['vmag']:>5.2f}  {s['alt']:>6.1f}  ({cpx:>4},{cpy:>4})")

    # -----------------------------------------------------------------------
    # 8. Save FITS with WCS
    # -----------------------------------------------------------------------
    from astropy.io import fits as astrofits
    from astropy.wcs import WCS

    # CRPIX: where the crop centre lands in the FITS (1-indexed, y-up)
    crpix1 = float(crop_cx - x0) + 1.0
    crpix2 = float(ch - 1 - (crop_cy - y0)) + 1.0   # flip y for FITS

    # CD matrix for TAN projection at crop centre.
    # The fisheye maps North to -y and East to +x when roll=0.
    # With roll applied: the CD matrix is rotated by roll.
    # East-on-right (negative parity): CD1_1 should be POSITIVE.
    cos_r = math.cos(roll)
    sin_r = math.sin(roll)
    ps    = plate_scale_deg

    # Standard TAN CD for East-right (negative parity):
    #   North = +y_FITS, East = +x → CD1_1=+ps, CD2_2=+ps
    # With roll:
    cd11 =  ps * cos_r
    cd12 =  ps * sin_r
    cd21 = -ps * sin_r
    cd22 =  ps * cos_r

    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [crpix1, crpix2]
    wcs.wcs.crval = [ra_cen_deg, dec_cen_deg]
    wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    wcs.wcs.cd    = [[cd11, cd12], [cd21, cd22]]

    new_hdr = wcs.to_header()
    new_hdr['DATE-OBS'] = obs_time.isot
    new_hdr['PIXSCALE'] = (round(plate_scale_arcsec, 3), 'arcsec/px at optical centre')
    new_hdr['CALFILE']  = os.path.basename(cal_path)
    new_hdr['SRCFILE']  = os.path.basename(fits_path)
    new_hdr['COMMENT']  = f'Crop from fisheye all-sky {os.path.basename(fits_path)}'
    new_hdr['COMMENT']  = f'Optical centre (full frame): ({cx:.1f}, {cy:.1f}) px'
    new_hdr['COMMENT']  = f'Crop centre: ({crop_cx}, {crop_cy}) px'
    new_hdr['COMMENT']  = f'WCS: approx TAN, valid near crop centre; East is RIGHT (negative parity)'

    # Save FITS (flip back to FITS y-up convention)
    crop_fits = np.flipud(crop).astype(np.float32)
    out_fits  = os.path.join(out_dir, f"{stem}_crop_{args.crop}px.fits")
    astrofits.PrimaryHDU(data=crop_fits, header=new_hdr).writeto(out_fits, overwrite=True)
    print(f"\nSaved FITS  : {out_fits}")

    # -----------------------------------------------------------------------
    # 9. Save JPEG preview + diagnostic
    # -----------------------------------------------------------------------
    from PIL import Image, ImageDraw

    gray_u8 = _stretch_uint8(crop)
    rgb     = np.stack([gray_u8] * 3, axis=-1)
    preview = Image.fromarray(rgb, 'RGB')
    draw    = ImageDraw.Draw(preview)
    # Mark crop centre
    draw.ellipse([cw//2 - 8, ch//2 - 8, cw//2 + 8, ch//2 + 8],
                 outline=(0, 255, 0), width=2)
    draw.line([cw//2 - 8, ch//2, cw//2 + 8, ch//2], fill=(0, 255, 0), width=1)
    draw.line([cw//2, ch//2 - 8, cw//2, ch//2 + 8], fill=(0, 255, 0), width=1)

    out_jpg = os.path.join(out_dir, f"{stem}_crop_{args.crop}px_preview.jpg")
    preview.save(out_jpg, quality=95)
    print(f"Saved JPEG  : {out_jpg}")

    diag_path = _generate_diagnostic(
        crop, x0, y0, best_stars, model, cal, out_dir, stem, args.crop
    )
    print(f"Saved diag  : {diag_path}")
    print("  (Yellow circles = where catalog stars SHOULD appear per calibration)")
    print("  (If they align with bright dots, the calibration is accurate)")

    # -----------------------------------------------------------------------
    # 10. Plate-solve instructions
    # -----------------------------------------------------------------------
    scale_lo = round(plate_scale_arcsec * 0.85, 1)
    scale_hi = round(plate_scale_arcsec * 1.15, 1)

    sep = "=" * 68
    print()
    print(sep)
    print(f"  PLATE SOLVE PARAMETERS  --  {os.path.basename(fits_path)}")
    print(sep)
    print(f"  Observation time   : {obs_time.isot} UTC")
    print(f"  Crop centre RA     : {_deg_to_hms(ra_cen_deg)}  ({ra_cen_deg:.4f} deg)")
    print(f"  Crop centre Dec    : {_deg_to_dms(dec_cen_deg)}  ({dec_cen_deg:.4f} deg)")
    print(f"  Plate scale        : {plate_scale_arcsec:.2f} arcsec/px (range {scale_lo}-{scale_hi})")
    print(f"  FOV (crop)         : {fov_deg:.2f} deg")
    print(f"  Stars in crop      : {len(best_stars)} (mag < {args.mag_limit:.1f})")
    print(f"  PARITY             : NEGATIVE (East is on the RIGHT in image)")
    print()
    print("  ASTROMETRY.NET  (nova.astrometry.net -> Upload)")
    print(f"    File        : {os.path.basename(out_jpg)}  or  {os.path.basename(out_fits)}")
    print(f"    RA guess    : {ra_cen_deg:.3f} deg  (search radius 5 deg)")
    print(f"    Dec guess   : {dec_cen_deg:.3f} deg")
    print(f"    Scale lower : {scale_lo} arcsec/px")
    print(f"    Scale upper : {scale_hi} arcsec/px")
    print( "    Parity      : NEGATIVE  <-- this is critical, mirror-flip image")
    print()
    print("  ASTAP  (local solver)")
    print(f'    astap.exe -f "{os.path.basename(out_fits)}"')
    print(f"         -ra {ra_cen_deg/15:.5f}  -spd {dec_cen_deg + 90:.4f}")
    print(f"         -fov {fov_deg:.2f}  -s 40  -m")
    print()
    print(f"    ra  = {ra_cen_deg/15:.5f} hours")
    print(f"    spd = {dec_cen_deg + 90:.4f} deg  (Dec + 90 = South Polar Distance)")
    print(f"    fov = {fov_deg:.2f} deg")
    print( "    -m  = mirror/flip (negative parity)")
    print(sep)
    print()


if __name__ == '__main__':
    main()
