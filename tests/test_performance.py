"""
Test performance measurement helpers
"""
import pytest
import os
import sys
import time

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.performance import ProcessingTimer, get_memory_usage_mb, get_disk_space


class TestProcessingTimer:
    """Test processing time measurement"""

    def test_timer_returns_positive_value(self):
        """Test processing time measurement returns reasonable value (>0)"""
        with ProcessingTimer() as timer:
            time.sleep(0.01)
        assert timer.elapsed > 0

    def test_timer_within_reasonable_range(self):
        """Test timer measures approximately correct duration"""
        with ProcessingTimer() as timer:
            time.sleep(0.05)
        # Should be around 0.05s, allow generous tolerance
        assert 0.01 < timer.elapsed < 1.0

    def test_timer_zero_work(self):
        """Test timer with no work returns very small value"""
        with ProcessingTimer() as timer:
            pass
        assert timer.elapsed >= 0
        assert timer.elapsed < 0.1


class TestMemoryUsage:
    """Test memory usage measurement"""

    def test_memory_returns_positive(self):
        """Test memory usage returns positive value or -1 on failure"""
        mb = get_memory_usage_mb()
        # Either positive (success) or -1 (no psutil/ctypes)
        assert mb > 0 or mb == -1.0

    def test_memory_reasonable_range(self):
        """Test memory is in reasonable range for a Python process"""
        mb = get_memory_usage_mb()
        if mb > 0:
            # Python process should use at least a few MB, less than 10 GB
            assert 1.0 < mb < 10000.0


class TestDiskSpace:
    """Test disk space queries"""

    def test_disk_space_valid_path(self, temp_dir):
        """Test disk space returns valid numbers for existing path"""
        result = get_disk_space(temp_dir)
        assert result is not None
        assert result['total_gb'] > 0
        assert result['free_gb'] >= 0
        assert result['used_gb'] >= 0

    def test_disk_space_invalid_path(self):
        """Test disk space handles invalid/nonexistent path gracefully"""
        result = get_disk_space('/nonexistent/path/that/does/not/exist')
        assert result is None

    def test_disk_space_none_path(self):
        """Test disk space handles None path"""
        result = get_disk_space(None)
        assert result is None

    def test_disk_space_empty_path(self):
        """Test disk space handles empty string"""
        result = get_disk_space('')
        assert result is None

    def test_disk_space_keys(self, temp_dir):
        """Test disk space returns expected keys"""
        result = get_disk_space(temp_dir)
        assert 'total_gb' in result
        assert 'used_gb' in result
        assert 'free_gb' in result
