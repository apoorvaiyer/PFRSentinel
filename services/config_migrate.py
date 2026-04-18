"""Legacy config-key migration.

Historically, per-camera settings lived as flat top-level keys (`zwo_exposure_ms`,
`zwo_gain`, `zwo_wb_r`, …). The current model is `camera_profiles[clean_name]`,
which avoids cross-contamination when the user swaps cameras.

`migrate_legacy_camera_keys` folds any legacy flat keys into the active camera's
profile and strips them from the dict. Idempotent — safe to call on already-migrated
configs.
"""
from __future__ import annotations

from typing import Any


LEGACY_TO_PROFILE = {
    "zwo_exposure_ms": "exposure_ms",
    "zwo_gain": "gain",
    "zwo_max_exposure_ms": "max_exposure_ms",
    "zwo_target_brightness": "target_brightness",
    "zwo_wb_r": "wb_r",
    "zwo_wb_b": "wb_b",
    "zwo_offset": "offset",
    "zwo_flip": "flip",
    "zwo_bayer_pattern": "bayer_pattern",
}

# Legacy global-ish key with no new home — replaced by `white_balance.mode`.
DEAD_KEYS = ("zwo_auto_wb",)


def migrate_legacy_camera_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Fold legacy `zwo_*` per-camera keys into `camera_profiles[active_camera]`.

    Mutates and returns `data`. No-op when no legacy keys are present.

    Rules:
    - Active camera name is taken from `zwo_selected_camera_name` (falls back to
      `zwo_camera_name`). If neither is set, a synthetic `"__unassigned__"` slot
      is used so the values aren't silently lost.
    - If the profile already has a value for a migrated key, the existing profile
      value wins — the legacy global value is dropped. This matches how the app
      actually used the keys: profile first, flat global as fallback.
    - `zwo_auto_wb` is dropped entirely — it duplicates `white_balance.mode`.
    """
    if not isinstance(data, dict):
        return data

    has_legacy_per_camera = any(k in data for k in LEGACY_TO_PROFILE)
    has_dead_keys = any(k in data for k in DEAD_KEYS)
    if not has_legacy_per_camera and not has_dead_keys:
        return data

    for dead in DEAD_KEYS:
        data.pop(dead, None)

    if not has_legacy_per_camera:
        return data

    active = data.get("zwo_selected_camera_name") or data.get("zwo_camera_name") or "__unassigned__"
    profiles = data.setdefault("camera_profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        data["camera_profiles"] = profiles

    profile = profiles.setdefault(active, {})
    if not isinstance(profile, dict):
        profile = {}
        profiles[active] = profile

    for legacy_key, profile_key in LEGACY_TO_PROFILE.items():
        if legacy_key not in data:
            continue
        legacy_value = data.pop(legacy_key)
        profile.setdefault(profile_key, legacy_value)

    return data
