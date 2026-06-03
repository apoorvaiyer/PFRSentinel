#!/usr/bin/env python3
"""
Rectify an all-sky fisheye image to gnomonic (TAN) projection.

Reprojects the fisheye data to a standard TAN image centred on any
sky direction. The output follows a true TAN projection so AstrometryNet
and ASTAP can plate-solve it without distortion issues.

Usage:
    # Centre on zenith (default)
    python scripts/allsky_rectify.py sample_images/lum_20260116_021511.fits \\
        --cal sample_images/multi_calibration.json --lat 52 --lon -1

    # Centre on a specific RA/Dec
    python scripts/allsky_rectify.py sample_images/lum_20260116_021511.fits \\
        --cal sample_images/multi_calibration.json --lat 52 --lon -1 \\
        --ra 85.3 --dec 16.5

    # Custom output plate scale / size
    python scripts/allsky_rectify.py ... --scale 100 --size 800
"""
import os
import sys
import re
import math
import json
import argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ascii_name(name: str) -> str:
    table = str.maketrans({
        '\u03b1':'alf','\u03b2':'bet','\u03b3':'gam','\u03b4':'del',
        '\u03b5':'eps','\u03b6':'zet','\u03b7':'eta','\u03b8':'tet',
        '\u03b9':'iot','\u03ba':'kap','\u03bb':'lam','\u03bc':'mu',
        '\u03bd':'nu', '\u03be':'xi', '\u03bf':'omi','\u03c0':'pi',
        '\u03c1':'rho','\u03c3':'sig','\u03c4':'tau','\u03c5':'ups',
        '\u03c6':'phi','\u03c7':'chi','\u03c8':'psi','\u03c9':'ome',
        '\u00b9':'1',  '\u00b2':'2',  '\u00b3':'3',
    })
    return name.translate(table).encode('ascii', 'replace').decode('ascii')


def _load_fits_2d(path):
    from astropy.io import fits
    with fits.open(path) as hdul:
        data = hdul[0].data.astype(np.float32)
        hdr  = hdul[0].header
    if data.ndim == 3 and data.shape[0] in (1, 3, 4) and data.shape[0] < data.shape[1]:
        data = np.transpose(data, (1, 2, 0))
    if data.ndim == 3:
        data = data[..., :3].mean(axis=2)
    return np.flipud(data), hdr   # FITS y-up → PIL y-down


def _parse_obs_time(fits_path, hdr):
    from astropy.time import Time
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


def _stretch_uint8(data):
    p1, p99 = np.percentile(data[data > 0], [1, 99]) if np.any(data > 0) else (0, 1)
    if p99 > p1:
        out = (data - p1) / (p99 - p1) * 255.0
    else:
        out = data.copy()
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Core: build output→source pixel mapping via vectorised fisheye projection
# ---------------------------------------------------------------------------

def _build_pixel_map(center_ra, center_dec, obs_time, lat_deg, lon_deg,
                     model, out_size, out_scale_deg):
    """
    For each pixel in an (out_size x out_size) TAN output image,
    return the corresponding (src_x, src_y) in the full fisheye image.

    Returns:
        src_x, src_y : float arrays shape (out_size, out_size)
        valid        : bool array — True where source pixel is inside image
    """
    from astropy.coordinates import SkyCoord, EarthLocation, AltAz
    import astropy.units as u

    half = out_size / 2.0
    j, i = np.meshgrid(np.arange(out_size, dtype=np.float64),
                       np.arange(out_size, dtype=np.float64))

    # TAN offsets in degrees from field centre (xi=East, eta=North)
    xi  = (j - half + 0.5) * out_scale_deg
    eta = -(i - half + 0.5) * out_scale_deg   # row↓ → flip to North↑

    # Full gnomonic (TAN) deprojection
    xi_r   = np.radians(xi)
    eta_r  = np.radians(eta)
    dec0_r = math.radians(center_dec)
    ra0_r  = math.radians(center_ra)

    denom  = math.cos(dec0_r) - eta_r * math.sin(dec0_r)
    ra_r   = np.arctan2(xi_r, denom) + ra0_r
    dec_r  = np.arctan2(
        (eta_r * math.cos(dec0_r) + math.sin(dec0_r)) * np.cos(ra_r - ra0_r),
        denom,
    )

    ra_flat  = (np.degrees(ra_r) % 360.0).ravel()
    dec_flat = np.degrees(dec_r).ravel()

    # RA/Dec → AltAz (vectorised via astropy)
    loc   = EarthLocation(lat=lat_deg * u.deg, lon=lon_deg * u.deg, height=0 * u.m)
    frame = AltAz(obstime=obs_time, location=loc)
    sky   = SkyCoord(ra=ra_flat * u.deg, dec=dec_flat * u.deg, frame='icrs')
    aa    = sky.transform_to(frame)

    alt_arr = aa.alt.deg
    az_arr  = aa.az.deg

    # AltAz → fisheye source pixels (vectorised)
    src_x_flat, src_y_flat, vis = model.altaz_array_to_pixels(alt_arr, az_arr)

    return (src_x_flat.reshape(out_size, out_size),
            src_y_flat.reshape(out_size, out_size),
            vis.reshape(out_size, out_size))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Rectify fisheye all-sky image to gnomonic TAN projection',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument('fits_file')
    ap.add_argument('--cal',   required=True, help='Calibration JSON')
    ap.add_argument('--lat',   type=float, default=52.0)
    ap.add_argument('--lon',   type=float, default=-1.0)
    ap.add_argument('--ra',    type=float, default=None,
                    help='Field centre RA deg (default: zenith)')
    ap.add_argument('--dec',   type=float, default=None,
                    help='Field centre Dec deg (default: zenith)')
    ap.add_argument('--scale', type=float, default=150.0,
                    help='Output plate scale arcsec/px (default 150)')
    ap.add_argument('--size',  type=int,   default=600,
                    help='Output image size px (default 600)')
    ap.add_argument('--out',   default=None, help='Output directory')
    ap.add_argument('--mag-limit', type=float, default=5.5)
    args = ap.parse_args()

    fits_path = os.path.abspath(args.fits_file)
    out_dir   = args.out or os.path.dirname(fits_path)
    stem      = os.path.splitext(os.path.basename(fits_path))[0]

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    print(f"Loading {os.path.basename(fits_path)} ...")
    data, hdr = _load_fits_2d(fits_path)
    img_h, img_w = data.shape
    print(f"  Size: {img_w} x {img_h}")

    from astropy.time import Time
    obs_time = _parse_obs_time(fits_path, hdr)
    print(f"  Time: {obs_time.isot} UTC")

    from services.allsky.fisheye import FisheyeModel
    model = FisheyeModel.load(args.cal)
    print(f"  Cal : {os.path.basename(args.cal)}  "
          f"a1={model.a1:.1f} axis_alt={model.axis_alt:.2f}")

    # ------------------------------------------------------------------
    # Field centre
    # ------------------------------------------------------------------
    from datetime import timezone
    dt = obs_time.to_datetime().replace(tzinfo=timezone.utc)

    if args.ra is not None and args.dec is not None:
        center_ra, center_dec = args.ra, args.dec
        print(f"  Centre: RA={center_ra:.4f} Dec={center_dec:.4f} (user-supplied)")
    else:
        # Zenith: convert axis_alt / axis_az → RA/Dec
        from services.allsky.coords import altaz_to_radec
        center_ra, center_dec = altaz_to_radec(
            model.axis_alt, model.axis_az, args.lat, args.lon, dt, refraction=False
        )
        center_ra  = float(center_ra)
        center_dec = float(center_dec)
        print(f"  Centre: zenith RA={center_ra:.4f} Dec={center_dec:.4f}")

    out_scale_deg = args.scale / 3600.0
    out_fov_deg   = args.size * out_scale_deg
    print(f"  Output: {args.size}x{args.size} px  "
          f"scale={args.scale:.1f} arcsec/px  FOV={out_fov_deg:.2f} deg")

    # ------------------------------------------------------------------
    # Build pixel mapping
    # ------------------------------------------------------------------
    print("Building pixel map (vectorised) ...")
    src_x, src_y, valid = _build_pixel_map(
        center_ra, center_dec, obs_time, args.lat, args.lon,
        model, args.size, out_scale_deg,
    )
    valid &= (src_x >= 0) & (src_x < img_w) & (src_y >= 0) & (src_y < img_h)
    print(f"  {valid.sum()} / {args.size ** 2} output pixels mapped to source")

    # ------------------------------------------------------------------
    # Resample source image
    # ------------------------------------------------------------------
    from scipy.ndimage import map_coordinates

    out_data = np.zeros((args.size, args.size), dtype=np.float32)
    coords   = np.array([src_y[valid].ravel(), src_x[valid].ravel()])
    sampled  = map_coordinates(data, coords, order=1, mode='constant', cval=0.0)
    out_data[valid] = sampled

    # ------------------------------------------------------------------
    # Save rectified FITS with TAN WCS
    # ------------------------------------------------------------------
    from astropy.io import fits as astrofits
    from astropy.wcs import WCS

    wcs = WCS(naxis=2)
    half = args.size / 2.0
    wcs.wcs.crpix = [half + 0.5, half + 0.5]
    wcs.wcs.crval = [center_ra, center_dec]
    wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    wcs.wcs.cdelt = [-out_scale_deg, out_scale_deg]   # RA decreases left→right

    new_hdr = wcs.to_header()
    new_hdr['DATE-OBS'] = obs_time.isot
    new_hdr['PIXSCALE'] = (round(args.scale, 3), 'arcsec/px')
    new_hdr['CALFILE']  = os.path.basename(args.cal)
    new_hdr['SRCFILE']  = os.path.basename(fits_path)
    new_hdr['COMMENT']  = 'Rectified gnomonic (TAN) reprojection from fisheye all-sky image'

    out_fits = os.path.join(out_dir, f"{stem}_rect_{args.size}px.fits")
    astrofits.PrimaryHDU(data=np.flipud(out_data), header=new_hdr).writeto(
        out_fits, overwrite=True
    )
    print(f"\nSaved FITS : {out_fits}")

    # ------------------------------------------------------------------
    # Save JPEG preview
    # ------------------------------------------------------------------
    from PIL import Image, ImageDraw, ImageFont

    gray = _stretch_uint8(out_data)
    rgb  = np.stack([gray] * 3, axis=-1)
    img  = Image.fromarray(rgb, 'RGB')

    out_jpg = os.path.join(out_dir, f"{stem}_rect_{args.size}px_preview.jpg")
    img.save(out_jpg, quality=95)
    print(f"Saved JPEG : {out_jpg}")

    # ------------------------------------------------------------------
    # Diagnostic: overlay catalog star predictions
    # ------------------------------------------------------------------
    from services.allsky.catalogs import get_bright_stars
    from services.allsky.coords   import radec_to_altaz

    catalog = get_bright_stars(max_mag=args.mag_limit)
    draw    = ImageDraw.Draw(img)

    try:
        font    = ImageFont.truetype("arial.ttf", 11)
        font_sm = ImageFont.truetype("arial.ttf", 9)
    except Exception:
        font = font_sm = ImageFont.load_default()

    n_plotted = 0
    for star in catalog:
        alt, az = radec_to_altaz(
            star['ra_deg'], star['dec_deg'], args.lat, args.lon, dt, refraction=True
        )
        if float(alt) < 5.0:
            continue

        # Project to rectified image via TAN formula
        ra_s, dec_s = float(star['ra_deg']), float(star['dec_deg'])
        xi  = math.radians(ra_s  - center_ra)
        eta = math.radians(dec_s - center_dec)
        # TAN projection onto output pixel
        denom = (math.sin(math.radians(center_dec)) * math.sin(math.radians(dec_s))
                 + math.cos(math.radians(center_dec)) * math.cos(math.radians(dec_s))
                 * math.cos(math.radians(ra_s - center_ra)))
        if denom <= 0:
            continue
        x_tan = (math.cos(math.radians(dec_s))
                 * math.sin(math.radians(ra_s - center_ra))) / denom
        y_tan = (math.cos(math.radians(center_dec)) * math.sin(math.radians(dec_s))
                 - math.sin(math.radians(center_dec)) * math.cos(math.radians(dec_s))
                 * math.cos(math.radians(ra_s - center_ra))) / denom

        px = half - math.degrees(x_tan) / out_scale_deg   # RA decreases left
        py = half - math.degrees(y_tan) / out_scale_deg   # Dec increases up

        if not (0 <= px < args.size and 0 <= py < args.size):
            continue

        vmag = star['vmag']
        r    = max(5, int(12 - vmag * 1.5))
        r    = min(r, 20)
        draw.ellipse([px - r, py - r, px + r, py + r],
                     outline=(255, 220, 0), width=2)

        raw_name = (star.get('name') or '').strip()
        if not raw_name:
            raw_name = f"{star.get('bayer','')}{star.get('const','')}".strip()
        name = _ascii_name(raw_name)
        if name:
            draw.text((px + r + 2, py - 6), f"{name} {vmag:.1f}",
                      fill=(255, 220, 0), font=font)
        n_plotted += 1

    draw.text((5,  5), "Yellow = catalog stars predicted by calibration",
              fill=(255, 220, 0), font=font_sm)
    draw.text((5, 18), f"Centre: RA {center_ra:.2f} Dec {center_dec:.2f}",
              fill=(200, 200, 200), font=font_sm)

    out_diag = os.path.join(out_dir, f"{stem}_rect_{args.size}px_diagnostic.jpg")
    img.save(out_diag, quality=92)
    print(f"Saved diag : {out_diag}  ({n_plotted} catalog stars plotted)")

    # ------------------------------------------------------------------
    # Plate solve parameters
    # ------------------------------------------------------------------
    scale_lo = round(args.scale * 0.85, 1)
    scale_hi = round(args.scale * 1.15, 1)
    sep = "=" * 68

    print()
    print(sep)
    print("  PLATE SOLVE PARAMETERS  (RECTIFIED — standard TAN)")
    print(sep)
    print(f"  Field centre RA  : {center_ra:.4f} deg  ({center_ra/15:.5f} h)")
    print(f"  Field centre Dec : {center_dec:.4f} deg")
    print(f"  Plate scale      : {args.scale:.1f} arcsec/px  ({scale_lo}-{scale_hi})")
    print(f"  FOV              : {out_fov_deg:.2f} deg")
    print(f"  Parity           : NEGATIVE (East is right, same as fisheye camera)")
    print()
    print("  ASTROMETRY.NET")
    print(f"    File       : {os.path.basename(out_jpg)}")
    print(f"    RA guess   : {center_ra:.3f} deg,  Dec: {center_dec:.3f} deg,  r=5 deg")
    print(f"    Scale      : {scale_lo} - {scale_hi} arcsec/px")
    print(f"    Parity     : NEGATIVE  <-- still required after rectification")
    print(f"    Downsample : 1")
    print()
    print("  ASTAP")
    print(f'    astap.exe -f "{os.path.basename(out_fits)}"')
    print(f"         -ra {center_ra/15:.5f}  -spd {center_dec + 90:.4f}")
    print(f"         -fov {out_fov_deg:.2f}  -s 40  -m")
    print(f"    (-m flag: East is still on the right in the rectified image)")
    print(sep)


if __name__ == '__main__':
    main()
