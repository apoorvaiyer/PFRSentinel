"""
Shared icon helpers.

Panels should use ``mdi()`` for Material Design Icon 6 glyphs instead of
reaching for ``qtawesome`` directly, so the color/theming stays consistent
with the nav rail.
"""
import qtawesome as qta

from .tokens import Colors


def mdi(name: str, color: str = None):
    """Material Design Icon 6 icon. Defaults to the muted-secondary app color."""
    return qta.icon(f'mdi6.{name}', color=color or Colors.text_secondary)
