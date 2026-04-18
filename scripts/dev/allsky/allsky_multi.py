#!/usr/bin/env python3
"""
Multi-image all-sky calibration tool.

Calibrates a fisheye lens model from a sequence of FITS images, then
optionally generates a verification overlay on the last (or best) frame.

All images must be from the same fixed camera.  The more images you
provide — especially spread across different times of night or different
nights — the better the calibration, particularly for axis_alt (zenith
pointing) and the higher-order polynomial terms.

Usage:
    # Calibrate from all lum_* files in a folder
    python scripts/allsky_multi.py sample_images/lum_20260116_*.fits

    # Calibrate from multiple nights
    python scripts/allsky_multi.py sample_images/*.fits

    # With explicit site coordinates
    python scripts/allsky_multi.py sample_images/lum_*.fits --lat 52.0 --lon -1.0

    # Save calibration JSON to a specific path
    python scripts/allsky_multi.py sample_images/lum_*.fits --out multi_cal.json

    # Also generate a verification overlay on the last frame
    python scripts/allsky_multi.py sample_images/lum_*.fits --verify
"""
import os
import sys
import re
import glob
import json
import argparse
import math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# FITS loading (PIL y-down, percentile-stretched uint8 for detection)
# ---------------------------------------------------------------------------

def _load_fits(path: str):
    """Return (PIL Image for detection, raw float32 array) from FITS file."""
    from astropy.io import fits
    from PIL import Image

    with fits.open(path) as hdul:
        data = hdul[0].data.astype(np.float32)

    if data.ndim == 3 and data.shape[0] in (1, 3, 4) and data.shape[0] < data.shape[1]:
        data = np.transpose(data, (1, 2, 0))
    if data.ndim == 3:
        data = data[..., :3].mean(axis=2)

    # FITS row-0 = bottom → flip to PIL y-down
    data = np.flipud(data)

    # Percentile stretch → uint8 for star detection
    p1, p99 = np.percentile(data, 1), np.percentile(data, 99)
    stretched = np.clip((data - p1) / max(p99 - p1, 1) * 255, 0, 255).astype(np.uint8)
    rgb = np.stack([stretched] * 3, axis=-1)
    return Image.fromarray(rgb, 'RGB'), data


def _parse_time(path: str):
    """Extract UTC datetime from filename (YYYYMMDD_HHMMSS) or raise."""
    from datetime import datetime, timezone
    m = re.search(r'(\d{8})_(\d{6})', os.path.basename(path))
    if m:
        ds, ts = m.group(1), m.group(2)
        iso = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}T{ts[:2]}:{ts[2:4]}:{ts[4:6]}"
        return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    raise ValueError(f"Cannot parse datetime from filename: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Multi-image all-sky fisheye calibration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument('fits_files', nargs='+',
                    help='FITS files (globs expanded by shell or here)')
    ap.add_argument('--lat',      type=float, default=52.0,
                    help='Observer latitude deg N (default 52.0)')
    ap.add_argument('--lon',      type=float, default=-1.0,
                    help='Observer longitude deg E (default -1.0)')
    ap.add_argument('--out',      default=None,
                    help='Output calibration JSON path '
                         '(default: multi_calibration.json in same dir as first file)')
    ap.add_argument('--max-residual', type=float, default=10.0,
                    help='Max median residual px to accept (default 10.0)')
    ap.add_argument('--min-matches',  type=int, default=20,
                    help='Min total star matches required (default 20)')
    ap.add_argument('--verify', action='store_true',
                    help='Generate a verification overlay on the best frame')
    ap.add_argument('--mag-limit', type=float, default=6.5,
                    help='Catalog magnitude limit for matching (default 6.5)')
    args = ap.parse_args()

    # Expand globs (Windows shell doesn't auto-expand)
    paths = []
    for pattern in args.fits_files:
        expanded = glob.glob(pattern)
        if expanded:
            paths.extend(sorted(expanded))
        elif os.path.exists(pattern):
            paths.append(pattern)
        else:
            print(f"WARNING: no files matched: {pattern}")

    # Exclude any crops, rectified outputs, or debug artefacts
    paths = [p for p in paths
             if '_crop_' not in os.path.basename(p)
             and '_rect_' not in os.path.basename(p)
             and 'debug' not in os.path.basename(p).lower()]

    if not paths:
        print("ERROR: No FITS files found.")
        sys.exit(1)

    # Remove duplicates, keep order
    seen = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    print(f"Found {len(paths)} FITS file(s):")
    for p in paths:
        print(f"  {os.path.basename(p)}")

    # -----------------------------------------------------------------------
    # Load images
    # -----------------------------------------------------------------------
    print("\nLoading images ...")
    images_and_times = []
    for path in paths:
        try:
            pil_img, _ = _load_fits(path)
            dt = _parse_time(path)
            images_and_times.append((pil_img, dt))
            print(f"  {os.path.basename(path)}  {dt.isoformat()} UTC  "
                  f"({pil_img.width}x{pil_img.height})")
        except Exception as e:
            print(f"  SKIP {os.path.basename(path)}: {e}")

    if not images_and_times:
        print("ERROR: No images loaded successfully.")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Multi-image calibration
    # -----------------------------------------------------------------------
    import logging
    logging.basicConfig(level=logging.INFO,
                        format='%(levelname)s %(name)s: %(message)s')

    from services.allsky.multi_calibrate import multi_calibrate
    from services.allsky.calibration import CalibrationError

    print(f"\nRunning multi-image calibration on {len(images_and_times)} frame(s) ...")
    try:
        model = multi_calibrate(
            images_and_times,
            lat_deg=args.lat,
            lon_deg=args.lon,
            min_total_matches=args.min_matches,
            max_residual_px=args.max_residual,
        )
    except CalibrationError as e:
        print(f"\nCalibration FAILED: {e}")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    print("\n=== MULTI-IMAGE CALIBRATION RESULT ===")
    print(f"  Optical centre     : ({model.cx:.1f}, {model.cy:.1f}) px")
    print(f"  a1 (radial scale)  : {model.a1:.2f} px/rad")
    print(f"  a3, a5             : {model.a3:.2f}, {model.a5:.4f}")
    print(f"  Roll               : {math.degrees(model.roll):.2f} deg")
    print(f"  axis_alt           : {model.axis_alt:.4f} deg  "
          f"{'(AT ZENITH BOUNDARY)' if abs(model.axis_alt - 90.0) < 0.01 else '(OFF ZENITH)'}")
    print(f"  axis_az            : {model.axis_az:.4f} deg")
    print(f"  Plate scale        : {206265.0 / model.a1:.2f} arcsec/px at centre")
    print(f"  Total star matches : {model.n_matches}")
    print(f"  Median RMS         : {model.rms_residual:.2f} px")

    # Zenith verdict
    if abs(model.axis_alt - 90.0) < 0.01:
        print("\n  Zenith: model converged to the 90-deg boundary.")
        print("  Camera is pointing at or very close to straight up.")
        print("  Consider Polaris test (predicted at cx+dx, cy+dy) to confirm.")
    else:
        tilt_deg = 90.0 - model.axis_alt
        print(f"\n  Zenith: camera is tilted {tilt_deg:.2f} deg from vertical")
        print(f"  toward azimuth {model.axis_az:.1f} deg.")
        tilt_px = model.a1 * math.radians(tilt_deg)
        az_r    = math.radians(model.axis_az)
        zenith_x = model.cx - tilt_px * math.sin(az_r)
        zenith_y = model.cy + tilt_px * math.cos(az_r)
        print(f"  True zenith pixel  : ({zenith_x:.0f}, {zenith_y:.0f})")
        print(f"  Optical centre     : ({model.cx:.0f}, {model.cy:.0f})")

    # Top-10 worst residuals (for diagnostics)
    if getattr(model, 'matched_stars', None):
        worst = sorted(model.matched_stars, key=lambda s: -s['residual_px'])[:5]
        print("\n  5 highest residuals:")
        for s in worst:
            t = s.get('frame_time', '')[:19]
            print(f"    {s['name']:<14} vmag={s['vmag']:.1f} "
                  f"res={s['residual_px']:.1f}px  [{t}]")

    # -----------------------------------------------------------------------
    # Save calibration JSON
    # -----------------------------------------------------------------------
    first_dir = os.path.dirname(os.path.abspath(paths[0]))
    out_path  = args.out or os.path.join(first_dir, 'multi_calibration.json')

    from datetime import timezone
    from datetime import datetime as _dt
    import dataclasses

    cal_dict = dataclasses.asdict(model)
    cal_dict['calibrated_at']  = _dt.now(timezone.utc).isoformat()
    cal_dict['source_images']  = [os.path.basename(p) for p in paths]
    cal_dict['n_source_images'] = len(paths)

    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(cal_dict, fh, indent=2)
    print(f"\nCalibration saved: {out_path}")

    # -----------------------------------------------------------------------
    # Optional verification overlay
    # -----------------------------------------------------------------------
    if args.verify:
        _generate_verify(paths[-1], model, args.lat, args.lon, out_path)


# ---------------------------------------------------------------------------
# Verification overlay
# ---------------------------------------------------------------------------

def _generate_verify(fits_path: str, model, lat: float, lon: float, cal_path: str):
    """Generate a verification overlay image using the multi-calibration model."""
    import re
    from datetime import datetime, timezone
    from PIL import Image, ImageDraw, ImageFont
    from services.allsky.catalogs import get_bright_stars, get_constellation_lines
    from services.allsky.coords import radec_to_altaz

    print(f"\nGenerating verification overlay on {os.path.basename(fits_path)} ...")

    pil_img, _ = _load_fits(fits_path)
    m = re.search(r'(\d{8})_(\d{6})', os.path.basename(fits_path))
    ds, ts = m.group(1), m.group(2)
    dt = datetime(int(ds[:4]), int(ds[4:6]), int(ds[6:8]),
                  int(ts[:2]), int(ts[2:4]), int(ts[4:6]),
                  tzinfo=timezone.utc)

    overlay = Image.new('RGBA', pil_img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    base    = pil_img.convert('RGBA')

    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except Exception:
        font = ImageFont.load_default()

    scale = max(pil_img.width, pil_img.height) / 750.0

    # Constellation lines
    for ra1, dec1, ra2, dec2 in get_constellation_lines():
        alt1, az1 = radec_to_altaz(ra1, dec1, lat, lon, dt, refraction=True)
        alt2, az2 = radec_to_altaz(ra2, dec2, lat, lon, dt, refraction=True)
        if float(alt1) < 5 or float(alt2) < 5:
            continue
        xy1 = model.altaz_to_pixel(float(alt1), float(az1))
        xy2 = model.altaz_to_pixel(float(alt2), float(az2))
        if xy1 and xy2:
            draw.line([*xy1, *xy2], fill=(80, 160, 255, 160),
                      width=max(1, int(scale)))

    # Named bright stars — skipped if projected position falls over equipment
    import numpy as np
    from services.allsky.render_objects import _is_sky_visible
    _gray = np.array(pil_img.convert('L'))
    for star in get_bright_stars(max_mag=3.5):
        alt, az = radec_to_altaz(
            star['ra_deg'], star['dec_deg'], lat, lon, dt, refraction=True
        )
        if float(alt) < 5:
            continue
        xy = model.altaz_to_pixel(float(alt), float(az))
        if xy is None:
            continue
        x, y = int(xy[0]), int(xy[1])
        if not _is_sky_visible(_gray, x, y):
            continue
        r = max(4, int((5 - star['vmag']) * 2 * scale))
        draw.ellipse([x - r, y - r, x + r, y + r],
                     outline=(255, 220, 0, 220), width=max(1, int(scale)))
        name = (star.get('name') or '').strip()
        if name:
            draw.text((x + r + 3, y - 6), name, fill=(255, 220, 0, 220), font=font)

    result = Image.alpha_composite(base, overlay).convert('RGB')
    stem   = os.path.splitext(fits_path)[0]
    out    = stem + '_multi_verify.jpg'
    result.save(out, quality=92)
    print(f"Saved: {out}")


if __name__ == '__main__':
    main()
