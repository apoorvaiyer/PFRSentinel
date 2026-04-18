# CLAUDE.md — PFR Sentinel

PFR Sentinel is a dual-mode astrophotography monitoring app built for 24/7 unattended observatory use. It either (1) watches a directory for new images written by another capture program (e.g. NINA), or (2) captures directly from a ZWO ASI camera. Either way, it adds configurable metadata + weather overlays and pushes the result to multiple output sinks simultaneously (file, web, Discord, RTSP).

Stack: Python 3.13, PySide6 6.8.1 + qfluentwidgets 1.10.5 (Windows 11 Fluent Design), Pillow, OpenCV, watchdog, ONNX runtime for ML inference. Packaged as a Windows installer via PyInstaller + Inno Setup.

## Capture modes

1. **Directory Watch** — `services/watcher.py` (watchdog) detects new FITS/JPEG/PNG, waits for file stability, parses sidecar metadata, runs the processor.
2. **ZWO Camera** — `services/zwo_camera.py` captures RAW8 Bayer frames, debayers (BGGR), and produces a PIL image + metadata dict for the processor.

## Output sinks (run simultaneously)

- **File** — saves to disk
- **Web** — HTTP server, `/latest` (image) and `/status` (JSON) endpoints
- **Discord** — periodic webhook posts with weather embeds
- **RTSP** — H.264 live stream via ffmpeg at `rtsp://127.0.0.1:8554/stream`

All output dispatch goes through `_push_to_output_servers()` in the processor.

## Project structure

```
PFRSentinel/
├── ui/                         # PySide6 + qfluentwidgets UI
│   ├── main_window.py          # FluentWindow + QStackedWidget navigation
│   ├── system_tray_qt.py       # Qt system tray + notifications
│   ├── components/             # Reusable widgets (header, monitoring panel, status indicator)
│   ├── panels/                 # Pages (layout only) — monitoring, capture, output, overlays, timelapse, logs
│   ├── controllers/            # Business logic — capture, output, overlay, timelapse, ML prediction
│   └── theme/                  # colors.py, styles.py
├── services/                   # Core processing modules
│   ├── config.py               # JSON config in %APPDATA%\PFRSentinel\config.json
│   ├── logger.py               # Thread-safe queue logger (app_logger singleton)
│   ├── processor.py            # Image overlay engine — dual input: PIL Image OR file path
│   ├── watcher.py              # watchdog FileSystemEventHandler
│   ├── zwo_camera.py           # ZWO ASI SDK wrapper, BGGR debayer, auto-exposure
│   ├── camera_connection.py    # SDK init, detection, USB reconnect
│   ├── camera_calibration.py   # Auto-exposure algorithms
│   ├── camera_utils.py         # Shared camera utilities
│   ├── cleanup.py              # Disk space management (files only, never folders)
│   ├── discord_alerts.py       # Discord webhook client
│   ├── weather.py              # OpenWeatherMap API, 10-min cache
│   ├── web_output.py           # HTTP server
│   ├── rtsp_output.py          # ffmpeg RTSP stream
│   ├── timelapse_writer.py     # ffmpeg stdin pipe, time-gated capture
│   ├── ffmpeg_utils.py         # Shared ffmpeg detection
│   └── allsky/                 # All-sky fisheye calibration + overlay
├── ml/                         # Scene classifiers (roof, sky conditions, stars, moon) — ONNX inference
├── tests/                      # pytest suite — see "Testing" below
├── docs/                       # Plans, design docs, references
├── archive/                    # Legacy Tkinter GUI (do not modify)
├── installer/                  # Inno Setup packaging
├── main.py                     # Entry point
├── app_config.py               # %APPDATA%\PFRSentinel path resolver, handles migration
└── version.py                  # VERSION, BUILD_DATE
```

## Architecture patterns

### Data flow
- **Watch mode**: `watcher.py` → file stable → parse sidecar → `processor.py` → `_push_to_output_servers()`
- **Camera mode**: `zwo_camera.py` → debayer → PIL Image + metadata → `capture_controller.py` → `processor.py` → outputs

### Threading
- Camera capture, watcher observer, Discord poster, web server, RTSP stream all run on background threads.
- All GUI updates flow through Qt signals/slots or `QMetaObject.invokeMethod()` — never touch widgets from worker threads.
- Logger uses a queue to avoid race conditions.

### UI architecture
- `ui/panels/` are layout only.
- `ui/controllers/` own business logic and threading.
- Communication is Qt signals/slots.

### Processor entry point
`services/processor.py` `add_overlays()` is dual-input — accepts a file path string OR an in-memory PIL image. Both modes route through the same code.

### Overlay tokens
Standard: `{CAMERA}`, `{EXPOSURE}`, `{GAIN}`, `{TEMP}`, `{RES}`, `{FILENAME}`, `{SESSION}`, `{DATETIME}`
Weather (requires `weather.api_key` + `weather.location` in config): `{WEATHER}`, `{WEATHER_ICON}`, `{TEMP}`, `{HUMIDITY}`, `{PRESSURE}`, `{WIND_SPEED}`

### Config
- Lives in `%APPDATA%\PFRSentinel\config.json` — always resolve via `app_config.get_config_dir()`.
- Loaded with a merge-against-`DEFAULT_CONFIG` pattern, so new keys land safely on old configs.
- Keys are nested. Output flags live under `output_config.*`, camera settings under `camera_profiles[<clean_name>]`.

### ML module
Phase 1–3 complete; Phase 4 future:
- **Phase 1**: Roof open/closed (CNN, 100% on test set)
- **Phase 2**: Sky conditions (85.3%), stars (91.2%), moon (100%)
- **Phase 3**: Dev-mode integration that saves calibration JSON + FITS per frame
- **Phase 4** (future): Stretch recipe prediction
- All inference is local via ONNX. Production interface: `ui/controllers/ml_prediction.py`.

## Working on this codebase

Detailed conventions are split by file type and live in `.claude/rules/`. **Read the relevant rule file before non-trivial changes.**

| When editing… | Read… |
|---|---|
| Any `.py` file | [`.claude/rules/python-general.md`](.claude/rules/python-general.md) — logging, imports, config, file-size cap, PostHog |
| `ui/panels/**` | [`.claude/rules/ui-panels.md`](.claude/rules/ui-panels.md) — UI only, no business logic |
| `ui/controllers/**` | [`.claude/rules/ui-controllers.md`](.claude/rules/ui-controllers.md) — threading, signals/slots |
| `services/**` | [`.claude/rules/services.md`](.claude/rules/services.md) — config, cleanup, processing pipeline order |
| `services/zwo_camera.py`, `services/camera_*.py` | [`.claude/rules/services-camera.md`](.claude/rules/services-camera.md) — BGGR debayer, exposure units, disconnect cleanup, per-camera profiles |
| `services/allsky/**` | [`.claude/rules/allsky.md`](.claude/rules/allsky.md) — calibration, coordinate frames |
| `tests/**` | [`.claude/rules/tests.md`](.claude/rules/tests.md) — pytest markers, fixtures |
| `ml/**` | [`.claude/rules/ml.md`](.claude/rules/ml.md) — ONNX inference conventions |

Two hooks enforce the most-violated rules automatically:
- `.claude/hooks/check_file_size.py` (PreToolUse) — blocks Edit/Write that would exceed the per-file size cap.
- `.claude/hooks/check_panel_purity.py` (PostToolUse) — warns when business-logic markers appear in `ui/panels/`.

Slash commands: `/audit-size`, `/audit-tests`, `/pre-commit-check`. Reviewer subagent: `pfr-reviewer`.

## Development

```powershell
# Quick start
.\start.bat

# Manual
.\venv\Scripts\Activate.ps1
python main.py

# Build
.\build_sentinel.bat              # PyInstaller exe (Python 3.13 needs an email module workaround)
.\build_sentinel_installer.bat    # Inno Setup installer
```

## Testing

```powershell
# Default — skip hardware/network tests
pytest -m "not requires_camera and not requires_network and not requires_ml_models"

# Full suite
pytest
```

| Test file | Tests | Covers |
|-----------|-------|--------|
| `test_auto_exposure.py` | 21 | `camera_utils` — brightness, clipping, exposure logic |
| `test_camera.py` | 14 | `zwo_camera` — SDK, config, debayering (3 need `requires_camera`) |
| `test_discord.py` | 32 | `discord_alerts` — webhooks, embeds (mocked) |
| `test_image_output.py` | 18 | `processor` — overlays, stretch, output formats |
| `test_settings.py` | 11 | `config` — JSON save/load, merge, defaults |
| `test_webserver.py` | 13 | `web_output` — HTTP server, ETag, status JSON (`requires_network`) |
| `test_ml_classifiers.py` | 6 | `ml.roof_classifier` / `ml.sky_classifier` + production `ui/controllers/ml_prediction.py` — ONNX load + inference smoke tests (`requires_ml_models`) |

Standalone (not in pytest suite):
- `ml/test_classifier.py` — interactive accuracy eval against a user-specific labelled dataset (walks `E:/Pier Camera ML Data`). Use this to validate a new model checkpoint, not for CI.
- `scripts/dev/test_usb_reset.py` — interactive USB reset, requires camera

## Key dependencies

| Package | Purpose |
|---------|---------|
| PySide6 6.8.1 | Qt6 bindings |
| qfluentwidgets 1.10.5 | Fluent Design components |
| opencv-python | Bayer debayering |
| Pillow | Image processing |
| watchdog | Directory monitoring |
| zwoasi | ZWO SDK wrapper |
| onnxruntime | ML inference |
| astral | Sunset/sunrise for timelapse windows |
| requests | Weather API + Discord webhooks |
| ffmpeg (external) | Timelapse + RTSP |
| PyInstaller 6.17.0 | Standalone executable |

## Active plans

- [`docs/CODE_QUALITY_PLAN.md`](docs/CODE_QUALITY_PLAN.md) — code quality + structure roadmap
- [`docs/ALLSKY_CALIBRATION_PLAN.md`](docs/ALLSKY_CALIBRATION_PLAN.md) — read before touching all-sky calibration

Developer-facing technical reference (feature design, build/release tooling, vendor SDK) lives in [`docs/dev/`](docs/dev/README.md). End-user content is on the project wiki.
