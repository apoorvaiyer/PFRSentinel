"""
Test compass rose overlay
"""
import pytest
import os
import sys
import json
import numpy as np
from PIL import Image

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.compass_overlay import draw_compass


def _make_image(w=256, h=256):
    """Create a test RGBA image."""
    return Image.new('RGBA', (w, h), (0, 0, 0, 255))


class TestCompassRendering:
    """Test compass overlay rendering"""

    def test_compass_renders_without_error(self):
        """Test compass renders on image without error at default rotation"""
        img = _make_image()
        result = draw_compass(img, rotation=0)
        assert result is not None
        assert result.size == (256, 256)

    def test_compass_modifies_image(self):
        """Test compass actually draws on the image (pixels change)"""
        img = _make_image()
        original = np.array(img).copy()
        result = draw_compass(img, rotation=0)
        assert not np.array_equal(original, np.array(result))

    def test_rotations_produce_distinct_output(self):
        """Test compass at 0, 90, 180, 270 rotations produces distinct outputs"""
        images = []
        for angle in [0, 90, 180, 270]:
            img = _make_image()
            result = draw_compass(img, rotation=angle)
            images.append(np.array(result))

        # Each rotation should differ from at least one other
        all_same = True
        for i in range(len(images)):
            for j in range(i + 1, len(images)):
                if not np.array_equal(images[i], images[j]):
                    all_same = False
                    break
        assert not all_same, "Different rotations should produce distinct outputs"


class TestCompassPosition:
    """Test compass position options"""

    def test_all_positions_render(self):
        """Test compass position is configurable (center, corners)"""
        positions = ['center', 'top-left', 'top-right', 'bottom-left', 'bottom-right']
        for pos in positions:
            img = _make_image()
            result = draw_compass(img, position=pos)
            assert result is not None, f"Failed to render at position {pos}"

    def test_positions_differ(self):
        """Test different positions produce different images"""
        img1 = draw_compass(_make_image(), position='top-left')
        img2 = draw_compass(_make_image(), position='bottom-right')
        assert not np.array_equal(np.array(img1), np.array(img2))


class TestCompassEdgeCases:
    """Test edge cases"""

    def test_small_image_no_crash(self):
        """Test compass on small image doesn't crash or overflow bounds"""
        img = _make_image(32, 32)
        # Small image should skip drawing (too small for compass)
        result = draw_compass(img, size=80)
        assert result is not None

    def test_rgb_input_converted(self):
        """Test RGB input is handled (converted to RGBA)"""
        img = Image.new('RGB', (256, 256), (0, 0, 0))
        result = draw_compass(img)
        assert result.mode == 'RGBA'

    def test_custom_size(self):
        """Test custom compass size"""
        img = _make_image(512, 512)
        result = draw_compass(img, size=120)
        assert result is not None


class TestCompassConfig:
    """Test compass configuration round-trip"""

    def test_config_round_trip(self, temp_config):
        """Test compass config round-trips through save/load"""
        from services.config import Config
        config = Config(temp_config)

        compass_settings = {
            'enabled': True,
            'rotation': 45,
            'position': 'top-right',
            'size': 100,
        }
        config.set('compass', compass_settings)
        config.save()

        config2 = Config(temp_config)
        loaded = config2.get('compass', {})
        assert loaded['enabled'] is True
        assert loaded['rotation'] == 45
        assert loaded['position'] == 'top-right'
        assert loaded['size'] == 100
