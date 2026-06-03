"""
All-Sky Camera Overlay System for PFR Sentinel.

Public API:
    render_allsky_overlay(img, config, metadata) -> PIL.Image
"""

from .overlay_renderer import render_allsky_overlay

__all__ = ['render_allsky_overlay']
