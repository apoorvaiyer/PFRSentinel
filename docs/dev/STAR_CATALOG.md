# Star Catalog — Data Sources & Usage Guide

This document describes the static star data files in `star_data/` and the Skyfield library for
planet calculations, explaining what each contains, how to parse it, and how to combine them into
a working star catalog.

---

## Overview

| File | Source | Contents | Objects |
|------|--------|----------|---------|
| `bsc5-short.json` | Yale Bright Star Catalog 5th ed. | All naked-eye stars | ~9,100 stars (V ≤ 6.5) |
| `messier_list.json` | Messier Catalog | Famous DSOs cross-referenced to NGC | 110 objects |
| `NGC.csv` | OpenNGC + addendum | Full NGC/IC deep-sky catalog + supplementary DSOs | ~14,000 objects |
| `constellations.json` | Stellarium (Western IAU) | Constellation lines + embedded HIP coords | 88 IAU constellations |
| *(calculated)* | Skyfield | Planet positions | 8 planets + Moon + Sun |

**Licenses**: OpenNGC (CC BY-SA 4.0), constellation data (CC BY-SA).
The BSC5 is public domain. Skyfield is MIT.

---

## File Reference

### `bsc5-short.json` — Yale Bright Star Catalog

**Source**: https://github.com/brettonw/YaleBrightStarCatalog

A JSON array of ~9,100 objects. Each entry is a star from BSC5 — the definitive
catalog of all stars visible to the naked eye (visual magnitude ≤ 6.5).

**Fields**:

| Key | Type | Always present | Description |
|-----|------|---------------|-------------|
| `HR` | string | Yes | Harvard Revised catalog number (unique star ID) |
| `RA` | string | Yes | Right Ascension — `"00h 05m 09.9s"` |
| `Dec` | string | Yes | Declination — `"+45° 13′ 45″"` |
| `V` | string | Yes | Visual magnitude (float as string) |
| `K` | string | Yes | Approximate color temperature in Kelvin |
| `C` | string | No | IAU constellation abbreviation (e.g. `"And"`) |
| `F` | string | No | Flamsteed number (e.g. `"21"` for 21 Andromedae) |
| `B` | string | No | Bayer Greek letter (e.g. `"α"`, `"β"`, `"γ²"`) |
| `N` | string | No | Proper name (e.g. `"Alpheratz"`, `"Sirius"`) |

**Parsing RA/Dec**: The strings use Unicode symbols and must be parsed before any math.

```python
import re

def parse_ra(ra_str):
    """Parse '00h 05m 09.9s' → decimal degrees."""
    h, m, s = re.match(r"(\d+)h\s+(\d+)m\s+([\d.]+)s", ra_str).groups()
    return (float(h) + float(m) / 60 + float(s) / 3600) * 15

def parse_dec(dec_str):
    """Parse '+45° 13′ 45″' → decimal degrees."""
    sign = -1 if dec_str.startswith("-") else 1
    d, m, s = re.match(r"[+-]?(\d+)[°]\s*(\d+)[′']\s*([\d.]+)[″\"]", dec_str).groups()
    return sign * (float(d) + float(m) / 60 + float(s) / 3600)
```

**Color from K**: The `K` field is the blackbody temperature. Map it to an approximate
RGB display color:

| Temp (K) | Color | Stars |
|----------|-------|-------|
| ≤ 3500 | Deep orange-red | M-type giants |
| 4000–5000 | Orange-yellow | K-type |
| 5500–6500 | Yellow-white | G-type (Sun-like) |
| 7000–9000 | White | A-type |
| 10000–15000 | Blue-white | B-type |
| ≥ 20000 | Deep blue | O/B supergiants |

**Usage example**:

```python
import json

with open("star_data/bsc5-short.json") as f:
    stars = json.load(f)

# Bright named stars only
named = [s for s in stars if "N" in s and float(s["V"]) < 3.0]

# All stars in Orion constellation
orion = [s for s in stars if s.get("C") == "Ori"]
```

---

### `messier_list.json` — Messier Catalog

**Source**: https://github.com/brettonw/YaleBrightStarCatalog

A JSON array of 110 Messier objects. Useful as a curated list of the most
recognisable deep-sky objects for overlay labels and planning tools.

**Fields**:

| Key | Type | Always present | Description |
|-----|------|---------------|-------------|
| `M` | string | Yes | Messier identifier — `"M1"` through `"M110"` |
| `T` | string | Yes | Object type (see type codes below) |
| `V` | string | Yes | Visual magnitude |
| `S` | string | Yes | Angular size in arcminutes (may be `"WxH"` for non-circular) |
| `RA` | string | Yes | Right Ascension — `"5h 34.5m"` (hours + decimal minutes) |
| `Dec` | string | Yes | Declination — `"+22° 01′"` |
| `Con` | string | Yes | IAU constellation abbreviation |
| `NGC` | string | No | NGC or IC number cross-reference |
| `N` | string | No | Common name (e.g. `"Crab Nebula"`, `"Andromeda Galaxy"`) |

**Object type codes**:

| Code | Type | Code | Type |
|------|------|------|------|
| `GC` | Globular Cluster | `OC` | Open Cluster |
| `DN` | Diffuse Nebula | `PN` | Planetary Nebula |
| `SG` | Spiral Galaxy | `EG` | Elliptical Galaxy |
| `BG` | Barred Spiral Galaxy | `SN` | Supernova Remnant |
| `MW` | Milky Way star cloud | `DS` | Double Star |

**Parsing RA/Dec**: RA is `"Xh YY.Ym"` (decimal minutes), Dec is `"±DD° MM′"`.

```python
def parse_messier_ra(ra_str):
    """Parse '5h 34.5m' → decimal degrees."""
    h, m = re.match(r"(\d+)h\s+([\d.]+)m", ra_str).groups()
    return (float(h) + float(m) / 60) * 15

def parse_messier_dec(dec_str):
    """Parse '+22° 01′' → decimal degrees."""
    sign = -1 if dec_str.startswith("-") else 1
    d, m = re.match(r"[+-]?(\d+)[°]\s*(\d+)[′']", dec_str).groups()
    return sign * (float(d) + float(m) / 60)
```

---

### `NGC.csv` — OpenNGC Database

**Source**: https://github.com/mattiaverga/OpenNGC
**License**: CC BY-SA 4.0

A single semicolon-delimited CSV file containing the full New General Catalogue, Index
Catalogue, and supplementary objects (Barnard dark nebulae, Caldwell catalog, ESO objects,
well-known named objects such as the LMC and Horsehead Nebula).

**Key fields**:

| Column | Description |
|--------|-------------|
| `Name` | Primary identifier (e.g. `NGC0224`, `IC0001`, `B033`) |
| `Type` | Object type (see below) |
| `RA` | Right Ascension — `"hh:mm:ss.ss"` |
| `Dec` | Declination — `"±dd:mm:ss.s"` |
| `Const` | IAU constellation abbreviation |
| `MajAx` | Major axis in arcminutes |
| `MinAx` | Minor axis in arcminutes |
| `PosAng` | Position angle in degrees (east of north) |
| `B-Mag` | Blue magnitude |
| `V-Mag` | Visual magnitude |
| `J-Mag`, `H-Mag`, `K-Mag` | Near-infrared magnitudes |
| `SurfBr` | Surface brightness (mag/arcsec²) |
| `Hubble` | Hubble morphological type (for galaxies) |
| `Pax` | Parallax (mas) — stars only |
| `Pm-RA`, `Pm-Dec` | Proper motion (mas/yr) |
| `RadVel` | Radial velocity (km/s) |
| `Redshift` | Cosmological redshift |
| `Cstar V-Mag` | Central star visual magnitude (planetary nebulae) |
| `M` | Messier cross-reference number |
| `NGC` | NGC cross-reference (for IC entries) |
| `IC` | IC cross-reference (for NGC entries) |
| `Common names` | Pipe-delimited popular names |
| `Identifiers` | Pipe-delimited alternate catalog designations |

**OpenNGC type codes** (superset of Messier codes above):

| Code | Type | Code | Type |
|------|------|------|------|
| `G` | Galaxy (generic) | `GC` | Globular Cluster |
| `OC` | Open Cluster | `*Ass` | Stellar Association |
| `Cl+N` | Cluster with Nebulosity | `PN` | Planetary Nebula |
| `HII` | HII Region | `EN` | Emission Nebula |
| `RN` | Reflection Nebula | `DrkN` | Dark Nebula |
| `SNR` | Supernova Remnant | `Nova` | Nova |
| `*` | Star | `**` | Double Star |
| `D*` | Double Star (old code) | `NotFound` | Not found / non-existent |

**Parsing**:

```python
import csv

def parse_ngc_ra(ra_str):
    """Parse 'hh:mm:ss.ss' → decimal degrees."""
    h, m, s = ra_str.split(":")
    return (float(h) + float(m) / 60 + float(s) / 3600) * 15

def parse_ngc_dec(dec_str):
    """Parse '±dd:mm:ss.s' → decimal degrees."""
    sign = -1 if dec_str.startswith("-") else 1
    parts = dec_str.lstrip("+-").split(":")
    d, m, s = parts
    return sign * (float(d) + float(m) / 60 + float(s) / 3600)

def load_ngc(path):
    objects = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if row["RA"] and row["Dec"]:
                row["ra_deg"] = parse_ngc_ra(row["RA"])
                row["dec_deg"] = parse_ngc_dec(row["Dec"])
            objects.append(row)
    return objects

all_dso = load_ngc("star_data/NGC.csv")
```

**Finding a specific object**:

```python
# By NGC number
m31 = next(o for o in all_dso if o["Name"] == "NGC0224")

# By Messier number
m42 = next(o for o in all_dso if o["M"] == "42")

# By common name (case-insensitive substring)
horsehead = next(o for o in all_dso if "Horsehead" in o.get("Common names", ""))

# All galaxies brighter than mag 10
bright_galaxies = [
    o for o in all_dso
    if o["Type"] in ("G", "SG", "EG", "BG", "IG")
    and o.get("V-Mag") and float(o["V-Mag"]) < 10.0
]
```

---

## Planet Calculations — Skyfield

**Source**: https://rhodesmill.org/skyfield/
**Install**: `pip install skyfield`

Planets are not stored statically — their positions must be computed for a given
observer location, date, and time. Skyfield handles this using JPL ephemeris data.

**Quick start**:

```python
from skyfield.api import load, wgs84
from datetime import datetime, timezone

# Load ephemeris (downloads once to ~/.skyfield/ or a local cache)
planets = load("de421.bsp")   # ~17 MB download; covers 1900–2050
ts = load.timescale()

# Observer location (latitude, longitude, elevation in metres)
observer = wgs84.latlon(51.5, -1.8, elevation_m=100)

# Current UTC time
now = ts.from_datetime(datetime.now(timezone.utc))

# Get apparent planet position
earth = planets["earth"]
mars = planets["mars"]
astrometric = (earth + observer).at(now).observe(mars)
alt, az, distance = astrometric.apparent().altaz()

print(f"Mars: alt={alt.degrees:.2f}°  az={az.degrees:.2f}°  dist={distance.au:.3f} AU")
```

**Available bodies in `de421.bsp`**:

```python
PLANETS = [
    "sun", "moon",
    "mercury", "venus", "mars",
    "jupiter barycenter", "saturn barycenter",
    "uranus barycenter", "neptune barycenter",
]
```

**Converting to RA/Dec** (for catalog-style display):

```python
from skyfield.api import load

planets = load("de421.bsp")
ts = load.timescale()

t = ts.now()
astrometric = planets["earth"].at(t).observe(planets["mars"])
ra, dec, distance = astrometric.radec()

print(f"Mars RA: {ra}  Dec: {dec}")
# Output: Mars RA: 14h 21m 33.45s  Dec: -14deg 07' 11.2"
```

**Caching**: Ephemeris loading is slow (~1s). Instantiate `planets` and `ts` once at
application startup and reuse. Planet positions only need recalculating when the
display time changes.

---

## Combining the Catalogs

A suggested loading strategy for a star chart or overlay feature:

```python
import json
import csv

def load_all():
    # 1. Bright stars (for plotting and calibration)
    with open("star_data/bsc5-short.json") as f:
        stars = json.load(f)

    # 2. Messier objects (curated DSO labels)
    with open("star_data/messier_list.json") as f:
        messier = json.load(f)

    # 3. Full NGC/IC catalog (includes addendum objects)
    def load_csv(path):
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f, delimiter=";"))

    dso = load_csv("star_data/NGC.csv")

    # 4. Constellation lines (HIP coords embedded in same file)
    with open("star_data/constellations.json") as f:
        constellations = json.load(f)

    return {
        "stars": stars,
        "messier": messier,
        "dso": dso,
        "constellations": constellations,
    }
```

**Recommended display priority / magnitude cutoffs**:

| Layer | Source | Filter |
|-------|--------|--------|
| Background stars | BSC5 | V ≤ 6.5 (all entries) |
| Bright star labels | BSC5 | `N` field present (proper name) |
| Constellation lines | constellations.json | All IAU constellations |
| Famous DSO labels | messier_list.json | V ≤ 9.0 or named |
| Extended DSO catalog | NGC.csv | V ≤ 12.0 (per use-case) |
| Planets | Skyfield | Always visible if above horizon |

---

## Field-of-View Filtering

For overlay or chart use, filter objects to only those within the camera's FOV:

```python
import math

def objects_in_fov(catalog, center_ra, center_dec, fov_width_deg, fov_height_deg):
    """
    Return objects within a rectangular FOV.
    catalog: list of dicts with 'ra_deg' and 'dec_deg' float fields.
    """
    results = []
    half_w = fov_width_deg / 2
    half_h = fov_height_deg / 2
    cos_dec = math.cos(math.radians(center_dec))

    for obj in catalog:
        ra = obj.get("ra_deg")
        dec = obj.get("dec_deg")
        if ra is None or dec is None:
            continue
        d_dec = abs(dec - center_dec)
        d_ra = abs(ra - center_ra)
        if d_ra > 180:
            d_ra = 360 - d_ra
        d_ra_corrected = d_ra * cos_dec
        if d_dec <= half_h and d_ra_corrected <= half_w:
            results.append(obj)
    return results
```

---

## Notes

- All static files are read-only reference data — do not write to them.
- The Skyfield ephemeris file (`de421.bsp`) should be stored outside `star_data/` (e.g. in
  `%APPDATA%\PFRSentinel\`) so it is not bundled into the installer unnecessarily, and
  downloaded on first use.
- When matching Messier objects to NGC records, use the `M` column in NGC.csv as the
  join key (stored as an integer string, e.g. `"42"`).
- NGC.csv includes addendum objects with non-NGC prefixes in the `Name` column:
  `B` (Barnard), `C` (Caldwell), `Cl` (cluster designations), `ESO`, etc.
