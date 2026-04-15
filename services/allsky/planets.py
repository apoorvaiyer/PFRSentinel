"""
Planet position calculator using Meeus Astronomical Algorithms (2nd ed.).

Implements truncated VSOP87-style heliocentric elements for Mercury–Neptune
and the Chapter 47 simplified Moon series. No network dependency.

Accuracy: ~1–5 arcminutes for 2000–2050. Sufficient for all-sky overlay labels.
"""
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple

from .coords import julian_date, ecliptic_to_equatorial

# ---------------------------------------------------------------------------
# Orbital elements at J2000.0 and secular rates (degrees, per Julian century)
# From Meeus Table 33.a "Keplerian elements for approximate positions"
#   Columns: L0, L_dot, a(AU), e, i, Omega, omega_bar
# ---------------------------------------------------------------------------
_ELEMENTS = {
    'Mercury': (252.250906, 149472.6746358, 0.38709831,  0.20563175,  7.004986, 48.330893,  77.456119),
    'Venus':   (181.979801,  58517.8156760, 0.72332982,  0.00677188,  3.394662, 76.679920, 131.563703),
    'Earth':   (100.466449,  35999.3728519, 1.000001018, 0.01670862,  0.000000,  0.000000, 102.937348),
    'Mars':    (355.433275,  19140.2993313, 1.52367934,  0.09340062,  1.849726, 49.558093, 336.060234),
    'Jupiter': ( 34.351484,   3034.9056746, 5.20260319,  0.04849485,  1.303270,100.464441,  14.331309),
    'Saturn':  ( 50.077444,   1222.1137943, 9.53667582,  0.05550825,  2.488878,113.665524,  93.057599),
    'Uranus':  (314.055005,    428.4669983,19.191263,    0.04629590,  0.773197, 74.005957, 173.005159),
    'Neptune': (304.348665,    218.4862002,30.068963,    0.00898809,  1.769952,131.784057,  48.123691),
}

PLANETS = ['Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune']


def _to_rad(deg: float) -> float:
    return np.radians(deg % 360.0)


def _heliocentric(name: str, T: float) -> Tuple[float, float, float]:
    """
    Compute heliocentric ecliptic longitude (deg), latitude (deg), radius (AU)
    for a planet at Julian centuries T from J2000.0.
    Uses mean elements with equation-of-centre correction.
    """
    L0, Ldot, a, e, inc, Omega, omega = _ELEMENTS[name]

    L = (L0 + Ldot * T) % 360.0          # Mean longitude (deg)
    M = np.radians((L - omega) % 360.0)  # Mean anomaly (rad)
    omega_r = np.radians(omega)
    Omega_r = np.radians(Omega)
    inc_r = np.radians(inc)

    # Equation of centre (Meeus Eq. 30.1, truncated to 3 terms)
    C = ((2.0 * e - e**3 / 4.0) * np.sin(M)
         + 5.0 / 4.0 * e**2 * np.sin(2.0 * M)
         + 13.0 / 12.0 * e**3 * np.sin(3.0 * M))
    C_deg = np.degrees(C)

    v = np.radians((L + C_deg - omega) % 360.0)  # True anomaly (rad)
    r = a * (1.0 - e**2) / (1.0 + e * np.cos(v))  # Radius vector (AU)

    # Heliocentric true longitude & latitude in ecliptic plane
    true_lon_r = v + omega_r  # = true anomaly + argument of perihelion
    # Ecliptic latitude
    sin_lat = np.sin(inc_r) * np.sin(true_lon_r - Omega_r)
    lat = np.degrees(np.arcsin(sin_lat))

    # Projected longitude
    y = np.sin(true_lon_r - Omega_r) * np.cos(inc_r)
    x = np.cos(true_lon_r - Omega_r)
    lon = (np.degrees(np.arctan2(y, x)) + np.degrees(Omega_r)) % 360.0

    return lon, lat, r


def _heliocentric_xyz(name: str, T: float) -> Tuple[float, float, float]:
    """Heliocentric ecliptic Cartesian coordinates (AU)."""
    lon, lat, r = _heliocentric(name, T)
    lon_r = np.radians(lon)
    lat_r = np.radians(lat)
    x = r * np.cos(lat_r) * np.cos(lon_r)
    y = r * np.cos(lat_r) * np.sin(lon_r)
    z = r * np.sin(lat_r)
    return x, y, z


def planet_radec(name: str, dt: datetime) -> Tuple[float, float]:
    """
    Compute geocentric RA and Dec (J2000, degrees) for a planet at UTC datetime.
    Returns (ra_deg, dec_deg).
    """
    jd = julian_date(dt)
    T = (jd - 2451545.0) / 36525.0

    # Heliocentric positions
    Ex, Ey, Ez = _heliocentric_xyz('Earth', T)
    Px, Py, Pz = _heliocentric_xyz(name, T)

    # Geocentric ecliptic
    gx, gy, gz = Px - Ex, Py - Ey, Pz - Ez
    dist = np.sqrt(gx**2 + gy**2 + gz**2)

    # Light-time correction (~0.006 days per AU); one iteration
    lt_T = T - dist * 0.0057755183 / 36525.0
    Px2, Py2, Pz2 = _heliocentric_xyz(name, lt_T)
    gx, gy, gz = Px2 - Ex, Py2 - Ey, Pz2 - Ez

    geo_lon = np.degrees(np.arctan2(gy, gx)) % 360.0
    geo_lat = np.degrees(np.arctan2(gz, np.sqrt(gx**2 + gy**2)))

    return ecliptic_to_equatorial(geo_lon, geo_lat, jd)


def sun_radec(dt: datetime) -> Tuple[float, float]:
    """
    Compute apparent RA and Dec of the Sun (degrees) using Meeus Ch.25 low precision.
    """
    jd = julian_date(dt)
    T = (jd - 2451545.0) / 36525.0

    L0 = 280.46646 + 36000.76983 * T  # Geometric mean longitude
    M = np.radians(357.52911 + 35999.05029 * T - 0.0001537 * T**2)
    C = ((1.914602 - 0.004817 * T - 0.000014 * T**2) * np.sin(M)
         + (0.019993 - 0.000101 * T) * np.sin(2.0 * M)
         + 0.000289 * np.sin(3.0 * M))
    sun_lon = (L0 + C) % 360.0
    # Apparent longitude (abberation)
    omega = np.radians(125.04 - 1934.136 * T)
    apparent_lon = sun_lon - 0.00569 - 0.00478 * np.sin(omega)

    return ecliptic_to_equatorial(apparent_lon, 0.0, jd)


# ---------------------------------------------------------------------------
# Moon — Meeus Chapter 47 simplified (accuracy ~5 arcminutes)
# ---------------------------------------------------------------------------

def moon_radec(dt: datetime) -> Tuple[float, float]:
    """Compute Moon geocentric RA and Dec (J2000, degrees)."""
    jd = julian_date(dt)
    T = (jd - 2451545.0) / 36525.0

    # Fundamental arguments (degrees)
    Lp = 218.3165 + 481267.8813 * T          # Moon mean longitude
    M  = 357.5291 +  35999.0503 * T          # Sun mean anomaly
    Mp = 134.9634 + 477198.8676 * T          # Moon mean anomaly
    D  = 297.8502 + 445267.1115 * T          # Moon mean elongation
    F  = 93.2721  + 483202.0175 * T          # Moon mean arg of latitude

    # Convert to radians
    Lp_r = np.radians(Lp)
    M_r  = np.radians(M)
    Mp_r = np.radians(Mp)
    D_r  = np.radians(D)
    F_r  = np.radians(F)

    # Longitude corrections (arcseconds → degrees)
    dL = (6288774 * np.sin(Mp_r)
          + 1274027 * np.sin(2*D_r - Mp_r)
          +  658314 * np.sin(2*D_r)
          +  213618 * np.sin(2*Mp_r)
          - 185116 * np.sin(M_r)
          - 114332 * np.sin(2*F_r)
          +   58793 * np.sin(2*D_r - 2*Mp_r)
          +   57066 * np.sin(2*D_r - M_r - Mp_r)
          +   53322 * np.sin(2*D_r + Mp_r)
          +   45758 * np.sin(2*D_r - M_r)
          -   40923 * np.sin(M_r - Mp_r)
          -   34720 * np.sin(D_r)
          -   30383 * np.sin(M_r + Mp_r)) / 1000000.0  # to degrees

    # Latitude corrections (arcseconds → degrees)
    dB = (5128122 * np.sin(F_r)
          +  280602 * np.sin(Mp_r + F_r)
          +  277693 * np.sin(Mp_r - F_r)
          +  173237 * np.sin(2*D_r - F_r)
          +   55413 * np.sin(2*D_r - Mp_r + F_r)
          +   46271 * np.sin(2*D_r - Mp_r - F_r)
          +   32573 * np.sin(2*D_r + F_r)
          +   17198 * np.sin(2*Mp_r + F_r)
          +    9266 * np.sin(2*D_r + Mp_r - F_r)
          +    8822 * np.sin(2*Mp_r - F_r)) / 1000000.0

    moon_lon = (Lp + dL) % 360.0
    moon_lat = dB

    return ecliptic_to_equatorial(moon_lon, moon_lat, jd)


# ---------------------------------------------------------------------------
# Convenience: all bodies at once
# ---------------------------------------------------------------------------

def get_all_positions(dt: datetime) -> Dict[str, Tuple[float, float]]:
    """
    Return RA/Dec (degrees) for all planets, Moon, and Sun.
    Keys: 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune',
          'Moon', 'Sun'.
    """
    positions = {}
    for name in PLANETS:
        try:
            positions[name] = planet_radec(name, dt)
        except Exception:
            pass
    try:
        positions['Moon'] = moon_radec(dt)
    except Exception:
        pass
    try:
        positions['Sun'] = sun_radec(dt)
    except Exception:
        pass
    return positions
