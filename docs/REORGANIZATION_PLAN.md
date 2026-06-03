# PFRSentinel — Directory Reorganization Plan

**Goal**: Move stray root-level Python modules into their proper packages, group
the ZWO camera files into a `services/camera/` sub-package (mirroring the existing
`services/allsky/` and `services/meteor/` patterns), and collect the
`MainWindow` mixin files into a `ui/main_window/` package so the `ui/` root
stays clean.

---

## Current problems

| Location | File(s) | Problem |
|----------|---------|---------|
| project root | `app_config.py` | App-identity constants imported by ~12 files — logically belongs in `services/` |
| project root | `utils_paths.py` | Path utilities for PyInstaller + AppData — logically belongs in `services/` |
| project root | `logging_config.py` | **Dead code.** Zero importers; references old app name "ASIOverlayWatchDog"; superseded entirely by `services/logger.py` |
| `services/` root | `camera_calibration.py`, `camera_config.py`, `camera_connection.py`, `camera_reconnect.py`, `camera_utils.py`, `zwo_camera.py`, `zwo_capture_worker.py` | 7 camera files mixed into the flat services/ root — `allsky/` and `meteor/` already use sub-packages |
| `ui/` root | `main_window_capture.py`, `main_window_lifecycle.py`, `main_window_output.py`, `main_window_settings.py` | 4 mixin files alongside `main_window.py` — package them together |

---

## Phases

### Phase 1 — Delete dead code

**File**: `logging_config.py` (131 lines)

Zero importers confirmed by grep. The entire file is legacy from the
"ASIOverlayWatchDog" era. The app's real logging pipeline is
`services/logger.py` (thread-safe queue, `app_logger` singleton).

Action: **delete** `logging_config.py`.

No import updates needed.

---

### Phase 2 — Move root utilities into `services/`

#### 2a. `app_config.py` → `services/app_config.py`

Importers to update:

| File | Current import | New import |
|------|---------------|------------|
| `main.py` | `from app_config import APP_DISPLAY_NAME, APP_SUBTITLE` | `from services.app_config import ...` |
| `ui/main_window.py` (→ `ui/main_window/core.py` after Phase 4) | `from app_config import ...` | `from services.app_config import ...` |
| `services/config.py` | `from app_config import APP_DATA_FOLDER, DEFAULT_OUTPUT_SUBFOLDER` | `from .app_config import ...` |
| `services/discord_alerts.py` | `from app_config import APP_DISPLAY_NAME` | `from .app_config import ...` |
| `services/heartbeat.py` | `from app_config import APP_DATA_FOLDER` | `from .app_config import ...` |
| `services/logger.py` | `from app_config import APP_NAME, APP_DATA_FOLDER, LOG_FILE` | `from .app_config import ...` |
| `services/timelapse_writer.py` | `from app_config import APP_DATA_FOLDER` (deferred) | `from services.app_config import ...` |
| `services/weather.py` | `from app_config import APP_DATA_FOLDER` (deferred) | `from services.app_config import ...` |
| `services/allsky/calibration_service.py` | `from app_config import APP_DATA_FOLDER` (deferred) | `from services.app_config import ...` |
| `ui/controllers/allsky_controller.py` | `from app_config import APP_DATA_FOLDER` (deferred) | `from services.app_config import ...` |
| `ui/controllers/meteor_controller.py` | `from app_config import APP_DATA_FOLDER` (deferred) | `from services.app_config import ...` |
| `scripts/dev/allsky/allsky_debug.py` | `from app_config import APP_DATA_FOLDER` (deferred) | `from services.app_config import ...` (add root to sys.path guard if needed) |

Also add `utils_paths.py`'s fallback guard (`except ImportError: APP_DATA_FOLDER = "PFRSentinel"`)
— once both files move into `services/`, the guard in `utils_paths.py` becomes `from .app_config import APP_DATA_FOLDER`.

#### 2b. `utils_paths.py` → `services/utils_paths.py`

Importers to update:

| File | Current import | New import |
|------|---------------|------------|
| `services/config.py` | `from utils_paths import resource_path, get_exe_dir` | `from .utils_paths import ...` |
| `services/ml_data_collector.py` | `from utils_paths import get_ml_contribution_dir` | `from .utils_paths import ...` |
| `services/update_checker.py` | `from utils_paths import get_app_data_dir` | `from .utils_paths import ...` |
| `services/allsky/catalogs.py` | `from utils_paths import resource_path` (deferred) | `from services.utils_paths import ...` |
| `ui/components/app_bar.py` | `from utils_paths import resource_path` (deferred) | `from services.utils_paths import ...` |
| `ui/main_window.py` (→ `core.py`) | `from utils_paths import resource_path` (deferred) | `from services.utils_paths import ...` |
| `ui/system_tray_qt.py` | `from utils_paths import resource_path` | `from services.utils_paths import ...` |
| `scripts/fix_cameras.py` | `from utils_paths import get_app_data_dir` (adds root to sys.path first) | `from services.utils_paths import get_app_data_dir` (update path setup) |

After moving, `logging_config.py` is already deleted so its `from utils_paths import get_log_dir`
import disappears with it.

**Root shims** (keep the root-level filenames as one-liners during transition, remove
in a follow-up once every caller is updated):

```python
# app_config.py  (root shim — delete after all callers updated)
from services.app_config import *  # noqa: F401,F403

# utils_paths.py  (root shim — delete after all callers updated)
from services.utils_paths import *  # noqa: F401,F403
```

Alternatively, update all callers in a single commit and delete root files immediately —
preferred since the caller list is short and fully enumerated above.

---

### Phase 3 — Camera files → `services/camera/` sub-package

This mirrors the existing `services/allsky/` and `services/meteor/` patterns.

#### Files to move

| Current path | New path |
|-------------|----------|
| `services/camera_calibration.py` | `services/camera/calibration.py` |
| `services/camera_config.py` | `services/camera/config.py` |
| `services/camera_connection.py` | `services/camera/connection.py` |
| `services/camera_reconnect.py` | `services/camera/reconnect.py` |
| `services/camera_utils.py` | `services/camera/utils.py` |
| `services/zwo_camera.py` | `services/camera/zwo_camera.py` |
| `services/zwo_capture_worker.py` | `services/camera/capture_worker.py` |

#### `services/camera/__init__.py` (new file)

```python
from .zwo_camera import ZWOCamera
from .camera_utils import (
    calculate_brightness, check_clipping,
    is_within_scheduled_window,
)
from .camera_connection import CameraConnection
from .camera_calibration import CameraCalibration

__all__ = [
    'ZWOCamera', 'CameraConnection', 'CameraCalibration',
    'calculate_brightness', 'check_clipping', 'is_within_scheduled_window',
]
```

#### Internal relative imports to fix

Within the moved files, all `from .camera_xxx import` and `from . import zwo_capture_worker`
references become either same-package relative imports or updated names:

| Old relative import | New relative import |
|--------------------|-------------------|
| `from .camera_utils import ...` | `from .utils import ...` |
| `from .camera_config import ...` | `from .config import ...` |
| `from .camera_reconnect import ...` | `from .reconnect import ...` |
| `from . import zwo_capture_worker` | `from . import capture_worker` (also update call sites: `zwo_capture_worker.foo` → `capture_worker.foo`) |

#### External importers to update

| File | Current import | New import |
|------|---------------|------------|
| `services/__init__.py` | `from .zwo_camera import ZWOCamera` | `from .camera import ZWOCamera` |
| `services/headless_runner.py` | `from .zwo_camera import ZWOCamera` | `from .camera import ZWOCamera` |
| `ui/controllers/camera_controller.py` | `from services.zwo_camera import ZWOCamera` | `from services.camera import ZWOCamera` |
| `tests/test_auto_exposure.py` | `from services.camera_utils import ...` | `from services.camera import ...` (or `from services.camera.utils import ...`) |
| `tests/test_camera.py` | `from services.camera_utils import ...` / `from services.camera_connection import ...` | `from services.camera import ...` |

#### Rule file update

`CLAUDE.md` and `.claude/rules/services-camera.md` reference paths like
`services/zwo_camera.py` and `services/camera_*.py` — update to the new paths.

---

### Phase 4 — `ui/main_window/` package

The four mixin files are only ever imported by `ui/main_window.py`. Turning them
into a package makes `ui/` root contain only single-file modules and proper
sub-packages (`components/`, `controllers/`, `dialogs/`, `panels/`, `theme/`,
`main_window/`).

#### Files to move

| Current path | New path |
|-------------|----------|
| `ui/main_window.py` | `ui/main_window/core.py` |
| `ui/main_window_capture.py` | `ui/main_window/capture.py` |
| `ui/main_window_lifecycle.py` | `ui/main_window/lifecycle.py` |
| `ui/main_window_output.py` | `ui/main_window/output.py` |
| `ui/main_window_settings.py` | `ui/main_window/settings.py` |

#### `ui/main_window/__init__.py` (new file)

```python
from .core import MainWindow

__all__ = ['MainWindow']
```

#### Internal import fixes in `core.py`

The mixin imports in `core.py` (formerly `main_window.py`) change from:

```python
from .main_window_capture import _MainWindowCaptureMixin
from .main_window_output import _MainWindowOutputMixin
from .main_window_settings import _MainWindowSettingsMixin
from .main_window_lifecycle import _MainWindowLifecycleMixin
```

to:

```python
from .capture import _MainWindowCaptureMixin
from .output import _MainWindowOutputMixin
from .settings import _MainWindowSettingsMixin
from .lifecycle import _MainWindowLifecycleMixin
```

The `sys.path.insert(0, ...)` guard at the top of `main_window.py` also goes away —
it was a workaround for importing `app_config` from root; after Phase 2 that import
uses `services.app_config`, so no path manipulation is needed.

#### External callers — no changes needed

Both `main.py` (`from ui.main_window import MainWindow`) and
`ui/controllers/allsky_controller.py` (`from ui.main_window import MainWindow`)
continue to work unchanged via `__init__.py`.

---

### Phase 5 — Verification

After each phase (or in a single pass at the end):

```powershell
# Standard test run
pytest -m "not requires_camera and not requires_network and not requires_ml_models"

# Confirm main.py imports resolve (no GUI launch required)
python -c "from ui.main_window import MainWindow; print('OK')"

# Check no remaining root-module imports
grep -rn "from app_config\|from utils_paths\|from logging_config" . --include="*.py" \
    --exclude-dir=venv --exclude-dir=archive
```

Expected outcome: 360 tests pass (same baseline as the overlay-panel session).

---

## Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Deferred imports inside functions (e.g. `from app_config import APP_DATA_FOLDER` inside a method) are easy to miss | The grep in Phase 5 catches any stragglers |
| `scripts/fix_cameras.py` manually adds root to `sys.path` to import `utils_paths` | Update the path setup alongside the import |
| `services/camera/__init__.py` might not re-export every symbol tests use | Audit `test_auto_exposure.py` and `test_camera.py` imports and expose whatever is needed |
| `ui/main_window/core.py` still contains `sys.path.insert` guard | Remove it when app_config moves to `services/` (Phase 2 prerequisite) |
| PyInstaller `.spec` file may have explicit `hiddenimports` or `datas` for moved modules | Check `PFRSentinel.spec` and update any hard-coded module paths |

---

## Order of execution

Phases **must** run in order because later phases depend on earlier ones (e.g.
`core.py` imports `services.app_config` which only exists after Phase 2).

```
Phase 1 (delete)  →  Phase 2 (root utils)  →  Phase 3 (camera)  →  Phase 4 (main_window)  →  Phase 5 (verify)
```

Each phase ends with a green test run before the next starts.

---

## Out of scope

- Moving `version.py` — it's a 3-line single-source-of-truth file, root is the right place.
- Moving `main.py` — entry point, must stay at root.
- Any changes to `services/allsky/` or `services/meteor/` — already correctly structured.
- Renaming public APIs (`ZWOCamera`, `add_overlays`, etc.) — this is a structural move only.
