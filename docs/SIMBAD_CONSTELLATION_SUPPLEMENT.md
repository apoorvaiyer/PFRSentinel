# Task: Build SIMBAD Supplement for Unresolved Constellation Line Stars

## Goal

`services/allsky/catalogs.py` uses `Dien.json` to draw IAU constellation stick figures.
Each star in Dien.json is referenced by Bayer/Flamsteed designation (e.g. `* alf UMa`,
`* zet01 UMa`, `* 109 Her`). Stars are resolved against the BSC5 catalog. As of now,
~55 out of ~1447 references fail to resolve, leaving gaps in some constellation lines.

This task creates a one-time data-build script that:
1. Identifies every Dien.json star ref that the current resolver cannot match.
2. Queries the SIMBAD astronomical database for each, extracting RA/Dec.
3. Writes the results to `star_data/dien_supplements.json`.
4. Updates `catalogs.py` to use that file as a final fallback.

---

## Context: What the existing resolver already handles

`_resolve_dien_star(abbrev, const, star_map, flamsteed_map)` in `catalogs.py` tries
four strategies in order before giving up:

1. **Direct Bayer lookup** — `"alf UMa"` in `star_map` (built from BSC5).
2. **Add `01` suffix** — `"alf Cen"` → `"alf01 Cen"` (BSC5 stores α Cen as component).
3. **Strip trailing digits** — `"zet01 UMa"` → `"zet UMa"`.
4. **Flamsteed number fallback** — `"109 Her"` in `flamsteed_map`.

The supplement file is Strategy 5 — SIMBAD as ground truth for anything still unresolved.

---

## SIMBAD API details

Base URL (no auth required, free public API):
```
https://simbad.cds.unistra.fr/simbad/sim-id?output.format=ASCII&Ident=<encoded_name>
```

Name encoding: replace spaces with `+`, e.g. `alf+UMa`.

Dien.json uses SIMBAD-style Bayer abbreviations already:
- `alf`, `bet`, `gam`, `del`, `eps`, `zet`, `eta`, `tet`, `iot`, `kap`
- `lam`, `mu.`, `nu.`, `ksi`, `omi`, `pi.`, `rho`, `sig`, `tau`, `ups`
- `phi`, `chi`, `psi`, `ome`

So the Dien.json entry `* bet Aur` maps to SIMBAD identifier `bet Aur`.

### Example query
```
GET https://simbad.cds.unistra.fr/simbad/sim-id?output.format=ASCII&Ident=bet+Aur
```

The ASCII response contains a line beginning with `Coordinates(ICRS...` like:
```
Coordinates(ICRS,ep=J2000,eq=2000): 05 59 31.72328  +44 56 50.7573
```
Parse this with regex:
```python
import re
m = re.search(
    r'Coordinates\(ICRS.*?\):\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([+-]?\d+)\s+([\d.]+)\s+([\d.]+)',
    response_text
)
if m:
    ra_h, ra_m, ra_s = float(m.group(1)), float(m.group(2)), float(m.group(3))
    dec_d, dec_m, dec_s = float(m.group(4)), float(m.group(5)), float(m.group(6))
    ra_deg = (ra_h + ra_m/60 + ra_s/3600) * 15.0
    sign = -1 if '-' in m.group(4) else 1
    dec_deg = sign * (abs(dec_d) + dec_m/60 + dec_s/3600)
```

If SIMBAD returns `Object not found`, skip that entry.

---

## Script to write: `scripts/build_dien_supplements.py`

### What the script does

```
python scripts/build_dien_supplements.py
```

1. Load `star_data/Dien.json`.
2. For each constellation's lines, collect all star refs `(abbrev, const)`.
3. Build `star_map` and `flamsteed_map` exactly as `_load_dien` does.
4. Run `_resolve_dien_star` for each ref using the existing 4 strategies.
5. For every ref that returns `None`, query SIMBAD.
6. Rate-limit: sleep 0.25s between requests (SIMBAD policy: max ~6 req/s).
7. Print a progress line for each lookup (resolved / failed / skipped).
8. On success, write `star_data/dien_supplements.json`:

```json
{
  "alf Crt": [164.943, -18.299],
  "bet Ara": [261.328, -55.531],
  ...
}
```

Keys are `"<abbrev> <const>"` strings (exact format used by `_resolve_dien_star`).
Values are `[ra_deg, dec_deg]` floats.

### Error handling
- HTTP errors or timeouts → log warning, skip that star (don't abort the run).
- If the supplements file already exists, load it and skip stars already present (resume).
- Only write if at least one new entry was fetched.

---

## Change to `catalogs.py`

After writing the script and generating `star_data/dien_supplements.json`,
add Strategy 5 to `_resolve_dien_star`:

```python
def _resolve_dien_star(abbrev, const, star_map, flamsteed_map, supplements=None):
    key = f"{abbrev} {const}"

    # Strategies 1–4 (unchanged) ...

    # Strategy 5: SIMBAD supplement lookup
    if supplements and key in supplements:
        ra, dec = supplements[key]
        return (ra, dec)

    return None
```

Load supplements once in `_load_dien`:
```python
supplements = {}
try:
    sup_path = _star_data_path('dien_supplements.json')
    with open(sup_path, encoding='utf-8') as f:
        supplements = json.load(f)
except Exception:
    pass
```

Pass `supplements` through to each `_resolve_dien_star` call.

---

## Files to create / modify

| File | Action |
|------|--------|
| `scripts/build_dien_supplements.py` | **Create** — one-time data-build script |
| `star_data/dien_supplements.json` | **Generated** by running the script |
| `services/allsky/catalogs.py` | **Modify** — add Strategy 5 supplement lookup |

The supplement JSON is committed to the repo so end users don't need internet access.

---

## Expected outcome

- ~55 currently-unresolved stars resolved → miss rate drops from ~3.8% to near 0%.
- Constellation lines for Crater, Ara, Monoceros, and others with gaps become complete.
- Zero runtime network calls — all data baked into the supplement file at build time.
