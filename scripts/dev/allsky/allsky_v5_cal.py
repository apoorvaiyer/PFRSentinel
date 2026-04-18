"""
Cross-sky fisheye calibration (V5).

Fixes the V2 problem (7 stars clustered in one 30° azimuth sector) by adding
Regulus (SSE) and Procyon (SSW) as hard anchor points, giving the optimizer
genuine cross-sky leverage.

Key improvements over V4/manual_cal:
  - Uses FisheyeModel.altaz_to_pixel() directly — NO custom projection formula
  - Frees axis_alt (camera tilt) — was locked at 90° in V2
  - BigDipper assignment is uncertain: Hungarian matching avoids penalising swaps
  - DE global search followed by L-BFGS-B refinement

Saves: sample_images/bigdipper_v5_calibration.json

Run from repo root:
    python scripts/allsky_v5_cal.py
"""
import sys
import os
import json
import math

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from scipy.optimize import minimize, differential_evolution, linear_sum_assignment
from datetime import datetime, timezone

from services.allsky.coords import radec_to_altaz
from services.allsky.catalogs import get_bright_stars
from services.allsky.fisheye import FisheyeModel

# ---------------------------------------------------------------------------
# Observation context
# ---------------------------------------------------------------------------

DT  = datetime(2026, 1, 16, 8, 15, 11, tzinfo=timezone.utc)   # filename is CST; +6h → UTC
LAT = 38.9717
LON = -95.2353

# ---------------------------------------------------------------------------
# Confirmed correspondences
# ---------------------------------------------------------------------------

# Exact pixel↔star pairs (user-confirmed, cross-sky anchors)
EXACT_PAIRS = {
    'Regulus': (1160.5, 2274.4),   # det #3, SSE
    'Procyon': (1991.3, 2458.4),   # det #5, SSW
}

# BigDipper detections confirmed as UMa region — star↔det ASSIGNMENT UNCERTAIN
BIGDIPPER_DETS = [
    (769.6,  1028.4),   # det #8
    (916.2,  1006.7),   # det #9
    (982.5,  1056.2),   # det #15
    (1077.8, 1118.2),   # det #55
    (1072.7, 1216.0),   # det #18
    (1242.8, 1243.3),   # det #17
    (1303.3, 1137.1),   # det #21
]

BIGDIPPER_NAMES = ['Alkaid', 'Mizar', 'Alioth', 'Megrez', 'Phecda', 'Merak', 'Dubhe']

# ---------------------------------------------------------------------------
# Resolve catalog alt/az for all stars
# ---------------------------------------------------------------------------

def _get_altaz(name: str, max_mag: float = 5.0):
    catalog = get_bright_stars(max_mag=max_mag)
    for s in catalog:
        if s.get('name') == name:
            alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], LAT, LON, DT)
            return float(alt), float(az)
    raise ValueError(f"Star {name!r} not found in catalog (max_mag={max_mag})")


print("Resolving catalog positions...")
exact = {}   # name → (alt, az, px, py)
for name, (px, py) in EXACT_PAIRS.items():
    alt, az = _get_altaz(name)
    exact[name] = (alt, az, px, py)
    print(f"  {name:<10}  alt={alt:6.1f}°  az={az:7.1f}°  target=({px:.1f}, {py:.1f})")

bd = []     # list of (alt, az, name)
for name in BIGDIPPER_NAMES:
    alt, az = _get_altaz(name)
    bd.append((alt, az, name))
    print(f"  {name:<10}  alt={alt:6.1f}°  az={az:7.1f}°  (Hungarian pool)")

# Verification-only star (not in cost, just checked at the end)
arcturus_altaz = _get_altaz('Arcturus', max_mag=1.0)
print(f"  {'Arcturus':<10}  alt={arcturus_altaz[0]:6.1f}°  az={arcturus_altaz[1]:7.1f}°  (verification only)")

# ---------------------------------------------------------------------------
# Cost function
# ---------------------------------------------------------------------------

# Parameter vector order: [cx, cy, a1, a3, a5, roll, axis_alt, axis_az]
_PARAM_ORDER = ('cx', 'cy', 'a1', 'a3', 'a5', 'roll', 'axis_alt', 'axis_az')

BD_DETS_ARR = np.array(BIGDIPPER_DETS)   # (7, 2)


def _make_model(params) -> FisheyeModel:
    cx, cy, a1, a3, a5, roll, axis_alt, axis_az = params
    return FisheyeModel(
        cx=cx, cy=cy,
        a1=a1, a3=a3, a5=a5,
        roll=roll,
        axis_alt=axis_alt,
        axis_az=axis_az,
        east_left=True,
    )


def residuals(params) -> float:
    """Sum of squared pixel errors — exact anchors + Hungarian BigDipper."""
    model = _make_model(params)
    total = 0.0

    # --- Exact anchors ---
    for alt, az, px, py in exact.values():
        xy = model.altaz_to_pixel(alt, az)
        if xy is None:
            return 1e10
        total += (xy[0] - px) ** 2 + (xy[1] - py) ** 2

    # --- BigDipper: Hungarian min-cost assignment ---
    n = len(bd)
    m = len(BIGDIPPER_DETS)
    cost = np.empty((n, m))
    for i, (alt, az, _) in enumerate(bd):
        xy = model.altaz_to_pixel(alt, az)
        if xy is None:
            cost[i, :] = 1e8
            continue
        dx = xy[0] - BD_DETS_ARR[:, 0]
        dy = xy[1] - BD_DETS_ARR[:, 1]
        cost[i] = dx * dx + dy * dy

    row_ind, col_ind = linear_sum_assignment(cost)
    total += float(cost[row_ind, col_ind].sum())

    # --- Regularization: keep polynomial terms small relative to a1 ---
    _, _, a1, a3, a5, _, _, _ = params
    if a1 > 0:
        total += _REG_A3 * (a3 / a1) ** 2 * 1e4
        total += _REG_A5 * (a5 / a1) ** 2 * 1e4

    return total


def rms_px(params) -> float:
    n = len(exact) + len(bd)   # 9 stars
    return math.sqrt(residuals(params) / n)

# ---------------------------------------------------------------------------
# Bounds and initial guess (V2 as warm start)
# ---------------------------------------------------------------------------

# [cx,  cy,   a1,    a3,    a5,    roll,  axis_alt, axis_az]
BOUNDS = [
    (1300, 1800),    # cx
    (1600, 2100),    # cy
    ( 800, 1600),    # a1
    (-250,   50),    # a3
    (-250,   50),    # a5
    (-0.6,  0.6),    # roll  (radians ~±34°)
    (  75,   90),    # axis_alt (degrees)
    ( -30,   30),    # axis_az  (degrees)
]

# Regularization weight — soft constraint keeping polynomial terms
# proportional to a1 (avoids degenerate large-a3 / large-a5 solutions).
# Penalizes |a3/a1| > ~0.08 and |a5/a1| > ~0.08.
_REG_A3 = 0.5    # px²  per unit of (a3/a1)²
_REG_A5 = 2.0    # px²  per unit of (a5/a1)²

X0 = [1430.59, 1868.81, 1245.67, -43.7267, -42.522, -0.19877, 90.0, 0.099]

print(f"\nV2 warm-start RMS: {rms_px(X0):.2f} px")

# ---------------------------------------------------------------------------
# Optimization
# ---------------------------------------------------------------------------

print("\n--- Pass 1: L-BFGS-B from V2 warm start ---")
r1 = minimize(residuals, X0, method='L-BFGS-B', bounds=BOUNDS,
              options={'maxiter': 20000, 'ftol': 1e-14, 'gtol': 1e-10})
print(f"  RMS: {rms_px(r1.x):.2f} px  (converged={r1.success})")

print("\n--- Pass 2: Differential Evolution global search ---")
_de_iter = [0]

def _de_callback(xk, convergence):
    _de_iter[0] += 1
    if _de_iter[0] % 250 == 0:
        print(f"    iter {_de_iter[0]:4d}  RMS={rms_px(xk):.2f} px  conv={convergence:.2e}")

de = differential_evolution(
    residuals,
    BOUNDS,
    seed=42,
    maxiter=3000,
    tol=1e-12,
    popsize=20,
    mutation=(0.5, 1.5),
    recombination=0.9,
    workers=1,
    updating='deferred',
    callback=_de_callback,
)
print(f"  DE RMS: {rms_px(de.x):.2f} px  (success={de.success})")

print("\n--- Pass 3: L-BFGS-B refine DE result ---")
r2 = minimize(residuals, de.x, method='L-BFGS-B', bounds=BOUNDS,
              options={'maxiter': 20000, 'ftol': 1e-14, 'gtol': 1e-10})
print(f"  RMS: {rms_px(r2.x):.2f} px  (converged={r2.success})")

# Pick best of all passes
candidates = [('L-BFGS-B pass 1', r1.x), ('DE→L-BFGS-B pass 3', r2.x)]
best_label, best_p = min(candidates, key=lambda c: residuals(c[1]))
print(f"\n--- Best: {best_label}  RMS = {rms_px(best_p):.2f} px ---")

# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------

p = best_p
cx, cy, a1, a3, a5, roll, axis_alt, axis_az = p
model = _make_model(p)

print(f"\n{'Parameter':>12}  {'Value':>12}")
print(f"  {'cx':>10}  {cx:12.3f} px")
print(f"  {'cy':>10}  {cy:12.3f} px")
print(f"  {'a1':>10}  {a1:12.3f} px/rad")
print(f"  {'a3':>10}  {a3:12.4f}")
print(f"  {'a5':>10}  {a5:12.6f}")
print(f"  {'roll':>10}  {math.degrees(roll):12.4f}°  ({roll:.6f} rad)")
print(f"  {'axis_alt':>10}  {axis_alt:12.4f}°")
print(f"  {'axis_az':>10}  {axis_az:12.4f}°")

print("\n--- Exact anchor residuals ---")
print(f"  {'Star':<10} {'Alt':>6} {'Az':>7}  {'ProjX':>7} {'ProjY':>7}  {'TgtX':>7} {'TgtY':>7}  {'Err':>7}")
for name, (alt, az, px, py) in exact.items():
    xy = model.altaz_to_pixel(alt, az)
    if xy is None:
        print(f"  {name:<10}  NOT PROJECTED")
    else:
        err = math.hypot(xy[0] - px, xy[1] - py)
        print(f"  {name:<10} {alt:6.1f} {az:7.1f}  {xy[0]:7.1f} {xy[1]:7.1f}"
              f"  {px:7.1f} {py:7.1f}  {err:7.2f}px")

print("\n--- BigDipper Hungarian assignment ---")
n, m = len(bd), len(BIGDIPPER_DETS)
cost = np.empty((n, m))
proj_bd = []
for i, (alt, az, name) in enumerate(bd):
    xy = model.altaz_to_pixel(alt, az)
    proj_bd.append(xy)
    if xy is None:
        cost[i, :] = 1e8
    else:
        dx = xy[0] - BD_DETS_ARR[:, 0]
        dy = xy[1] - BD_DETS_ARR[:, 1]
        cost[i] = dx * dx + dy * dy

row_ind, col_ind = linear_sum_assignment(cost)
for ri, ci in zip(row_ind, col_ind):
    alt, az, name = bd[ri]
    dpx, dpy = BIGDIPPER_DETS[ci]
    xy = proj_bd[ri]
    if xy is not None:
        err = math.hypot(xy[0] - dpx, xy[1] - dpy)
        print(f"  {name:<10} alt={alt:.1f} az={az:.1f}"
              f"  proj=({xy[0]:.1f},{xy[1]:.1f})"
              f"  det=({dpx:.1f},{dpy:.1f})  err={err:.2f}px")
    else:
        print(f"  {name:<10}  NOT PROJECTED")

print("\n--- Verification (not in cost) ---")
for vname, (valt, vaz) in [('Arcturus', arcturus_altaz)]:
    xy = model.altaz_to_pixel(valt, vaz)
    if xy is None:
        print(f"  {vname}: NOT PROJECTED (alt={valt:.1f}°)")
    else:
        print(f"  {vname:<10} alt={valt:.1f}° az={vaz:.1f}°  proj=({xy[0]:.1f}, {xy[1]:.1f})")

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

out_path = "sample_images/bigdipper_v5_calibration.json"
os.makedirs(os.path.dirname(out_path), exist_ok=True)

final_model = FisheyeModel(
    cx=cx, cy=cy,
    a1=a1, a3=a3, a5=a5,
    roll=roll,
    axis_alt=axis_alt,
    axis_az=axis_az,
    east_left=True,
    rms_residual=round(rms_px(p), 4),
    n_matches=len(exact) + len(bd),
    calibrated_at=datetime.now(timezone.utc).isoformat(),
)
final_model.save(out_path)
print(f"\nSaved → {out_path}")
print(f"RMS = {final_model.rms_residual:.2f} px over {final_model.n_matches} stars")
print(f"\nNext step — render overlay:")
print(f"  python scripts/allsky_debug.py sample_images/lum_20260116_021511.fits \\")
print(f"    --overlay-only --cal {out_path} \\")
print(f"    --lat {LAT} --lon {LON} --utc \"2026-01-16 08:15:11\" \\")
print(f"    --out sample_images/lum_20260116_021511_v5 --stars 20 --con-width 2")
