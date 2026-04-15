"""
Pure-numpy celestial coordinate transforms for PFR Sentinel All-Sky overlays.

Implements:
  - Julian Date conversion
  - Greenwich / Local Mean Sidereal Time
  - RA/Dec (J2000) ↔ topocentric AltAz
  - Atmospheric refraction (Bennett 1982)

All formulae from Meeus "Astronomical Algorithms" 2nd ed.
No external astronomy libraries required.
"""
import numpy as np
from datetime import datetime, timezone
from typing import Union

# Type alias for scalar-or-array inputs
Numeric = Union[float, np.ndarray]


# ---------------------------------------------------------------------------
# Julian Date
# ---------------------------------------------------------------------------

def julian_date(dt: datetime) -> float:
    """Convert a datetime (UTC) to Julian Date."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    y, m = dt.year, dt.month
    d = (dt.day
         + dt.hour / 24.0
         + dt.minute / 1440.0
         + dt.second / 86400.0
         + dt.microsecond / 86400e6)
    if m <= 2:
        y -= 1
        m += 12
    A = int(y / 100)
    B = 2 - A + int(A / 4)
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + B - 1524.5


# ---------------------------------------------------------------------------
# Sidereal Time
# ---------------------------------------------------------------------------

def gmst_degrees(jd: float) -> float:
    """Greenwich Mean Sidereal Time in degrees (Meeus Eq. 12.4)."""
    T = (jd - 2451545.0) / 36525.0
    theta = (280.46061837
             + 360.98564736629 * (jd - 2451545.0)
             + 0.000387933 * T * T
             - T ** 3 / 38710000.0)
    return theta % 360.0


def lst_degrees(jd: float, lon_deg: float) -> float:
    """Local Sidereal Time in degrees (east-positive longitude)."""
    return (gmst_degrees(jd) + lon_deg) % 360.0


# ---------------------------------------------------------------------------
# Core AltAz ↔ RA/Dec  (all-array, all-radian internals)
# ---------------------------------------------------------------------------

def _ha_dec_to_altaz(ha_r: Numeric, dec_r: Numeric, lat_r: float):
    """Return (alt_rad, az_rad) from hour-angle and declination (radians)."""
    sin_alt = (np.sin(dec_r) * np.sin(lat_r)
               + np.cos(dec_r) * np.cos(lat_r) * np.cos(ha_r))
    alt_r = np.arcsin(np.clip(sin_alt, -1.0, 1.0))

    # Azimuth — North=0, East=90 convention
    cos_az_num = np.sin(dec_r) - np.sin(alt_r) * np.sin(lat_r)
    cos_az_den = np.cos(alt_r) * np.cos(lat_r)
    safe_den = np.where(np.abs(cos_az_den) < 1e-10,
                        np.sign(cos_az_den) * 1e-10, cos_az_den)
    cos_az = np.clip(cos_az_num / safe_den, -1.0, 1.0)
    az_r = np.arccos(cos_az)
    # Quadrant fix: if hour angle is positive (star moving westward), az > 180
    az_r = np.where(np.sin(ha_r) > 0, 2.0 * np.pi - az_r, az_r)
    return alt_r, az_r


def radec_to_altaz(
    ra_deg: Numeric,
    dec_deg: Numeric,
    lat_deg: float,
    lon_deg: float,
    dt: datetime,
    refraction: bool = True,
) -> tuple:
    """
    Convert RA/Dec (J2000, degrees) to topocentric AltAz (degrees).

    Inputs may be scalar floats or numpy arrays.
    Returns (alt_deg, az_deg) arrays.
    """
    ra = np.asarray(ra_deg, dtype=float)
    dec = np.asarray(dec_deg, dtype=float)
    jd = julian_date(dt)
    lsa = lst_degrees(jd, lon_deg)
    ha_deg = (lsa - ra) % 360.0

    alt_r, az_r = _ha_dec_to_altaz(
        np.radians(ha_deg), np.radians(dec), np.radians(lat_deg)
    )
    alt_d = np.degrees(alt_r)
    az_d = np.degrees(az_r)

    if refraction:
        alt_d = alt_d + atmospheric_refraction(alt_d)

    return alt_d, az_d


def altaz_to_radec(
    alt_deg: Numeric,
    az_deg: Numeric,
    lat_deg: float,
    lon_deg: float,
    dt: datetime,
    refraction: bool = True,
) -> tuple:
    """
    Convert topocentric AltAz (degrees) to RA/Dec (J2000, degrees).
    Returns (ra_deg, dec_deg).
    """
    alt = np.asarray(alt_deg, dtype=float)
    az = np.asarray(az_deg, dtype=float)
    if refraction:
        alt = alt - atmospheric_refraction(alt)

    alt_r = np.radians(alt)
    az_r = np.radians(az)
    lat_r = np.radians(lat_deg)

    sin_dec = (np.sin(alt_r) * np.sin(lat_r)
               + np.cos(alt_r) * np.cos(lat_r) * np.cos(az_r))
    dec_r = np.arcsin(np.clip(sin_dec, -1.0, 1.0))

    denom = np.cos(dec_r) * np.cos(lat_r)
    safe_denom = np.where(np.abs(denom) < 1e-10,
                          np.sign(denom) * 1e-10, denom)
    cos_ha = (np.sin(alt_r) - np.sin(dec_r) * np.sin(lat_r)) / safe_denom
    ha_r = np.arccos(np.clip(cos_ha, -1.0, 1.0))
    ha_r = np.where(np.sin(az_r) > 0, 2.0 * np.pi - ha_r, ha_r)

    jd = julian_date(dt)
    lsa = lst_degrees(jd, lon_deg)
    ra_d = (lsa - np.degrees(ha_r)) % 360.0
    dec_d = np.degrees(dec_r)
    return ra_d, dec_d


# ---------------------------------------------------------------------------
# Atmospheric Refraction (Bennett 1982)
# ---------------------------------------------------------------------------

def atmospheric_refraction(alt_deg: Numeric) -> Numeric:
    """
    Atmospheric refraction correction in degrees (Bennett 1982).

    Returns correction to ADD to true altitude to get observed altitude.
    Typical values: ~0.5° at horizon, ~0° near zenith.
    Returns 0 for altitudes below -2°.
    """
    alt = np.asarray(alt_deg, dtype=float)
    alt_clamped = np.clip(alt, -1.9, 90.0)
    a = np.radians(alt_clamped + 7.31 / (alt_clamped + 4.4))
    R_arcmin = 1.0 / np.tan(a)
    R_deg = np.clip(R_arcmin / 60.0, 0.0, 1.0)
    return np.where(alt < -2.0, 0.0, R_deg)


# ---------------------------------------------------------------------------
# Obliquity of the Ecliptic
# ---------------------------------------------------------------------------

def mean_obliquity(jd: float) -> float:
    """Mean obliquity of the ecliptic in degrees (Meeus Eq. 22.2)."""
    T = (jd - 2451545.0) / 36525.0
    eps0 = (23.0 + 26.0 / 60.0 + 21.448 / 3600.0
            - (46.8150 / 3600.0) * T
            - (0.00059 / 3600.0) * T * T
            + (0.001813 / 3600.0) * T * T * T)
    return eps0


def ecliptic_to_equatorial(lon_deg: Numeric, lat_deg: Numeric, jd: float) -> tuple:
    """
    Convert ecliptic (lon, lat) degrees to equatorial (RA, Dec) degrees.
    Returns (ra_deg, dec_deg).
    """
    eps = np.radians(mean_obliquity(jd))
    lon = np.radians(lon_deg)
    lat = np.radians(lat_deg)

    sin_dec = (np.sin(lat) * np.cos(eps)
               + np.cos(lat) * np.sin(eps) * np.sin(lon))
    dec = np.degrees(np.arcsin(np.clip(sin_dec, -1.0, 1.0)))

    y = np.sin(lon) * np.cos(eps) - np.tan(lat) * np.sin(eps)
    x = np.cos(lon)
    ra = np.degrees(np.arctan2(y, x)) % 360.0

    return ra, dec
