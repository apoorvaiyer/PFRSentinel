"""Tests for services.config_migrate — legacy zwo_* → camera_profiles migration."""
import json

import pytest

from services.config import Config
from services.config_migrate import (
    DEAD_KEYS,
    LEGACY_TO_PROFILE,
    migrate_legacy_camera_keys,
)


class TestMigrateLegacyCameraKeys:
    def test_empty_config_is_noop(self):
        data = {}
        assert migrate_legacy_camera_keys(data) == {}

    def test_config_without_legacy_keys_is_noop(self):
        data = {"capture_mode": "camera", "camera_profiles": {"X": {"exposure_ms": 50}}}
        before = json.dumps(data, sort_keys=True)
        after = migrate_legacy_camera_keys(data)
        assert json.dumps(after, sort_keys=True) == before

    def test_legacy_keys_fold_into_active_profile(self):
        data = {
            "zwo_selected_camera_name": "ZWO ASI676MC",
            "zwo_exposure_ms": 250.0,
            "zwo_gain": 150,
            "zwo_wb_r": 80,
            "zwo_wb_b": 90,
            "zwo_offset": 22,
            "zwo_flip": 1,
            "zwo_bayer_pattern": "RGGB",
            "zwo_max_exposure_ms": 20000.0,
            "zwo_target_brightness": 110,
        }
        result = migrate_legacy_camera_keys(data)

        assert "camera_profiles" in result
        profile = result["camera_profiles"]["ZWO ASI676MC"]
        assert profile == {
            "exposure_ms": 250.0,
            "gain": 150,
            "wb_r": 80,
            "wb_b": 90,
            "offset": 22,
            "flip": 1,
            "bayer_pattern": "RGGB",
            "max_exposure_ms": 20000.0,
            "target_brightness": 110,
        }
        # All legacy per-camera keys stripped
        for legacy_key in LEGACY_TO_PROFILE:
            assert legacy_key not in result

    def test_falls_back_to_zwo_camera_name(self):
        data = {"zwo_camera_name": "Old Camera", "zwo_exposure_ms": 333.0}
        result = migrate_legacy_camera_keys(data)
        assert result["camera_profiles"]["Old Camera"]["exposure_ms"] == 333.0

    def test_unassigned_slot_when_no_camera_name(self):
        """Legacy values aren't silently lost when no camera is selected."""
        data = {"zwo_exposure_ms": 999.0}
        result = migrate_legacy_camera_keys(data)
        assert "__unassigned__" in result["camera_profiles"]
        assert result["camera_profiles"]["__unassigned__"]["exposure_ms"] == 999.0

    def test_existing_profile_value_wins(self):
        """When profile already has a value, the legacy global is dropped (profile wins)."""
        data = {
            "zwo_selected_camera_name": "Cam",
            "zwo_exposure_ms": 100.0,  # legacy
            "camera_profiles": {"Cam": {"exposure_ms": 500.0}},  # profile already set
        }
        result = migrate_legacy_camera_keys(data)
        assert result["camera_profiles"]["Cam"]["exposure_ms"] == 500.0
        assert "zwo_exposure_ms" not in result

    def test_is_idempotent(self):
        data = {
            "zwo_selected_camera_name": "Cam",
            "zwo_gain": 200,
        }
        once = migrate_legacy_camera_keys(dict(data))
        twice = migrate_legacy_camera_keys(dict(once))
        assert once == twice

    def test_dead_keys_are_stripped(self):
        data = {"zwo_auto_wb": True, "capture_mode": "camera"}
        result = migrate_legacy_camera_keys(data)
        for dead in DEAD_KEYS:
            assert dead not in result
        # Non-dead keys preserved
        assert result["capture_mode"] == "camera"

    def test_preserves_global_zwo_keys(self):
        """zwo_interval, zwo_auto_exposure, zwo_sdk_path are globals — not migrated."""
        data = {
            "zwo_selected_camera_name": "Cam",
            "zwo_interval": 10.0,
            "zwo_auto_exposure": True,
            "zwo_sdk_path": "C:/foo/ASICamera2.dll",
            "zwo_exposure_ms": 200.0,  # this one DOES migrate
        }
        result = migrate_legacy_camera_keys(data)
        assert result["zwo_interval"] == 10.0
        assert result["zwo_auto_exposure"] is True
        assert result["zwo_sdk_path"] == "C:/foo/ASICamera2.dll"
        assert "zwo_exposure_ms" not in result


class TestMigrationAtLoadTime:
    """End-to-end: writing a legacy config to disk, then loading via Config, should migrate."""

    def test_legacy_config_migrates_on_load(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({
            "zwo_selected_camera_name": "ZWO ASI676MC",
            "zwo_exposure_ms": 250.0,
            "zwo_gain": 180,
            "zwo_bayer_pattern": "BGGR",
            "zwo_auto_wb": False,
        }))

        cfg = Config(str(cfg_path))

        # Legacy keys gone
        assert cfg.get("zwo_exposure_ms") is None
        assert cfg.get("zwo_auto_wb") is None
        # Profile populated
        profile = cfg.data["camera_profiles"]["ZWO ASI676MC"]
        assert profile["exposure_ms"] == 250.0
        assert profile["gain"] == 180
        assert profile["bayer_pattern"] == "BGGR"
