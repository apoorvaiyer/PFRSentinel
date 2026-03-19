# CLAUDE.md — PFR Sentinel

Dual-mode astrophotography app: (1) watches directories for new images and adds metadata overlays, or (2) captures directly from ZWO ASI cameras with real-time processing. Built for 24/7 unattended operation using PySide6 + qfluentwidgets (Fluent Design UI).

## Project Structure

```
PFRSentinel/
├── ui/                         # PySide6 + qfluentwidgets UI
│   ├── main_window.py          # Qt FluentWindow, QStackedWidget navigation
│   ├── system_tray_qt.py       # Qt system tray + notifications
│   ├── components/             # Reusable widgets (header, monitoring_panel, status_indicator)
│   ├── panels/                 # Pages: monitoring, capture, output, overlays, logs
│   ├── controllers/            # Business logic: capture_controller, output_controller, overlay_controller
│   └── theme/                  # colors.py, styles.py (QSS)
├── services/                   # Core processing modules
│   ├── config.py               # JSON config in %APPDATA%\PFRSentinel\config.json
│   ├── logger.py               # Thread-safe queue-based logging (app_logger singleton)
│   ├── processor.py            # Image overlay engine — dual input: PIL Image OR file path
│   ├── watcher.py              # watchdog FileSystemEventHandler
│   ├── zwo_camera.py           # ZWO ASI SDK wrapper, BGGR debayering, auto-exposure
│   ├── camera_connection.py    # SDK init, detection, reconnect
│   ├── camera_calibration.py   # Auto-exposure algorithms
│   ├── camera_utils.py         # Shared camera utilities
│   ├── cleanup.py              # Disk space management — NEVER deletes folders, only files
│   ├── discord_alerts.py       # Discord webhook: periodic posts + event notifications
│   ├── weather.py              # OpenWeatherMap API, 10-min cache
│   └── web_output.py           # HTTP server: /latest (image), /status (JSON)
├── docs/                       # Documentation
├── archive/                    # Legacy Tkinter GUI (do not modify)
├── installer/                  # Inno Setup packaging
├── main.py                     # Entry point: from ui.main_window import main
├── app_config.py               # Returns %APPDATA%\PFRSentinel path, handles migration
└── version.py                  # VERSION, BUILD_DATE constants
```

## Architecture Patterns

### Data Flow
1. **Watch mode**: `watcher.py` → file stable → parse sidecar → `processor.py` (file path) → `_push_to_output_servers()`
2. **Camera mode**: `zwo_camera.py` → RAW8 Bayer → debayer → PIL Image + metadata dict → `capture_controller.py` → `processor.py` (PIL Image) → outputs
3. **Output push**: Checks config for enabled outputs → File / Web / Discord

### Threading
- Camera capture, directory watcher, Discord poster, web server all run in **background threads**
- **All GUI updates via Qt signals/slots or `QMetaObject.invokeMethod()`** — never direct from worker threads
- Logger uses queue-based message passing to avoid race conditions

### UI Architecture
- Panels (`ui/panels/`) = layout only, no business logic
- Controllers (`ui/controllers/`) = all business logic
- Communication via Qt signals/slots pattern

## Critical Technical Details

### ZWO Camera
- **Debayering**: RAW8 Bayer BGGR → RGB via `cv2.cvtColor(data, cv2.COLOR_BayerBG2RGB)` — CRITICAL: ASI676MC uses BGGR, NOT RGGB (wrong pattern causes red/blue swap)
- **Exposure units**: GUI uses **milliseconds**, internally converted to seconds for SDK
- **SDK**: `ASICamera2.dll` in app root or custom path via Capture tab

### Config
- **Location**: `%APPDATA%\PFRSentinel\config.json` — always use `app_config.get_config_dir()`
- **Access**: Use `config.set()` / `config.save()` — never edit JSON directly
- **Merge pattern**: `config.load()` merges saved JSON with `DEFAULT_CONFIG` for forward compatibility
- **Key structure**: Nested — `output_config.get('webserver_enabled')` NOT `config.get('web_enabled')`

### processor.py — Dual Input
```python
def add_overlays(image_input, overlays, metadata):
    if isinstance(image_input, str):
        img = Image.open(image_input)
    else:
        img = image_input  # Already PIL Image from camera
```

### Overlay Tokens
- Standard: `{CAMERA}`, `{EXPOSURE}`, `{GAIN}`, `{TEMP}`, `{RES}`, `{FILENAME}`, `{SESSION}`, `{DATETIME}`
- Weather: `{WEATHER}`, `{WEATHER_ICON}`, `{TEMP}`, `{HUMIDITY}`, `{PRESSURE}`, `{WIND_SPEED}` — requires `weather.api_key` + `weather.location` in config

## Code Standards

### File Size
- **Target: ≤500 lines per file** (hard cap: 550)
- Split by responsibility: `models.py`, `service.py`, `handlers.py`, `repo.py`, `errors.py`
- Avoid catch-all `utils.py` — use clearly named modules
- **Exceptions** — these legacy files exceed the limit but must NOT grow further. New functionality should be added to new/split files, not appended here:
  - `ui/main_window.py` (~1565 lines) — Qt FluentWindow orchestration; split planned for Phase 4
  - `services/processor.py` (~1271 lines) — image overlay engine; split planned for Phase 4
  - `ui/panels/overlay_settings.py` (~1338 lines) — overlay list/editor/preview; CRUD logic should move to a controller

### Imports
- From outside packages: `from services.module import Class`
- Within packages: `from .module import Class` (relative imports)

### Fluent Design
- Use `qfluentwidgets` components (PushButton, CardWidget, FluentWindow, etc.)
- Colors from `ui/theme/colors.py`, styles from `ui/theme/styles.py`
- Icons via `FluentIcon` enum

### Logging
- Use `app_logger` everywhere — never `print()` from threads
- Logs written to `%APPDATA%\PFRSentinel\logs`

## Development

```powershell
# Quick start
.\start.bat

# Manual
.\venv\Scripts\Activate.ps1
python main.py
```

- PyInstaller build: `build_sentinel.bat` (Python 3.13 requires email module workaround)
- Manual testing: verify both modes (Directory Watch and ZWO Camera)

### Testing

```powershell
# Run all unit tests (no hardware required)
pytest

# Skip tests that need network or camera hardware
pytest -m "not requires_camera and not requires_network"
```

**Test suite** (`tests/`, configured via `pytest.ini`):

| Test file | Tests | Covers | Notes |
|-----------|-------|--------|-------|
| `test_auto_exposure.py` | 21 | `camera_utils` — brightness, clipping, exposure logic | Pure unit tests |
| `test_camera.py` | 14 | `zwo_camera` — SDK integration, config, debayering | 3 tests need physical camera (`requires_camera`) |
| `test_discord.py` | 32 | `discord_alerts` — webhooks, embeds, error handling | All requests mocked |
| `test_image_output.py` | 18 | `processor` — overlays, stretch, format output | Uses Pillow/numpy |
| `test_settings.py` | 11 | `config` — JSON save/load, merge, defaults | Temp files only |
| `test_webserver.py` | 13 | `web_output` — HTTP server, ETag, status JSON | Starts real server (`requires_network`) |

**Standalone scripts** (not part of pytest suite):
- `ml/test_classifier.py` — validates roof classifier accuracy against labelled FITS data
- `test_usb_reset.py` — interactive USB reset test, requires physical ZWO camera

## Common Pitfalls

1. **Wrong Bayer pattern** — ASI676MC is BGGR (`COLOR_BayerBG2RGB`), not RGGB
2. **GUI calls from worker threads** — always use Qt signals/slots or `QMetaObject.invokeMethod()`
3. **Wrong config keys** — use nested `output_config.get('webserver_enabled')`, not `config.get('web_enabled')`
4. **Deleting folders in cleanup** — `cleanup.py` must only delete files (`os.path.isfile()` check)
5. **Business logic in panels** — panels are UI only; logic belongs in controllers
6. **Skipping debayering** — ZWO cameras output RAW8 Bayer, not RGB
7. **Weather API spam** — use 10-minute cache in `weather.py`
8. **Absolute imports within packages** — use relative imports inside `ui/` and `services/`
9. **Hardcoded config paths** — use `app_config.get_config_dir()`
10. **Missing image_count increment** — both watch and camera callbacks must update the counter

## Key Dependencies

| Package | Purpose |
|---------|---------|
| PySide6 6.8.1 | Qt6 bindings |
| qfluentwidgets 1.10.5 | Fluent Design components |
| opencv-python | Bayer debayering |
| Pillow | Image processing |
| watchdog | Directory monitoring |
| zwoasi | ZWO SDK Python wrapper |
| requests | Weather API + Discord webhooks |
| ffmpeg (external) | Timelapse recording |
| PyInstaller 6.17.0 | Standalone executable packaging |
