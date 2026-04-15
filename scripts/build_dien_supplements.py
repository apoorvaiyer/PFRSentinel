"""
One-time data-build script: query SIMBAD for Dien.json stars that the BSC5
resolver cannot match, and write results to star_data/dien_supplements.json.

Usage:
    python scripts/build_dien_supplements.py

Resume: if dien_supplements.json already exists, entries already present are
skipped so the script can be safely re-run after a partial failure.
"""
import json
import os
import re
import sys
import time
from typing import Dict, Optional

import requests

# ── add project root to sys.path so we can import from services ──────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.allsky.catalogs import (
    _load_bsc5,
    _parse_bsc5_ra,
    _parse_bsc5_dec,
    _resolve_dien_star,
    _star_data_path,
)

# ── SIMBAD query settings ─────────────────────────────────────────────────────

SIMBAD_URL = "https://simbad.cds.unistra.fr/simbad/sim-id"
RATE_LIMIT_S = 0.25   # SIMBAD policy: max ~6 req/s
TIMEOUT_S = 10

_COORD_RE = re.compile(
    r'Coordinates\(ICRS.*?\):\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)'
    r'\s+([+-]?\d+)\s+([\d.]+)\s+([\d.]+)'
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _query_simbad(abbrev: str, const: str) -> Optional[tuple]:
    """
    Query SIMBAD for a Bayer/Flamsteed designation.
    Returns (ra_deg, dec_deg) on success, None on failure or not-found.

    SIMBAD requires the '* ' prefix for Bayer/Flamsteed identifiers — without
    it, leading Latin letters are misinterpreted as catalog codes (e.g. 'c' →
    Cluster catalog), causing false "Object not found" responses.
    """
    ident = f"* {abbrev} {const}"
    try:
        resp = requests.get(
            SIMBAD_URL,
            params={'output.format': 'ASCII', 'Ident': ident},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        text = resp.text
    except Exception as exc:
        print(f"  WARNING: HTTP error for '{abbrev} {const}': {exc}")
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


def _build_maps() -> tuple:
    """Build star_map and flamsteed_map exactly as _load_dien does."""
    bsc5 = _load_bsc5()
    star_map: Dict[str, tuple] = {}
    for s in bsc5:
        abbrev = s.get('_bayer_abbrev')
        const = s.get('const', '')
        if abbrev and const:
            star_map[f"{abbrev} {const}"] = (s['ra_deg'], s['dec_deg'])

    flamsteed_map: Dict[str, tuple] = {}
    try:
        raw_path = _star_data_path('bsc5-short.json')
        with open(raw_path, encoding='utf-8') as f:
            raw_bsc5 = json.load(f)
        for star in raw_bsc5:
            f_num = star.get('F', '').strip()
            c_str = star.get('C', '').strip()
            if f_num and c_str:
                try:
                    ra = _parse_bsc5_ra(star['RA'])
                    dec = _parse_bsc5_dec(star['Dec'])
                    flamsteed_map[f"{f_num} {c_str}"] = (ra, dec)
                except Exception:
                    pass
    except Exception:
        pass

    return star_map, flamsteed_map


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    out_path = _star_data_path('dien_supplements.json')
    dien_path = _star_data_path('Dien.json')

    # Load existing supplements (resume support)
    supplements: Dict[str, list] = {}
    if os.path.exists(out_path):
        with open(out_path, encoding='utf-8') as f:
            supplements = json.load(f)
        print(f"Loaded {len(supplements)} existing entries from {out_path}")

    # Load Dien.json
    with open(dien_path, encoding='utf-8') as f:
        dien = json.load(f)

    # Build BSC5 lookup maps
    star_map, flamsteed_map = _build_maps()

    # Collect every unique ref that strategies 1–4 cannot resolve
    unresolved: set = set()
    for con in dien.get('constellations', []):
        for polyline in con.get('lines', []):
            for star_id in polyline:
                parts = star_id.split()
                if len(parts) >= 3 and parts[0] == '*':
                    abbrev, const = parts[1], parts[2]
                    if _resolve_dien_star(abbrev, const, star_map, flamsteed_map) is None:
                        key = f"{abbrev} {const}"
                        if key not in supplements:
                            unresolved.add((abbrev, const))

    print(f"\n{len(unresolved)} unresolved refs need SIMBAD queries "
          f"({len(supplements)} already in supplement file)")

    if not unresolved:
        print("Nothing to do.")
        return

    new_entries = 0
    failed = []
    total = len(unresolved)

    for i, (abbrev, const) in enumerate(sorted(unresolved), 1):
        key = f"{abbrev} {const}"
        print(f"[{i}/{total}] '{key}' ... ", end='', flush=True)

        result = _query_simbad(abbrev, const)
        time.sleep(RATE_LIMIT_S)

        if result is not None:
            ra, dec = result
            supplements[key] = [round(ra, 6), round(dec, 6)]
            new_entries += 1
            print(f"OK  RA={ra:.4f}  Dec={dec:.4f}")
        else:
            failed.append(key)
            print("not found")

    print(f"\nDone: {new_entries} new entries, {len(failed)} not found in SIMBAD.")
    if failed:
        print("Not found:", ', '.join(failed))

    if new_entries > 0:
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(supplements, f, indent=2, sort_keys=True)
        print(f"Written: {out_path}")
    else:
        print("No new entries — file not updated.")


if __name__ == '__main__':
    main()
