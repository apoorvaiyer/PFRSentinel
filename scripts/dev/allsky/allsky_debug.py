"""
All-Sky Debug / Calibration Tool
=================================

Run calibration on a saved all-sky image and write three debug images:

  <stem>_1_detections.jpg  — All detected star candidates (green circles)
  <stem>_2_matches.jpg     — Calibration match pairs on the clean image
                             (cyan circle = detected, magenta cross = catalog,
                              line shows the residual error)
  <stem>_3_overlay.jpg     — Full rendered overlay (constellations, grid, planets)
                             plus calibration match highlights

Usage
-----
    python scripts/allsky_debug.py <image_path> [options]

Examples
--------
    # Minimal — reads lat/lon from config, datetime from EXIF or now
    python scripts/allsky_debug.py "C:/path/to/allsky.jpg"

    # Explicit location + time (UTC)
    python scripts/allsky_debug.py allsky.jpg --lat 51.509 --lon -0.118 --utc "2026-04-03 04:11:21"

    # Only detect stars (no calibration attempt)
    python scripts/allsky_debug.py allsky.jpg --detect-only

    # Use saved calibration and render overlay
    python scripts/allsky_debug.py allsky.jpg --overlay-only --cal path/to/cal.json

    # Specify image centre guess
    python scripts/allsky_debug.py allsky.jpg --cx 960 --cy 540
"""

import argparse
import json
import os
import sys
import math
from datetime import datetime, timezone

# Ensure stdout/stderr use UTF-8 on Windows (avoids cp1252 UnicodeEncodeError)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Allow running from repo root or scripts/ directory
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)


def main():
    parser = argparse.ArgumentParser(
        description="All-sky calibration debug tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("image", help="Path to all-sky JPEG/PNG/FITS image")
    parser.add_argument("--lat",  type=float, default=None, help="Observer latitude (degrees N)")
    parser.add_argument("--lon",  type=float, default=None, help="Observer longitude (degrees E)")
    parser.add_argument("--utc",  default=None,
                        help="UTC datetime of image, e.g. '2026-04-03 04:11:21'")
    parser.add_argument("--cx",   type=float, default=None, help="Optical centre X guess (px)")
    parser.add_argument("--cy",   type=float, default=None, help="Optical centre Y guess (px)")
    parser.add_argument("--max-mag", type=float, default=5.5,
                        help="Catalog star magnitude limit (default 5.5)")
    parser.add_argument("--detect-only", action="store_true",
                        help="Only run star detection; skip calibration")
    parser.add_argument("--overlay-only", action="store_true",
                        help="Skip calibration; just render overlay with --cal")
    parser.add_argument("--cal",  default=None,
                        help="Path to existing calibration JSON (skips fitting)")
    parser.add_argument("--out",  default=None,
                        help="Output base stem, e.g. 'C:/tmp/run01'. "
                             "Files are written as <stem>_1_detections.jpg etc. "
                             "(default: <input>_debug)")
    parser.add_argument("--min-matches", type=int, default=8,
                        help="Minimum star matches required (default 8)")
    parser.add_argument("--sky-radius", type=float, default=None,
                        help="Radius of the sky circle in pixels (e.g. 340 for a "
                             "750×750 image). If omitted, a grid search is performed.")
    parser.add_argument("--trim", type=float, default=0.15,
                        help="Fraction of the fitted sky radius to trim inward to exclude "
                             "horizon buildings/equipment (default 0.15 = 15%%). "
                             "Increase (e.g. 0.25) to cut more; decrease (e.g. 0.05) "
                             "to keep more of the horizon.")

    # Overlay layer toggles
    overlay = parser.add_argument_group("overlay layers")
    overlay.add_argument("--no-constellations", action="store_true",
                         help="Disable constellation lines and labels")
    overlay.add_argument("--no-planets",        action="store_true",
                         help="Disable planet/Moon markers")
    overlay.add_argument("--no-messier",         action="store_true",
                         help="Disable Messier object markers")
    overlay.add_argument("--no-ngc",            action="store_true",
                         help="Disable NGC/IC deep-sky object markers")
    overlay.add_argument("--ngc-top", type=int, default=10,
                         help="Number of brightest NGC objects to show (default 10; "
                              "0 = all above horizon up to --max-mag)")
    overlay.add_argument("--stars", type=int, default=0,
                         help="Number of brightest catalog stars to mark as yellow "
                              "circles on the overlay image (default 0 = none; use e.g. --stars 15)")
    overlay.add_argument("--stars-pct", type=float, default=None,
                         help="Alternative to --stars: show the top P%% brightest "
                              "catalog stars (e.g. 10 = top 10%%). Overrides --stars.")
    overlay.add_argument("--con-width", type=int, default=2,
                         help="Constellation line width in pixels (default 2)")

    parser.add_argument("--verify", action="store_true",
                        help="Calibration-verify mode: show only constellation lines "
                             "+ the 20 brightest named stars. No Messier/NGC/planets. "
                             "Makes it easy to confirm labels sit on real star pixels.")
    parser.add_argument("--verbose", action="store_true",
                        help="Show catalog stars above horizon and their projected positions")
    args = parser.parse_args()

    # ------------------------------------------------------------------ setup
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        sys.exit("Pillow is required: pip install Pillow")

    import numpy as np

    # Derive stem: strip extension if user passed a full path
    if args.out:
        out_stem = os.path.splitext(args.out)[0]
    else:
        out_stem = _make_out_stem(args.image)

    print(f"Input:  {args.image}")
    print(f"Output: {out_stem}_[1|2|3].jpg")

    # ------------------------------------------------------------------ load image
    img = _load_image(args.image)
    if img is None:
        sys.exit(f"Cannot open image: {args.image}")

    W, H = img.size
    print(f"Image:  {W}×{H} px")
    scale = max(W, H) / 750.0   # marker/font scale relative to 750px reference

    # ------------------------------------------------------------------ datetime
    dt = _parse_datetime(args.utc, args.image)
    print(f"UTC:    {dt.isoformat()}")

    # ------------------------------------------------------------------ lat/lon
    lat, lon = _get_latlon(args.lat, args.lon)
    print(f"Lat/Lon: {lat:.4f}°, {lon:.4f}°")

    # ------------------------------------------------------------------ catalog preview
    try:
        from services.allsky.catalogs import get_bright_stars
        from services.allsky.coords import radec_to_altaz as _r2a
        cat_all = get_bright_stars(max_mag=6.5)
        above_h = []
        for s in cat_all:
            alt, az = _r2a(s['ra_deg'], s['dec_deg'], lat, lon, dt)
            if float(alt) > 3.0:
                above_h.append((s, float(alt), float(az)))
        above_h.sort(key=lambda x: x[0]['vmag'])
        print(f"\n--- Catalog Preview ---")
        print(f"Stars in BSC5 (mag ≤6.5): {len(cat_all)}")
        print(f"Above horizon (alt >3°):  {len(above_h)}")
        if above_h:
            print(f"Brightest 10 visible catalog stars:")
            for s, alt, az in above_h[:10]:
                name = s.get('name') or f"HR{s.get('hr', '?')}"
                print(f"  {name:<12} V={s['vmag']:.1f}  alt={alt:.1f}°  az={az:.1f}°")
    except Exception as e:
        print(f"(Catalog preview failed: {e})")

    # ------------------------------------------------------------------ sky circle
    from services.allsky.star_centroid import detect_stars, estimate_sky_circle

    trim = float(np.clip(args.trim, 0.0, 0.40))
    if args.sky_radius and args.cx is not None and args.cy is not None:
        sky_cx, sky_cy, sky_r = args.cx, args.cy, args.sky_radius
        print(f"\nSky circle (from args): centre=({sky_cx:.0f}, {sky_cy:.0f})  "
              f"radius={sky_r:.0f}px")
    else:
        sky_cx, sky_cy, sky_r = estimate_sky_circle(img, trim_fraction=trim)
        if args.cx is not None:
            sky_cx = args.cx
        if args.cy is not None:
            sky_cy = args.cy
        if args.sky_radius is not None:
            sky_r = args.sky_radius
        print(f"\nSky circle (auto): centre=({sky_cx:.0f}, {sky_cy:.0f})  "
              f"radius={sky_r:.0f}px  trim={trim:.0%}")

    # ------------------------------------------------------------------ detect stars
    print("\n--- Star Detection ---")
    detected = detect_stars(img, max_stars=200, border_px=20,
                            sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r)
    print(f"Detected {len(detected)} candidate stars")
    if detected:
        xs = [x for x, _, _ in detected]
        ys = [y for _, y, _ in detected]
        fs = [f for _, _, f in detected]
        print(f"X range: {min(xs):.0f}–{max(xs):.0f}  "
              f"Y range: {min(ys):.0f}–{max(ys):.0f}  "
              f"Flux: {min(fs):.0f}–{max(fs):.0f}")
        for i, (x, y, f) in enumerate(detected):
            print(f"  [{i+1:2d}] ({x:6.1f}, {y:6.1f})  flux={f:.0f}")

    # Always save image 1 — all detected candidates + sky circle boundary
    det_path = out_stem + '_1_detections.jpg'
    _draw_detections(img, detected, scale,
                     sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r).save(det_path, quality=92)
    print(f"\nSaved → {det_path}")
    print(f"  Green circles = all {len(detected)} detected candidates")

    if args.detect_only:
        return

    # ------------------------------------------------------------------ load/run calibration
    from services.allsky.fisheye import FisheyeModel

    if args.overlay_only or args.cal:
        cal_path = args.cal or _find_default_cal()
        if not cal_path or not os.path.exists(cal_path):
            sys.exit("No calibration file found. Run without --overlay-only first.")
        model = FisheyeModel.try_load(cal_path)
        if not model or not model.is_valid():
            sys.exit(f"Invalid calibration at {cal_path}")
        print(f"\nLoaded calibration: {model}")
        matched_stars = []
        # Project catalog stars for overlay circles
        from services.allsky.catalogs import get_bright_stars
        from services.allsky.coords import radec_to_altaz
        from services.allsky.render_objects import _is_sky_visible
        _cat = get_bright_stars(max_mag=args.max_mag)
        cat_projected = []
        _gray = np.array(img.convert('L'))
        for _s in _cat:
            _alt, _az = radec_to_altaz(_s['ra_deg'], _s['dec_deg'], lat, lon, dt)
            _alt, _az = float(_alt), float(_az)
            if _alt > 5.0:
                _xy = model.altaz_to_pixel(_alt, _az)
                if _xy is not None and _is_sky_visible(_gray, int(_xy[0]), int(_xy[1])):
                    cat_projected.append({
                        'cat_px': _xy, 'alt': _alt, 'az': _az,
                        'vmag': _s['vmag'], 'name': _s.get('name', ''),
                    })
    else:
        print("\n--- Calibration ---")
        from services.allsky.calibration import calibrate, CalibrationError

        print(f"Optical centre guess: ({sky_cx:.0f}, {sky_cy:.0f})  "
              f"sky radius: {sky_r:.0f}px  "
              f"→ a1 hint ≈ {sky_r / 1.5708:.0f} px/rad")

        try:
            model = calibrate(
                img,
                lat_deg=lat,
                lon_deg=lon,
                dt=dt,
                max_stars=200,
                min_matches=args.min_matches,
                max_residual_px=15.0,
                image_cx=sky_cx,
                image_cy=sky_cy,
                sky_radius_px=sky_r,
            )
        except CalibrationError as e:
            print(f"\nCalibration FAILED: {e}")
            print(f"  Detection image already saved → {det_path}")
            _print_calibration_hints(detected, W, H)
            return

        print(f"\nCalibration SUCCEEDED:")
        print(f"  Stars matched : {model.n_matches}")
        print(f"  RMS residual  : {model.rms_residual:.2f} px")
        print(f"  a1            : {model.a1:.1f} px/rad")
        print(f"  a3            : {model.a3:.4f}")
        print(f"  a5            : {model.a5:.6f}")
        print(f"  Optical centre: ({model.cx:.1f}, {model.cy:.1f})")
        print(f"  Axis alt/az   : ({model.axis_alt:.2f}°, {model.axis_az:.2f}°)")
        print(f"  Roll          : {math.degrees(model.roll):.2f}°")

        # Save calibration JSON
        cal_out = out_stem + '_calibration.json'
        model.save(cal_out)
        print(f"\nCalibration saved → {cal_out}")

        # Print matched star table
        matched_stars = getattr(model, 'matched_stars', [])
        if matched_stars:
            print(f"\n--- Matched Stars ({len(matched_stars)}, sorted by residual) ---")
            print(f"  {'Name':<18} {'Vmag':>5}  {'Alt':>6}  {'Az':>7}  "
                  f"{'DetX':>6}  {'DetY':>6}  {'Res':>7}")
            print(f"  {'-'*18} {'-----':>5}  {'------':>6}  {'-------':>7}  "
                  f"{'------':>6}  {'------':>6}  {'-------':>7}")
            for s in matched_stars:
                name = s['name'] or '(unnamed)'
                dx, dy = s['detected_px']
                print(f"  {name:<18} {s['vmag']:5.1f}  {s['alt']:6.1f}°  "
                      f"{s['az']:7.1f}°  {dx:6.1f}  {dy:6.1f}  {s['residual_px']:7.2f}px")

        # Save image 2 — match quality on clean image
        match_path = out_stem + '_2_matches.jpg'
        _draw_matches(img, matched_stars, scale).save(match_path, quality=92)
        print(f"\nSaved → {match_path}")
        print(f"  Cyan circles   = {len(matched_stars)} matched detected stars")
        print(f"  Magenta crosses = their catalog positions")
        print(f"  Lines show residual error (RMS {model.rms_residual:.2f} px)")

        # Build catalog projection list for image 3
        from services.allsky.catalogs import get_bright_stars
        from services.allsky.coords import radec_to_altaz
        from services.allsky.render_objects import _is_sky_visible
        catalog = get_bright_stars(max_mag=args.max_mag)
        cat_projected = []
        gray = np.array(img.convert('L'))
        for star in catalog:
            alt, az = radec_to_altaz(star['ra_deg'], star['dec_deg'], lat, lon, dt)
            alt, az = float(alt), float(az)
            if alt > 5.0:
                xy = model.altaz_to_pixel(alt, az)
                if xy is not None and _is_sky_visible(gray, int(xy[0]), int(xy[1])):
                    cat_projected.append({
                        'cat_px': xy,
                        'alt': alt,
                        'az': az,
                        'vmag': star['vmag'],
                        'name': star.get('name', ''),
                    })

    # ------------------------------------------------------------------ render overlay (image 3)
    print("\n--- Rendering Overlay ---")
    from services.allsky.overlay_renderer import render_allsky_overlay
    import tempfile

    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tf:
        tmp_cal = tf.name

    try:
        model.save(tmp_cal)
        # --verify: strip everything except constellation lines + bright named stars
        verify = args.verify
        config = {
            'enabled': True,
            'calibration_file': tmp_cal,
            '_lat': lat, '_lon': lon,
            'grid': {'enabled': False, 'horizon': False, 'altitude_rings': False,
                     'azimuth_lines': False, 'cardinal_labels': False,
                     'color': '#336633', 'line_width': 1, 'label_size': 14, 'opacity': 150},
            'constellations': {'enabled': not args.no_constellations,
                               'lines': True, 'labels': not verify,
                               'color': '#4488FF',
                               'line_width': args.con_width,
                               'label_size': 11, 'opacity': 230,
                               'edge_fade_px': 250},
            'messier': {'enabled': False if verify else not args.no_messier,
                        'color': '#FF8844',
                        'label_size': 12, 'opacity': 220},
            'ngc':     {'enabled': False if verify else not args.no_ngc,
                        'color': '#88FF44',
                        'label_size': 11, 'opacity': 220,
                        'min_magnitude': 10.0},
            'planets': {'enabled': False if verify else not args.no_planets,
                        'label_size': 14,
                        'opacity': 255, 'colors': {
                            'Mercury': '#B0B0B0', 'Venus': '#FFFFCC',
                            'Mars': '#FF6644', 'Jupiter': '#FFCC88',
                            'Saturn': '#FFDDAA', 'Uranus': '#88DDFF',
                            'Neptune': '#4466FF', 'Moon': '#FFFFEE',
                        }},
        }
        metadata = {'DATETIME': dt.strftime('%Y-%m-%d %H:%M:%S')}
        overlaid = render_allsky_overlay(img.convert('RGBA'), config, metadata)
        print("Overlay rendered successfully")
    finally:
        try:
            os.unlink(tmp_cal)
        except OSError:
            pass

    overlay_path = out_stem + '_3_overlay.jpg'
    matched_stars_for_overlay = getattr(model, 'matched_stars', None) if not args.overlay_only else None

    # Limit catalog crosses to the brightest N named stars
    # In --verify mode, only show named stars (so labels are always present for cross-checking)
    cat_sorted = sorted(cat_projected, key=lambda m: m['vmag'])  # brightest first
    n_total = len(cat_sorted)
    if verify:
        cat_display = [m for m in cat_sorted if m.get('name')][:20]
    elif args.stars_pct is not None:
        n_show = max(0, int(round(n_total * float(args.stars_pct) / 100.0)))
        cat_display = cat_sorted[:n_show]
    else:
        n_show = max(0, args.stars)
        cat_display = cat_sorted[:n_show]

    _draw_overlay(overlaid.convert('RGB'), cat_display, matched_stars_for_overlay, scale).save(
        overlay_path, quality=92
    )
    print(f"\nSaved → {overlay_path}")
    if cat_display:
        brightest_mag = cat_display[-1]['vmag'] if cat_display else 0
        print(f"  Yellow circles  = {len(cat_display)} brightest catalog stars "
              f"(mag ≤{brightest_mag:.1f} of {n_total} visible)")
    else:
        print(f"  Yellow circles  = none (use --stars N or --stars-pct P to enable)")
    active_layers = []
    if not args.no_constellations: active_layers.append("constellations")
    if not args.no_planets:        active_layers.append("planets")
    if not args.no_messier:        active_layers.append("messier")
    if not args.no_ngc:            active_layers.append(f"NGC top {args.ngc_top}" if args.ngc_top > 0 else "NGC all")
    print(f"  Overlay layers  = {', '.join(active_layers) if active_layers else 'grid only'}")
    if matched_stars_for_overlay:
        n = model.n_matches
        print(f"  Cyan circles    = {n} calibration-matched detected stars")
        print(f"  Magenta crosses = their catalog positions")
        print(f"  RMS = {model.rms_residual:.2f} px across {n} matches")


# ===================================================================
# Drawing helpers
# ===================================================================

def _px(value: float, scale: float) -> int:
    """Scale a pixel measurement from the 750px reference size."""
    return max(1, int(round(value * scale)))


def _load_font(size_px: int):
    """Load a readable TrueType font, falling back to PIL default."""
    from PIL import ImageFont
    for path in [
        'C:/Windows/Fonts/arial.ttf',
        'C:/Windows/Fonts/calibri.ttf',
        'C:/Windows/Fonts/segoeui.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]:
        try:
            return ImageFont.truetype(path, size_px)
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_detections(img, detected, scale: float = 1.0,
                     sky_cx=None, sky_cy=None, sky_radius=None):
    """Image 1 — original image with all detected candidates circled in green
    and the sky circle boundary drawn in orange."""
    from PIL import ImageDraw
    out = img.copy()
    draw = ImageDraw.Draw(out)
    font = _load_font(_px(11, scale))
    r = _px(7, scale)
    lw = _px(2, scale)

    # Sky circle boundary
    if sky_cx is not None and sky_radius is not None:
        sr = int(round(sky_radius))
        scx, scy = int(round(sky_cx)), int(round(sky_cy))
        draw.ellipse([(scx - sr, scy - sr), (scx + sr, scy + sr)],
                     outline=(255, 160, 0), width=_px(3, scale))
        draw.text((scx - _px(60, scale), scy + sr + _px(4, scale)),
                  f"sky r={sr}px", fill=(255, 160, 0), font=font)

    for i, (x, y, flux) in enumerate(detected):
        draw.ellipse([(x-r, y-r), (x+r, y+r)], outline=(0, 220, 60), width=lw)
        draw.text((x + r + _px(2, scale), y - _px(6, scale)),
                  str(i + 1), fill=(0, 220, 60), font=font)
    return out


def _draw_matches(img, matched_stars, scale: float = 1.0):
    """
    Image 2 — clean image with only the calibration-matched pairs.

    Cyan circle   = where the star was detected
    Magenta cross = where the catalog says it should be
    Line          = the residual error vector
    Label         = star name + residual in px
    """
    from PIL import ImageDraw
    out = img.copy()
    draw = ImageDraw.Draw(out)
    font_name = _load_font(_px(13, scale))
    font_res  = _load_font(_px(11, scale))
    r     = _px(11, scale)
    lw    = _px(2, scale)
    arm   = _px(7, scale)
    gap   = _px(4, scale)

    for s in matched_stars:
        dx, dy = s['detected_px']
        name = s['name'] or '(unnamed)'
        res  = s['residual_px']

        # Cyan circle on detected position
        draw.ellipse([(dx-r, dy-r), (dx+r, dy+r)], outline=(0, 220, 255), width=lw)

        if s.get('catalog_px'):
            cx, cy = s['catalog_px']
            dist = ((dx - cx)**2 + (dy - cy)**2) ** 0.5
            if dist > _px(2, scale):
                draw.line([(int(round(dx)), int(round(dy))),
                           (int(round(cx)), int(round(cy)))],
                          fill=(255, 60, 220), width=max(1, lw - 1))

        # Label: name above, residual below
        draw.text((dx + r + gap, dy - _px(10, scale)), name,
                  fill=(0, 220, 255), font=font_name)
        draw.text((dx + r + gap, dy + _px(2, scale)),  f"{res:.1f}px",
                  fill=(180, 220, 255), font=font_res)

    return out


def _draw_overlay(overlaid, catalog_projected, matched_stars, scale: float = 1.0):
    """
    Image 3 — full rendered overlay with catalog circles and matched pair highlights.

    All markers are hollow circles so the object inside is always visible.

    Yellow circle = catalog star projection
    Cyan circle   = matched detected star (slightly larger)
    Magenta circle = matched catalog position
    Line           = residual error vector between detected and catalog
    """
    from PIL import ImageDraw
    out = overlaid.copy()
    draw = ImageDraw.Draw(out)
    font_cat   = _load_font(_px(10, scale))
    font_match = _load_font(_px(12, scale))
    cat_r  = _px(6, scale)    # catalog star circle radius
    cat_lw = _px(2, scale)
    m_r    = _px(12, scale)   # matched detected circle radius
    m_r2   = _px(7,  scale)   # matched catalog circle radius (smaller, offset)
    m_lw   = _px(2, scale)
    gap    = _px(4, scale)

    # Catalog star labels (name only — no circle marker)
    for m in catalog_projected:
        cx, cy = m['cat_px']
        if m.get('name'):
            draw.text((cx + _px(4, scale), cy - _px(6, scale)),
                      m['name'], fill=(255, 220, 0), font=font_cat)

    # Calibration match markers — single cyan circle per match.
    # A line to the catalog position is drawn only when residual is notable (>3px scaled).
    residual_line_thresh = _px(3, scale)
    if matched_stars:
        for s in matched_stars:
            dx, dy = s['detected_px']
            # Single cyan hollow circle at detected position
            draw.ellipse([(dx - m_r, dy - m_r), (dx + m_r, dy + m_r)],
                         outline=(0, 220, 255), width=m_lw)
            if s.get('catalog_px'):
                cx, cy = s['catalog_px']
                dist = ((dx - cx)**2 + (dy - cy)**2) ** 0.5
                if dist > residual_line_thresh:
                    draw.line([(int(round(dx)), int(round(dy))),
                               (int(round(cx)), int(round(cy)))],
                              fill=(255, 80, 220), width=max(1, m_lw - 1))
            name = s.get('name', '')
            if name:
                draw.text((dx + m_r + gap, dy - _px(8, scale)),
                          name, fill=(0, 220, 255), font=font_match)

    return out


# ===================================================================
# Utilities
# ===================================================================

def _load_image(path: str):
    """
    Load an image from path as a PIL RGB Image.
    Supports JPEG/PNG/BMP/TIFF via Pillow and FITS via astropy.
    Returns None on failure.
    """
    from PIL import Image
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.fits', '.fit', '.fts'):
        try:
            from astropy.io import fits as astrofits
            import numpy as np
            with astrofits.open(path) as hdu:
                data = hdu[0].data
            if data is None:
                return None
            # Channels-first (C, H, W) → (H, W, C)
            if data.ndim == 3 and data.shape[0] in (1, 3, 4) and data.shape[0] < data.shape[1]:
                data = np.moveaxis(data, 0, -1)
            # Percentile stretch to uint8 (p1→0, p99→255).
            # Linear min-max compresses FITS sky background to 2-5 ADU
            # making it indistinguishable from dark corners.
            if data.dtype != np.uint8:
                flat = data.flatten().astype(np.float32)
                lo, hi = float(np.percentile(flat, 1)), float(np.percentile(flat, 99))
                if hi > lo:
                    data = ((data.astype(np.float32) - lo) / (hi - lo) * 255).clip(0, 255).astype(np.uint8)
                else:
                    data = np.zeros_like(data, dtype=np.uint8)
            if data.ndim == 2:
                return Image.fromarray(data, mode='L').convert('RGB')
            if data.shape[2] == 1:
                return Image.fromarray(data[:, :, 0], mode='L').convert('RGB')
            if data.shape[2] == 3:
                return Image.fromarray(data, mode='RGB')
            if data.shape[2] == 4:
                return Image.fromarray(data, mode='RGBA').convert('RGB')
            return None
        except Exception as e:
            print(f"Warning: FITS load failed ({e})")
            return None
    # Standard image formats via Pillow
    try:
        return Image.open(path).convert('RGB')
    except Exception as e:
        print(f"Warning: image load failed ({e})")
        return None


def _make_out_stem(image_path: str) -> str:
    base, _ = os.path.splitext(image_path)
    return base + '_debug'


def _parse_datetime(utc_str, image_path) -> datetime:
    """Parse UTC datetime from arg, EXIF, or fall back to now."""
    if utc_str:
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S',
                    '%Y%m%d_%H%M%S', '%d/%m/%Y %H:%M:%S'):
            try:
                return datetime.strptime(utc_str, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        sys.exit(f"Cannot parse --utc value: {utc_str!r}. Use format '2026-04-03 04:11:21'")

    # Try EXIF
    try:
        from PIL import Image as PILImage
        img = PILImage.open(image_path)
        exif = img._getexif() or {}
        dt_str = exif.get(36867) or exif.get(306)  # DateTimeOriginal or DateTime
        if dt_str:
            return datetime.strptime(dt_str, '%Y:%m:%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except Exception:
        pass

    print("Warning: no UTC datetime provided and no EXIF found. Using current UTC time.")
    print("  Use --utc '2026-04-03 04:11:21' for accurate results.")
    return datetime.now(timezone.utc)


def _get_latlon(lat_arg, lon_arg):
    """Get lat/lon from args or fall back to app config."""
    if lat_arg is not None and lon_arg is not None:
        return lat_arg, lon_arg

    try:
        from services.app_config import APP_DATA_FOLDER
        import json as _json
        cfg_path = os.path.join(os.getenv('APPDATA', ''), APP_DATA_FOLDER, 'config.json')
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                cfg = _json.load(f)
            weather = cfg.get('weather', {})
            lat = float(weather.get('latitude') or 0)
            lon = float(weather.get('longitude') or 0)
            if lat != 0.0 or lon != 0.0:
                print(f"Using lat/lon from config: {lat}, {lon}")
                return lat, lon
    except Exception:
        pass

    if lat_arg is None or lon_arg is None:
        print("Warning: lat/lon not specified and not in config.")
        print("  Using (0, 0) — results will be WRONG. Use --lat and --lon.")
        return 0.0, 0.0

    return float(lat_arg), float(lon_arg)


def _find_default_cal() -> str:
    """Try to find a calibration file in the app data folder."""
    try:
        from services.app_config import get_calibration_path
        path = get_calibration_path()
        if os.path.exists(path):
            return path
    except Exception:
        pass
    return ''


def _print_calibration_hints(detected, W, H):
    """Print diagnostics to help diagnose calibration failures."""
    print("\n--- Calibration Hints ---")
    print(f"  Image size: {W}×{H}")
    print(f"  Stars detected: {len(detected)}")
    if detected:
        fluxes = [f for _, _, f in detected]
        print(f"  Flux range: {min(fluxes):.0f} – {max(fluxes):.0f}")
        xs = [x for x, _, _ in detected]
        ys = [y for _, y, _ in detected]
        print(f"  X range: {min(xs):.0f} – {max(xs):.0f}")
        print(f"  Y range: {min(ys):.0f} – {max(ys):.0f}")
    print("\n  Suggestions:")
    print("  • Pass --lat <latitude> --lon <longitude> for your observatory")
    print("  • Pass --utc '<YYYY-MM-DD HH:MM:SS>' for the image UTC time")
    print("  • If image has few stars, try a longer exposure / darker sky")
    print("  • If telescope blocks centre, the calibration still works on edge stars")
    print("  • Try --min-matches 10 to accept a looser calibration")


if __name__ == '__main__':
    main()
