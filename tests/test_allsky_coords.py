"""
Tests for services/allsky/coords.py — coordinate transforms and refraction.

All tests use known reference values from Meeus "Astronomical Algorithms" 2nd ed.
or verifiable astronomical facts (e.g. zenith star always at alt=90°).
"""
import math
import pytest
from datetime import datetime, timezone

# ------------------------------------------------------------------ imports
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.coords import (
    julian_date,
    gmst_degrees,
    lst_degrees,
    radec_to_altaz,
    altaz_to_radec,
    atmospheric_refraction,
    ecliptic_to_equatorial,
)


# ===================================================================
# Julian Date
# ===================================================================

class TestJulianDate:
    def test_j2000_epoch(self):
        """J2000.0 = 2000-01-01 12:00:00 UTC → JD 2451545.0"""
        dt = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert abs(julian_date(dt) - 2451545.0) < 1e-5

    def test_b1950_epoch(self):
        """B1950.0 ≈ JD 2433282.423"""
        dt = datetime(1950, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        jd = julian_date(dt)
        assert abs(jd - 2433282.5) < 0.1

    def test_gregorian_reform(self):
        """Meeus example: 1582-10-15 → JD 2299160.5"""
        dt = datetime(1582, 10, 15, 0, 0, 0, tzinfo=timezone.utc)
        assert abs(julian_date(dt) - 2299160.5) < 0.01

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime should be treated as UTC (no timezone offset)."""
        dt_naive  = datetime(2024, 6, 15, 12, 0, 0)
        dt_aware  = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert abs(julian_date(dt_naive) - julian_date(dt_aware)) < 1e-8


# ===================================================================
# GMST
# ===================================================================

class TestGMST:
    def test_j2000_gmst(self):
        """
        At J2000.0 (2000-01-01 12:00:00 UTC), GMST ≈ 280.46061° (Meeus Eq. 12.4).
        """
        jd = 2451545.0
        g = gmst_degrees(jd) % 360.0
        assert abs(g - 280.46061) < 0.01

    def test_gmst_range(self):
        """GMST must be in [0, 360)."""
        for jd in [2451545.0, 2451545.5, 2400000.0, 2500000.0]:
            g = gmst_degrees(jd)
            assert 0.0 <= g < 360.0


# ===================================================================
# Coordinate round-trip
# ===================================================================

class TestAltAzRoundTrip:
    """Convert RA/Dec → AltAz → RA/Dec; verify round-trip to < 0.1°."""

    LAT = 51.5    # London
    LON = -0.1
    DT  = datetime(2024, 3, 20, 22, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize("ra,dec", [
        (0.0,   0.0),    # Vernal equinox direction
        (90.0,  30.0),
        (180.0, -45.0),
        (270.0, 60.0),
        (45.0,  75.0),
    ])
    def test_round_trip(self, ra, dec):
        alt, az = radec_to_altaz(ra, dec, self.LAT, self.LON, self.DT,
                                 refraction=False)
        ra2, dec2 = altaz_to_radec(float(alt), float(az), self.LAT, self.LON, self.DT,
                                    refraction=False)
        assert abs(dec2 - dec) < 0.1, f"Dec round-trip failed: {dec} → {dec2}"
        # RA wraps; check modular difference
        d_ra = abs((ra2 - ra + 180) % 360 - 180)
        assert d_ra < 0.2, f"RA round-trip failed: {ra} → {ra2}"

    def test_zenith_is_90(self):
        """A star at the zenith should have alt = 90° (ignoring refraction)."""
        # The LST at this moment gives the zenith RA
        from services.allsky.coords import julian_date, lst_degrees
        jd = julian_date(self.DT)
        ra_zenith = lst_degrees(jd, self.LON)
        dec_zenith = self.LAT
        alt, az = radec_to_altaz(ra_zenith, dec_zenith, self.LAT, self.LON, self.DT,
                                 refraction=False)
        assert abs(float(alt) - 90.0) < 0.5, f"Zenith alt = {alt} ≠ 90°"

    def test_northern_star_circumpolar(self):
        """Polaris (dec ≈ 89.3°) should always be above horizon at lat=51.5°."""
        ra_polaris, dec_polaris = 37.95, 89.26
        for hour in range(0, 24, 4):
            dt = datetime(2024, 3, 20, hour, 0, 0, tzinfo=timezone.utc)
            alt, _ = radec_to_altaz(ra_polaris, dec_polaris, self.LAT, self.LON, dt,
                                    refraction=False)
            assert float(alt) > 0.0, f"Polaris below horizon at hour={hour}"


# ===================================================================
# Atmospheric Refraction
# ===================================================================

class TestRefraction:
    def test_zenith_near_zero(self):
        """Refraction at zenith (alt=90°) should be ≈ 0."""
        r = float(atmospheric_refraction(90.0))
        assert r < 0.01

    def test_horizon_half_degree(self):
        """Refraction at horizon (alt=0°) should be ≈ 0.5°."""
        r = float(atmospheric_refraction(0.0))
        assert 0.4 < r < 0.6, f"Horizon refraction = {r}° (expected ≈0.5°)"

    def test_below_horizon_zero(self):
        """Refraction below -2° should return 0."""
        r = float(atmospheric_refraction(-5.0))
        assert r == 0.0

    def test_monotonic(self):
        """Refraction should decrease monotonically from horizon to zenith."""
        alts = [0, 5, 10, 20, 30, 45, 60, 80, 90]
        refractions = [float(atmospheric_refraction(a)) for a in alts]
        for i in range(len(refractions) - 1):
            assert refractions[i] >= refractions[i + 1], (
                f"Refraction non-monotonic at alt={alts[i]}"
            )

    def test_refraction_applied_increases_alt(self):
        """Adding refraction should increase the apparent altitude."""
        alt_true = 20.0
        refraction = float(atmospheric_refraction(alt_true))
        assert refraction > 0.0


# ===================================================================
# Ecliptic → Equatorial
# ===================================================================

class TestEclipticToEquatorial:
    def test_vernal_equinox(self):
        """Ecliptic lon=0°, lat=0° → RA=0°, Dec=0°."""
        jd = 2451545.0  # J2000.0
        ra, dec = ecliptic_to_equatorial(0.0, 0.0, jd)
        assert abs(float(ra)) < 0.5
        assert abs(float(dec)) < 0.5

    def test_summer_solstice(self):
        """Ecliptic lon=90°, lat=0° → RA≈90°, Dec≈+23.4°."""
        jd = 2451545.0
        ra, dec = ecliptic_to_equatorial(90.0, 0.0, jd)
        assert abs(float(ra) - 90.0) < 1.0
        assert abs(float(dec) - 23.4) < 0.5
