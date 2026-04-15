"""Default configuration schema for the All-Sky Overlay system."""

ALLSKY_OVERLAY_DEFAULTS = {
    "enabled": False,
    # Path to calibration JSON file (auto-set after successful calibration)
    "calibration_file": "",
    # UTC offset of timestamps embedded in image metadata/filenames.
    # NINA and most capture software write LOCAL time, not UTC.
    # Set this to your UTC offset so constellations align correctly.
    # Examples: US Central = -6, US Eastern = -5, UK Winter = 0, UK Summer = +1
    "utc_offset_hours": 0,
    # Global object limit: show only the N brightest objects across ALL types
    # (Messier, NGC, planets combined). 0 = unlimited.
    "top_n": 15,
    "constellations": {
        "enabled": True,
        "lines": True,
        "labels": True,
        "color": "#4488FF",
        "line_width": 1,
        "label_size": 12,
        "opacity": 180,
        # Fade lines over this many pixels approaching the sky circle edge (0 = no fade)
        "edge_fade_px": 250,
    },
    "messier": {
        "enabled": True,
        "color": "#FF8844",
        "label_size": 10,
        "opacity": 200,
    },
    "ngc": {
        "enabled": False,
        "min_magnitude": 8.0,
        "color": "#88FF44",
        "label_size": 9,
        "opacity": 150,
    },
    "planets": {
        "enabled": True,
        "label_size": 14,
        "opacity": 255,
        "colors": {
            "Mercury": "#B0B0B0",
            "Venus":   "#FFFFCC",
            "Mars":    "#FF6644",
            "Jupiter": "#FFCC88",
            "Saturn":  "#FFDDAA",
            "Uranus":  "#88DDFF",
            "Neptune": "#4466FF",
            "Moon":    "#FFFFEE",
        },
    },
    "grid": {
        "enabled": True,
        "horizon": False,          # Horizon circle hidden by default
        "altitude_rings": True,
        "altitude_step": 30,
        "azimuth_lines": True,
        "cardinal_labels": True,
        "color": "#336633",
        "line_width": 1,
        "label_size": 14,
        "opacity": 120,
    },
}
