"""
Catalog loader for All-Sky overlays.

Loads from star_data/ at module level (cached). All catalog data is read-only.
Sources: BSC5 (bsc5-short.json), Messier (messier_list.json),
         OpenNGC (NGC.csv),
         Western IAU constellation lines (constellations.json — includes hip_coords).
"""
import json
import csv
import re
import os
import numpy as np
from typing import List, Dict, Optional

from services.logger import app_logger as log

# ---------------------------------------------------------------------------
# Resource path helper
# ---------------------------------------------------------------------------

def _star_data_path(filename: str) -> str:
    """Return absolute path to a file in star_data/, works for dev and PyInstaller."""
    try:
        from services.utils_paths import resource_path
        return resource_path(os.path.join('star_data', filename))
    except Exception:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base, 'star_data', filename)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_bsc5_ra(ra_str: str) -> float:
    """Parse '00h 05m 09.9s' → decimal degrees."""
    m = re.match(r'(\d+)h\s+([\d.]+)m\s+([\d.]+)s', ra_str.strip())
    if not m:
        raise ValueError(f"Cannot parse BSC5 RA: {ra_str!r}")
    h, mn, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
    return (h + mn / 60.0 + s / 3600.0) * 15.0


def _parse_bsc5_dec(dec_str: str) -> float:
    """Parse '+45° 13′ 45″' → decimal degrees."""
    sign = -1.0 if dec_str.strip().startswith('-') else 1.0
    m = re.match(r'[+-]?(\d+)[°\u00b0]\s*(\d+)[′\u2032\']\s*([\d.]+)[″\u2033"]',
                 dec_str.strip())
    if not m:
        raise ValueError(f"Cannot parse BSC5 Dec: {dec_str!r}")
    d, mn, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
    return sign * (d + mn / 60.0 + s / 3600.0)


def _parse_messier_ra(ra_str: str) -> float:
    """Parse '5h 34.5m' → decimal degrees."""
    m = re.match(r'(\d+)h\s+([\d.]+)m', ra_str.strip())
    if not m:
        raise ValueError(f"Cannot parse Messier RA: {ra_str!r}")
    return (float(m.group(1)) + float(m.group(2)) / 60.0) * 15.0


def _parse_messier_dec(dec_str: str) -> float:
    """Parse '+22° 01′' → decimal degrees."""
    sign = -1.0 if dec_str.strip().startswith('-') else 1.0
    m = re.match(r'[+-]?(\d+)[°\u00b0]\s*(\d+)[′\u2032\']', dec_str.strip())
    if not m:
        raise ValueError(f"Cannot parse Messier Dec: {dec_str!r}")
    return sign * (float(m.group(1)) + float(m.group(2)) / 60.0)


def _parse_ngc_ra(ra_str: str) -> float:
    """Parse 'hh:mm:ss.ss' → decimal degrees."""
    h, mn, s = ra_str.strip().split(':')
    return (float(h) + float(mn) / 60.0 + float(s) / 3600.0) * 15.0


def _parse_ngc_dec(dec_str: str) -> float:
    """Parse '±dd:mm:ss.s' → decimal degrees."""
    sign = -1.0 if dec_str.strip().startswith('-') else 1.0
    parts = dec_str.strip().lstrip('+-').split(':')
    d, mn, s = float(parts[0]), float(parts[1]), float(parts[2])
    return sign * (d + mn / 60.0 + s / 3600.0)


# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------
_bright_stars_cache: Optional[List[Dict]] = None
_western_lines_cache: Optional[List[tuple]] = None
_western_labels_cache: Optional[List[Dict]] = None
_messier_cache: Optional[List[Dict]] = None
_ngc_cache: Optional[List[Dict]] = None


def get_bright_stars(max_mag: float = 4.5) -> List[Dict]:
    """Return BSC5 stars up to max_mag. Cached after first load."""
    global _bright_stars_cache
    if _bright_stars_cache is None:
        _bright_stars_cache = _load_bsc5()
    return [s for s in _bright_stars_cache if s['vmag'] <= max_mag]


def get_all_bsc5_stars() -> List[Dict]:
    """Return all BSC5 stars (for calibration star matching)."""
    global _bright_stars_cache
    if _bright_stars_cache is None:
        _bright_stars_cache = _load_bsc5()
    return _bright_stars_cache


def get_western_constellation_lines() -> List[tuple]:
    """Return (ra1, dec1, ra2, dec2) tuples for Western IAU constellation lines."""
    global _western_lines_cache, _western_labels_cache
    if _western_lines_cache is None:
        _western_lines_cache, _western_labels_cache = _load_western_constellations()
    return _western_lines_cache


def get_western_constellation_labels() -> List[Dict]:
    """Return Western constellation centroids: [{'name', 'abbrev', 'ra_deg', 'dec_deg'}]."""
    global _western_lines_cache, _western_labels_cache
    if _western_labels_cache is None:
        _western_lines_cache, _western_labels_cache = _load_western_constellations()
    return _western_labels_cache


def get_messier_objects() -> List[Dict]:
    """Return all 110 Messier objects. Cached after first load."""
    global _messier_cache
    if _messier_cache is None:
        _messier_cache = _load_messier()
    return _messier_cache


def get_ngc_objects(max_mag: float = 10.0) -> List[Dict]:
    """Return NGC/IC objects up to max_mag. Cached after first load."""
    global _ngc_cache
    if _ngc_cache is None:
        _ngc_cache = _load_ngc()
    return [o for o in _ngc_cache if o.get('vmag') is not None and o['vmag'] <= max_mag]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_bsc5() -> List[Dict]:
    path = _star_data_path('bsc5-short.json')
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    stars = []
    for s in raw:
        try:
            ra = _parse_bsc5_ra(s['RA'])
            dec = _parse_bsc5_dec(s['Dec'])
            vmag = float(s['V'])
        except Exception:
            continue
        stars.append({
            'ra_deg': ra,
            'dec_deg': dec,
            'vmag': vmag,
            'hr': s.get('HR', ''),
            'name': s.get('N', ''),
            'bayer': s.get('B', ''),
            'const': s.get('C', ''),
            'color_k': int(s['K']) if s.get('K', '').isdigit() else 6000,
        })
    stars.sort(key=lambda x: x['vmag'])
    return stars


def _load_messier() -> List[Dict]:
    path = _star_data_path('messier_list.json')
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    objects = []
    for obj in raw:
        try:
            ra = _parse_messier_ra(obj['RA'])
            dec = _parse_messier_dec(obj['Dec'])
            vmag = float(obj.get('V', '99') or '99')
        except Exception:
            continue
        objects.append({
            'ra_deg': ra,
            'dec_deg': dec,
            'vmag': vmag,
            'id': obj.get('M', ''),
            'label': obj.get('M', ''),
            'name': obj.get('N', ''),
            'type': obj.get('T', ''),
            'ngc': obj.get('NGC', ''),
        })
    return objects


def _load_ngc() -> List[Dict]:
    path = _star_data_path('NGC.csv')
    objects = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            ra_s = (row.get('RA') or '').strip()
            dec_s = (row.get('Dec') or '').strip()
            if not ra_s or not dec_s:
                continue
            try:
                ra = _parse_ngc_ra(ra_s)
                dec = _parse_ngc_dec(dec_s)
            except Exception:
                continue
            vmag_s = (row.get('V-Mag') or '').strip()
            vmag = float(vmag_s) if vmag_s else None
            messier_n = (row.get('M') or '').strip()
            objects.append({
                'ra_deg': ra,
                'dec_deg': dec,
                'vmag': vmag,
                'id': (row.get('Name') or '').strip(),
                'label': (row.get('Name') or '').strip(),
                'name': (row.get('Common names') or '').split('|')[0].strip(),
                'type': (row.get('Type') or '').strip(),
                'messier': messier_n,
            })
    return objects


def _load_western_constellations() -> tuple:
    """
    Load Western IAU constellation lines from constellations.json using HIP IDs.
    HIP coordinates are embedded in the same file under the 'hip_coords' key.
    lines_list: [(ra1, dec1, ra2, dec2, iau, thin), ...]  thin=True for secondary body lines
    labels_list: [{'name', 'abbrev', 'ra_deg', 'dec_deg'}, ...]
    """
    path = _star_data_path('constellations.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    # HIP → (ra, dec) lookup is embedded in the same file
    hip_coords: Dict[int, tuple] = {}
    for k, v in data.get('hip_coords', {}).items():
        hip_coords[int(k)] = (v[0], v[1])

    lines: List[tuple] = []
    labels: List[Dict] = []
    n_resolved = 0
    n_missing = 0

    for con in data.get('constellations', []):
        iau = con.get('iau', '')
        name = con.get('common_name', {}).get('english', iau)
        all_ra, all_dec = [], []

        for polyline in con.get('lines', []):
            # First element may be a style hint string ('thin'); remainder are HIP ints
            thin = isinstance(polyline[0], str) and polyline[0] == 'thin' if polyline else False
            hip_ids = [h for h in polyline if isinstance(h, int)]
            resolved = []
            for hip in hip_ids:
                coords = hip_coords.get(hip)
                if coords:
                    resolved.append(coords)
                    all_ra.append(coords[0])
                    all_dec.append(coords[1])
                    n_resolved += 1
                else:
                    n_missing += 1

            for i in range(len(resolved) - 1):
                ra1, dec1 = resolved[i]
                ra2, dec2 = resolved[i + 1]
                lines.append((ra1, dec1, ra2, dec2, iau, thin))

        if all_ra and iau:
            labels.append({
                'name': name,
                'abbrev': iau,
                'ra_deg': float(np.mean(all_ra)),
                'dec_deg': float(np.mean(all_dec)),
            })

    log.debug(f"Western constellation lines: {len(lines)} segments, "
              f"{n_resolved} resolved / {n_missing} unresolved HIP refs")
    return lines, labels
