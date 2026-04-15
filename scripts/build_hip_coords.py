"""
One-time data-build script: query SIMBAD for every HIP (Hipparcos) star number
referenced in star_data/constellations.json and write the results to
star_data/hip_coords.json.

Usage:
    python scripts/build_hip_coords.py

Resume: if hip_coords.json already exists, HIP numbers already present are
skipped so the script can be safely re-run after a partial failure.

Output format:
    {"677": [ra_deg, dec_deg], "3092": [ra_deg, dec_deg], ...}

Keys are string HIP numbers (JSON only supports string keys).
Values are [ra_deg, dec_deg] as floats rounded to 6 decimal places.
"""
import json
import os
import re
import sys
import time
from typing import Dict, Optional

import requests

# ── add project root to sys.path ──────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.allsky.catalogs import _star_data_path

# ── SIMBAD query settings ─────────────────────────────────────────────────────

SIMBAD_URL = "https://simbad.cds.unistra.fr/simbad/sim-id"
RATE_LIMIT_S = 0.25   # SIMBAD policy: max ~6 req/s
TIMEOUT_S = 10

_COORD_RE = re.compile(
    r'Coordinates\(ICRS.*?\):\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)'
    r'\s+([+-]?\d+)\s+([\d.]+)\s+([\d.]+)'
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _query_simbad(hip: int) -> Optional[tuple]:
    """
    Query SIMBAD for a HIP number.  Returns (ra_deg, dec_deg) or None.
    HIP identifiers use the catalog-code form 'HIP NNNNN' — no star prefix needed.
    """
    ident = f"HIP {hip}"
    try:
        resp = requests.get(
            SIMBAD_URL,
            params={'output.format': 'ASCII', 'Ident': ident},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        text = resp.text
    except Exception as exc:
        print(f"  WARNING: HTTP error for HIP {hip}: {exc}")
        return None

    if 'Object not found' in text:
        return None

    m = _COORD_RE.search(text)
    if not m:
        return None

    ra_h = float(m.group(1))
    ra_m = float(m.group(2))
    ra_s = float(m.group(3))
    dec_raw = m.group(4)
    dec_d = float(dec_raw)
    dec_m = float(m.group(5))
    dec_s = float(m.group(6))

    ra_deg = (ra_h + ra_m / 60.0 + ra_s / 3600.0) * 15.0
    sign = -1 if '-' in dec_raw else 1
    dec_deg = sign * (abs(dec_d) + dec_m / 60.0 + dec_s / 3600.0)
    return (ra_deg, dec_deg)


def _collect_hips(data: dict) -> set:
    """Extract all unique HIP numbers from constellations.json."""
    hips: set = set()
    for con in data.get('constellations', []):
        for line in con.get('lines', []):
            # Lines may start with a style hint string ('thin') — skip those
            hips.update(h for h in line if isinstance(h, int))
        for anchor in con.get('image', {}).get('anchors', []):
            hips.add(int(anchor['hip']))
    return hips


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    out_path = _star_data_path('hip_coords.json')
    src_path = _star_data_path('constellations.json')

    # Load existing output (resume support)
    coords: Dict[str, list] = {}
    if os.path.exists(out_path):
        with open(out_path, encoding='utf-8') as f:
            coords = json.load(f)
        print(f"Loaded {len(coords)} existing entries from {out_path}")

    # Load constellations.json
    with open(src_path, encoding='utf-8') as f:
        data = json.load(f)

    all_hips = _collect_hips(data)
    needed = sorted(h for h in all_hips if str(h) not in coords)

    print(f"\n{len(all_hips)} unique HIP numbers in constellations.json")
    print(f"{len(coords)} already fetched, {len(needed)} to query")
    print(f"Estimated time: {len(needed) * RATE_LIMIT_S:.0f}s")

    if not needed:
        print("Nothing to do.")
        return

    new_entries = 0
    failed = []
    total = len(needed)

    for i, hip in enumerate(needed, 1):
        print(f"[{i}/{total}] HIP {hip} ... ", end='', flush=True)

        result = _query_simbad(hip)
        time.sleep(RATE_LIMIT_S)

        if result is not None:
            ra, dec = result
            coords[str(hip)] = [round(ra, 6), round(dec, 6)]
            new_entries += 1
            print(f"OK  RA={ra:.4f}  Dec={dec:.4f}")
        else:
            failed.append(hip)
            print("not found")

    print(f"\nDone: {new_entries} new entries, {len(failed)} not found.")
    if failed:
        print(f"Not found: {failed}")

    if new_entries > 0:
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(coords, f, indent=2, sort_keys=True)
        print(f"Written: {out_path}")
    else:
        print("No new entries — file not updated.")


if __name__ == '__main__':
    main()
