"""
Manual-correspondence fisheye calibration.

Uses user-confirmed pixel↔star pairs spanning the full sky to produce
a model that fits everywhere, not just one quadrant.

Confirmed correspondences:
  BigDipper 7 stars  (NNE quadrant, det 8,9,15,55,18,17,21)
  Regulus            (SSE, det #3 confirmed by user)
  Procyon            (SSW, det #5 confirmed by user)

Run from repo root:
    python scripts/allsky_manual_cal.py
"""
import sys, os, json, math
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from scipy.optimize import minimize, differential_evolution
from datetime import datetime, timezone

from services.allsky.coords import radec_to_altaz
from services.allsky.catalogs import get_bright_stars
from services.allsky.fisheye import FisheyeModel

DT  = datetime(2026, 1, 16, 8, 15, 11, tzinfo=timezone.utc)
LAT, LON = 38.9717, -95.2353

# -------------------------------------------------------------------
# Confirmed pixel positions (from allsky_debug detection list)
# These were visually identified by the observer
# -------------------------------------------------------------------
CONFIRMED = {
    # BigDipper handle (NNE quadrant)
    'Alkaid':  (769.6,  1028.4),   # det #8
    'Mizar':   (916.2,  1006.7),   # det #9
    'Alioth':  (982.5,  1056.2),   # det #15
    # BigDipper bowl
    'Megrez':  (1077.8, 1118.2),   # det #55
    'Phecda':  (1072.7, 1216.0),   # det #18
    'Merak':   (1242.8, 1243.3),   # det #17
    'Dubhe':   (1303.3, 1137.1),   # det #21
    # Cross-sky (user-confirmed)
    'Regulus': (1160.5, 2274.4),   # det #3
    'Procyon': (1991.3, 2458.4),   # det #5
}

# -------------------------------------------------------------------
# Resolve catalog alt/az for each confirmed star
# -------------------------------------------------------------------
stars_catalog = get_bright_stars(max_mag=4.5)
star_lookup = {}
for s in stars_catalog:
    n = s.get('name', '')
    if n:
        star_lookup[n] = s

correspondences = []  # (alt_deg, az_deg, px, py, name)
for name, (px, py) in CONFIRMED.items():
    if name not in star_lookup:
        print(f"WARNING: {name!r} not found in catalog — skipping")
        continue
    s = star_lookup[name]
    alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], LAT, LON, DT)
    correspondences.append((float(alt), float(az), px, py, name))
    print(f"  {name:<10} alt={float(alt):6.1f}  az={float(az):7.1f}  px=({px:.1f}, {py:.1f})")

print(f"\n{len(correspondences)} correspondences loaded")

# -------------------------------------------------------------------
# Fisheye projection (same formula as FisheyeModel.altaz_to_pixel)
# -------------------------------------------------------------------

def project(params, alt_deg, az_deg):
    """Project (alt, az) → (x, y) pixel using the given parameter vector."""
    cx, cy, a1, a3, a5, roll = params
    east_left = True

    alt_r = math.radians(alt_deg)
    az_r  = math.radians(az_deg)
    theta = math.pi / 2.0 - alt_r          # zenith angle
    t2    = theta * theta
    r     = a1 * theta + a3 * t2 * theta + a5 * t2 * t2 * theta

    # Bearing from optical axis in image plane
    phi = az_r - roll
    if east_left:
        phi = -phi

    x = cx + r * math.sin(phi)
    y = cy - r * math.cos(phi)
    return x, y


def residuals(params):
    total = 0.0
    for alt, az, px, py, _ in correspondences:
        x, y = project(params, alt, az)
        total += (x - px) ** 2 + (y - py) ** 2
    return total


def rms(params):
    n = len(correspondences)
    return math.sqrt(residuals(params) / n)


# -------------------------------------------------------------------
# Multi-start optimization
# -------------------------------------------------------------------

# Initial guess: image centre near sky circle centre, typical fisheye scale
x0 = [1430.0, 1869.0,   # cx, cy — V2 values
      1245.0, -43.7, -42.5,   # a1, a3, a5 — V2 values
      math.radians(-11.4)]    # roll — V2 value

bounds = [
    (1200,  1900),    # cx
    (1600,  2100),    # cy
    ( 700,  1600),    # a1
    (-250,    50),    # a3
    (-250,    50),    # a5
    (-math.pi, math.pi),  # roll
]

print("\n--- Optimization ---")
print("Starting from V2 parameters...")
r0 = rms(x0)
print(f"  V2 initial RMS: {r0:.2f} px")

# First pass: refine from V2 starting point
result1 = minimize(residuals, x0, method='L-BFGS-B', bounds=bounds,
                   options={'maxiter': 10000, 'ftol': 1e-12})

print(f"  L-BFGS-B pass 1 RMS: {rms(result1.x):.2f} px")

# Second pass: global search via differential evolution
print("Running differential evolution (global search)...")
de_result = differential_evolution(residuals, bounds,
                                   seed=42, maxiter=2000, tol=1e-10,
                                   popsize=20, mutation=(0.5, 1.5),
                                   recombination=0.9, workers=1,
                                   updating='deferred')
print(f"  DE global RMS: {rms(de_result.x):.2f} px")

# Third pass: refine DE result
result2 = minimize(residuals, de_result.x, method='L-BFGS-B', bounds=bounds,
                   options={'maxiter': 10000, 'ftol': 1e-12})
r_final = rms(result2.x)
print(f"  L-BFGS-B pass 2 RMS: {r_final:.2f} px")

# Pick best overall
candidates = [result1, result2]
best = min(candidates, key=lambda r: r.fun)
p = best.x
r_best = rms(p)

print(f"\n--- Best Result: RMS = {r_best:.2f} px ---")
print(f"  cx={p[0]:.2f}  cy={p[1]:.2f}")
print(f"  a1={p[2]:.2f}  a3={p[3]:.4f}  a5={p[4]:.4f}")
print(f"  roll={math.degrees(p[5]):.3f} deg")

print(f"\n--- Per-star residuals ---")
print(f"  {'Name':<10} {'Alt':>6}  {'Az':>7}  {'ProjX':>7}  {'ProjY':>7}  {'ConfX':>7}  {'ConfY':>7}  {'Err':>7}")
for alt, az, px, py, name in correspondences:
    fx, fy = project(p, alt, az)
    err = math.hypot(fx - px, fy - py)
    print(f"  {name:<10} {alt:6.1f}  {az:7.1f}  {fx:7.1f}  {fy:7.1f}  {px:7.1f}  {py:7.1f}  {err:7.2f}px")

# -------------------------------------------------------------------
# Save result
# -------------------------------------------------------------------
cal_data = {
    "cx":             float(p[0]),
    "cy":             float(p[1]),
    "a1":             float(p[2]),
    "a3":             float(p[3]),
    "a5":             float(p[4]),
    "roll":           float(p[5]),
    "axis_alt":       90.0,
    "axis_az":        0.0,
    "east_left":      True,
    "rms_residual":   float(r_best),
    "n_matches":      len(correspondences),
    "calibrated_at":  "",
}

out_path = "sample_images/bigdipper_v4_calibration.json"
with open(out_path, 'w') as f:
    json.dump(cal_data, f, indent=2)
print(f"\nSaved → {out_path}")

# Check if BigDipper assignment might be swapped
print("\n--- Assignment sanity check ---")
print("If any BigDipper star has error > 30px, the det assignment might be wrong.")
