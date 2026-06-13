"""
Diagnose why single-image calibration fails on the reference frame.

Localises the 0% baseline failure: is it (A) the grid search failing to FIND
the known-good basin, or (B) the validation gate rejecting a genuinely-good
fit? Uses the committed multi-image model as ground truth.

Dev-only. Run from repo root:
    python scripts/dev/allsky/diagnose_single_fail.py
"""
import os
import sys

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from services.allsky.fisheye import FisheyeModel
from services.allsky.star_centroid import detect_stars, estimate_sky_circle
from services.allsky.catalogs import get_bright_stars
from services.allsky.coords import radec_to_altaz
from services.allsky.calibration_validate import (
    validate_bright_anchors, validate_lens_polynomial,
)
from services.allsky.calibration import _find_best_initial_model, _brightness_match
from scripts.dev.allsky.baseline_run import load_fits, fname_to_utc, LAT, LON

REF = "sample_images/lum_20260116_021511.fits"
GOOD = "sample_images/multi_calibration.json"

img = load_fits(REF)
dt = fname_to_utc(REF)
cx, cy, r = estimate_sky_circle(img)
detected = detect_stars(img, max_stars=200, sky_cx=cx, sky_cy=cy, sky_radius=r)

catalog = get_bright_stars(max_mag=6.5)
ah = []
for s in catalog:
    alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], LAT, LON, dt)
    if float(alt) > 3.0:
        ah.append((s, float(alt), float(az)))
ah.sort(key=lambda x: x[0]['vmag'])

print(f"Frame: {os.path.basename(REF)}  size={img.size}  sky_r={r:.0f}")
print(f"Detected stars: {len(detected)}   catalog above horizon: {len(ah)}")

good = FisheyeModel.load(GOOD)
print(f"\n--- Known-good multi model ---")
print(f"cx={good.cx:.1f} cy={good.cy:.1f} a1={good.a1:.1f} a3={good.a3:.1f} a5={good.a5:.1f}")
print(f"axis_alt={good.axis_alt:.2f} axis_az={good.axis_az:.2f} "
      f"roll={np.degrees(good.roll):.1f}deg east_left={good.east_left} "
      f"img={good.image_width}x{good.image_height}")

# (A) Does the known-good model pass the gates ON THIS FRAME?
poly_ok, poly_msg = validate_lens_polynomial(good)
anch_ok, anch_msg = validate_bright_anchors(good, ah, detected, sky_r=r)
gm = _brightness_match(detected, ah, good, tol_px=25.0)
print(f"\n--- Known-good model judged on this frame ---")
print(f"poly:   {poly_ok}  ({poly_msg})")
print(f"anchor: {anch_ok}  ({anch_msg})")
print(f"matches @25px: {len(gm)}")

# (B) What does the grid search land on?
gridm, gmatch = _find_best_initial_model(detected, ah, cx, cy, None,
                                         tol_scale=r / 1563.0)
print(f"\n--- Grid-search best initial model ---")
print(f"cx={gridm.cx:.1f} cy={gridm.cy:.1f} a1={gridm.a1:.1f} "
      f"axis_alt={gridm.axis_alt:.1f} axis_az={gridm.axis_az:.1f} "
      f"roll={np.degrees(gridm.roll):.1f}deg east_left={gridm.east_left}")
print(f"initial matches: {len(gmatch)}")
print(f"\nΔ vs known-good: axis_alt {gridm.axis_alt - good.axis_alt:+.1f}, "
      f"axis_az {gridm.axis_az - good.axis_az:+.1f}, "
      f"a1 {gridm.a1 - good.a1:+.1f}, "
      f"east_left {'SAME' if gridm.east_left == good.east_left else 'DIFFERENT'}")

print("\n=== Interpretation ===")
if anch_ok:
    print("Known-good model PASSES the gate on this frame → validation is fine;")
    print("the grid search / least_squares is failing to reach this basin.")
else:
    print("Known-good model FAILS the gate on this frame → either the gate is")
    print("too strict for single frames, or the multi model is not accurate here.")
