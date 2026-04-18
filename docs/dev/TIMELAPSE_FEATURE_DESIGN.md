# Timelapse Feature Design

## Scope

**Camera capture mode only.** This feature is not available in directory watch mode.
Rationale: the camera pipeline gives us precise control over every frame, consistent image sizing,
and a clean hook point before/after overlay rendering. Watch mode images arrive unpredictably
from external software and may vary in size, format, and timing.

---

## Overview

Daily timelapse video generation built into the ZWO camera capture pipeline. Each captured frame
is optionally written into a growing video file for the current session window. No intermediate
frame files are stored — frames are piped directly into a long-running ffmpeg subprocess.

At the end of each session window the video is finalized and a new one starts the next session.

---

## Navigation Placement — Own Nav Item

Timelapse is **not** added to the Output settings panel. It gets its own nav rail item.

**Why not Output settings:**
The Output panel presents File / Web / Discord as modes to choose between — the framing encourages
picking one. Timelapse is a different category: a background archival recording that runs
alongside whatever output modes you have enabled. Adding it there creates a false equivalence
and buries it inside a panel with an already-opinionated layout.

**New nav item — between Overlays and Logs:**

```
[Live Monitoring]
[Capture]
[Output]
[Image Processing]
[Overlays]
[Timelapse]          ← new
[Logs]
──────────────────
[Settings]
```

The timelapse nav item gets a `FluentIcon.VIDEO` icon and opens a dedicated full-panel
`TimelapsePanel` in the inspector stack — same pattern as every other panel.

### Changes to `nav_rail.py`

Add one entry to `nav_items`:

```python
nav_items = [
    (FluentIcon.VIEW,      "Live Monitoring", 'monitoring'),
    (FluentIcon.CAMERA,    "Capture",         'capture'),
    (FluentIcon.CLOUD,     "Output",          'output'),
    (FluentIcon.PHOTO,     "Image Processing",'processing'),
    (FluentIcon.FONT,      "Overlays",        'overlays'),
    (FluentIcon.VIDEO,     "Timelapse",       'timelapse'),   # ← new
    (FluentIcon.HISTORY,   "Logs",            'logs'),
]
```

### Changes to `main_window.py`

```python
# In _setup_ui():
self.timelapse_panel = TimelapsePanel(self)
self.inspector_stack.addWidget(self.timelapse_panel)   # Index 6
# logs_panel moves to Index 7, settings_panel to Index 8

# In _on_nav_changed():
panel_map = {
    'capture':    0,
    'output':     1,
    'processing': 2,
    'overlays':   3,
    'timelapse':  6,   # ← new
    'logs':       7,
    'settings':   8,
}
```

---

## ffmpeg — Optional External Tool, With In-App Install

**ffmpeg is optional.**
The application does not ship ffmpeg. The user must install it separately.

### `is_ffmpeg_available()` Already Exists

It's currently in `ui/panels/settings_panel.py`. As part of this feature, move it to a new
shared module `services/ffmpeg_utils.py` (~15 lines) so `TimelapsePanel` and `settings_panel.py`
can both import it without a circular dependency.

### In-App Installation Helper

Rather than just showing "not found — go figure it out", the Timelapse panel can offer a
one-click install via **winget** (Windows Package Manager), which is built into Windows 11
and modern Windows 10. This is the correct, official way to install software on Windows.

**winget install command:**
```
winget install Gyan.FFmpeg --source winget --accept-package-agreements --accept-source-agreements --silent
```

This installs ffmpeg system-wide, adds it to PATH, and is idempotent (safe to run again).

**UI flow when ffmpeg is missing:**

```
┌─ Timelapse ──────────────────────────────────────────────────────────┐
│                                                                       │
│  ffmpeg is required for timelapse recording.                         │
│  It is free, open-source, and widely used.                           │
│                                                                       │
│  [  Install via winget (recommended)  ]   [  Download manually  ]   │
│                                                                       │
│  winget is Windows' built-in package manager.                        │
│  "Download manually" opens ffmpeg.org in your browser.              │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

**After clicking "Install via winget":**

```
┌─ Installing ffmpeg ───────────────────────────────────────────────────┐
│                                                                       │
│  Running: winget install Gyan.FFmpeg                                  │
│                                                                       │
│  ████████████████░░░░░░░░░░░░  Downloading...                        │
│                                                                       │
│  [  Cancel  ]                                                         │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

Once winget exits successfully, re-probe with `is_ffmpeg_available()`. If it returns True,
replace the install prompt with the full settings UI and log `ffmpeg installed successfully`.
If it fails (winget not available, or user denied UAC), show the error and fall back to the
manual download link.

**Implementation:** A `QThread` subclass runs the winget subprocess, emitting progress signals
back to the UI. `QProgressBar` in indeterminate mode while running (winget's --silent flag
suppresses output we could parse). On completion, emit success/failure with the returncode.

**Winget availability check:**
```python
def is_winget_available() -> bool:
    try:
        result = subprocess.run(['winget', '--version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False
```

If winget itself isn't available (very old Windows 10), show only the manual download button.

---

## Space Efficiency: The Pipe Approach

### Why NOT store individual frames

The naive approach — save every processed image, encode at the end — has real problems:
- 1 frame/minute × 12 hours = 720 files
- At full resolution JPEG (~500 KB each) → **360 MB per night, every night**
- Disk fills fast, existing cleanup.py would conflict with it

### Additive ffmpeg Pipe (Recommended)

A long-running `ffmpeg` subprocess reads raw frame bytes from its stdin pipe and writes a
growing video file. ffmpeg encodes continuously — **no intermediate files at all**.

- Output is valid even if the process is killed mid-session (fragmented MP4 survives)
- Final video: ~10–30 MB for a 12-hour night (H.264 CRF 23, 1080p)
- No changes to existing cleanup.py — timelapse videos are small and stored separately

### Frame Timing

The timelapse receives a frame on every camera capture — there is no separate timelapse
capture interval. Whatever interval is set in Capture Settings (e.g. every 300 seconds)
is the rate at which the timelapse accumulates frames. This keeps configuration simple
and avoids any conflict between two competing timing settings.

**Space summary (example: 300s capture interval, 12-hour night = 144 frames):**

| Scenario | Timelapse Frames | Video Duration (24fps) | Approx Size |
|----------|-----------------|----------------------|-------------|
| 300s interval, 8hr night | 96 | 4 sec | ~2 MB |
| 300s interval, 12hr night | 144 | 6 sec | ~3 MB |
| 60s interval, 12hr night | 720 | 30 sec | ~12 MB |
| 30 nights of above | — | ~15 min total | ~360 MB |

vs. storing raw JPEG frames: 720 × 500 KB = **360 MB per single night**.
The pipe approach is ~30× more space-efficient.

---

## Overlays: Include or Exclude?

### The Concern

Overlays contain per-frame data: timestamp, exposure, gain, temperature, weather. When played back
at 24fps with 1 frame/minute decimation, the overlay text changes 24 times per second — rapidly
cycling through all values captured during the night.

- **Timestamp**: flickers visibly through all the minutes of the session
- **Exposure/Gain**: shifts as auto-exposure adapts — can look interesting as data-in-motion
- **Temperature/Weather**: changes slowly, barely noticeable in playback

### The Solution: `include_overlays` Setting

```
include_overlays = False  →  raw debayered image (clean, no text)   [default]
include_overlays = True   →  fully processed image with all overlays applied
```

### Hook Point in the Pipeline

```python
# In main_window.py camera capture callback

raw_image = pil_image_from_camera           # debayered, no overlays
processed_image = processor.add_overlays(raw_image, overlays, metadata)

# Existing: push to outputs
_push_to_output_servers(processed_image, metadata)

# New: timelapse hook
if self.timelapse_writer and self.timelapse_writer.enabled:
    cfg = self.config.get('timelapse', {})
    frame = processed_image if cfg.get('include_overlays', False) else raw_image
    self.timelapse_writer.add_frame(frame)
```

No changes to `processor.py`. The raw image reference is already in scope before the processor
call — it just needs to be forwarded.

---

## Session Windows

The timelapse records during a configured window each day. Three modes:

### 1. Fixed Times

User picks start and end wall-clock times. E.g. `18:00 → 06:00` (crossing midnight is supported).

### 2. Sun-Based (Recommended Default)

Uses the `astral` library (already in the project via the ML module):

| Sun Mode | Sun Angle | Typical Use |
|----------|-----------|-------------|
| `sunset_sunrise` | 0° | Full session incl. twilight |
| `civil` | -6° | Dusk-to-dawn wide window |
| `nautical` | -12° | Excludes bright twilight |
| `astronomical` | -18° | True darkness only |

Location pulled from `weather.location` config (already parsed to lat/lon). Falls back to
fixed-time mode with a warning if no location is configured.

### 3. Always On

Records continuously, splitting files at midnight.

---

## Session Boundary Handling

| Event | Action |
|-------|--------|
| Window opens / capture starts within window | Start ffmpeg, open `timelapse_YYYYMMDD.mp4` |
| Frame arrives within window | Time-gate check → pipe frame bytes if eligible |
| Window closes | Close ffmpeg stdin → file finalized naturally |
| Next day window opens | New file, new ffmpeg process |
| App shutdown mid-session | Graceful stdin close; fragmented MP4 playable up to last GOP |
| Resolution changes mid-session | Stop + restart ffmpeg with new frame size |

Filename date = session **start** date. An 18:00–06:00 session starting on the 1st →
`timelapse_20260301.mp4`.

---

## Output Format

**Fragmented MP4** (`-movflags frag_keyframe+empty_moov+default_base_moof`) — playable even
if ffmpeg is killed mid-session. Compatible with all players.

### ffmpeg Command

```bash
ffmpeg \
  -f rawvideo -pixel_format rgb24 \
  -video_size {width}x{height} \
  -framerate 1 \
  -i pipe:0 \
  -c:v libx264 -crf {crf} -preset {preset} \
  -r {playback_fps} \
  -pix_fmt yuv420p \
  -movflags frag_keyframe+empty_moov+default_base_moof \
  -y {output_path}
```

`-framerate 1` input + `-r 24` output = 24× time compression. With a 300s capture interval,
one capture = one frame = 1/24 sec of video → 12 hours of night ≈ 6 seconds of timelapse.

---

## Implementation Plan

### New File: `services/ffmpeg_utils.py` (~20 lines)

```python
"""Shared ffmpeg and winget availability checks."""
import subprocess

def is_ffmpeg_available() -> bool:
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False

def is_winget_available() -> bool:
    try:
        result = subprocess.run(['winget', '--version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False
```

### New File: `services/timelapse_writer.py` (~200 lines)

```python
class TimelapseWriter:
    def __init__(self):
        self.process = None           # ffmpeg subprocess
        self.last_frame_time = None   # wall-clock time of last piped frame
        self.session_date = None      # date stamp of current file
        self.session_start = None     # datetime when session opened
        self.frame_size = None        # (width, height)
        self.frame_count = 0
        self.frame_lock = threading.Lock()

    def start_session(self, frame_size: tuple) -> bool: ...
    def stop_session(self): ...
    def add_frame(self, image: Image.Image) -> bool: ...
    def get_status(self) -> dict: ...
    def _is_in_window(self) -> bool: ...
    def _get_window_for_today(self) -> tuple: ...
    def _build_output_path(self) -> str: ...
    def _build_ffmpeg_cmd(self) -> list: ...
```

### New File: `ui/panels/timelapse_panel.py` (~300 lines)

Full-panel layout with:
- ffmpeg status check on load
- winget install flow (QThread + QProgressBar) when missing
- Session window settings (mode selector, fixed times, sun mode dropdown)
- Frame/playback settings
- Include overlays toggle
- Output directory + keep N days
- Live status row (recording indicator, frame count, elapsed, filename)

### New File: `ui/controllers/timelapse_controller.py` (~100 lines)

Thin controller owning the `TimelapseWriter` instance, handling start/stop on capture
start/stop events, and providing status back to the panel.

### Modifications

| File | Change |
|------|--------|
| `services/ffmpeg_utils.py` | **Create** — move `is_ffmpeg_available()` here from `settings_panel.py` |
| `services/timelapse_writer.py` | **Create** |
| `ui/panels/timelapse_panel.py` | **Create** |
| `ui/controllers/timelapse_controller.py` | **Create** |
| `services/config.py` | Add `timelapse` block to `DEFAULT_CONFIG` |
| `ui/components/nav_rail.py` | Add `(FluentIcon.VIDEO, "Timelapse", 'timelapse')` entry |
| `ui/main_window.py` | Import + instantiate panel and controller; update nav→stack index map; add timelapse hook in camera capture callback; shutdown on close |
| `ui/panels/settings_panel.py` | Update `is_ffmpeg_available` import to use `services.ffmpeg_utils` |

All files within the 500-line limit.

---

## Config Keys

```json
{
  "timelapse": {
    "enabled": false,
    "window_mode": "sun",
    "fixed_start": "18:00",
    "fixed_end": "06:00",
    "sun_mode": "astronomical",
    "sun_latitude": null,
    "sun_longitude": null,
    "playback_fps": 24,
    "video_crf": 23,
    "video_preset": "fast",
    "include_overlays": false,
    "output_dir": "",
    "max_videos_to_keep": 30
  }
}
```

---

## Dependencies

| Dependency | Status | Notes |
|-----------|--------|-------|
| `ffmpeg` | **Optional external binary** | In-app install via winget. Card disabled + install prompt if absent. |
| `winget` | **Built into Windows 11** | Used for in-app ffmpeg install. Falls back gracefully if unavailable. |
| `astral` | Already in `.venv` | Sun/moon ephemeris for window calculation |
| `Pillow` | Already present | Frame conversion |
| `numpy` | Already present | PIL → bytes |

**No new pip packages required.**

---

## Edge Cases & Risks

| Scenario | Mitigation |
|----------|-----------|
| ffmpeg not installed | Show install prompt with winget + manual options |
| winget not available | Hide winget button, show manual download only |
| winget install fails (UAC denied, no internet) | Show error message, show manual download link |
| Camera mode not active | Hook only in camera callback — watch mode never calls `add_frame()` |
| Resolution changes mid-session | Stop current ffmpeg (partial video valid), restart with new size |
| App crashes mid-session | Fragmented MP4 playable up to last written GOP |
| Location not set for sun mode | Warn in UI, fall back to fixed-time mode |
| `include_overlays = True` + text cycling | Expected — tooltip in panel explains it |

---

## Future Enhancements (Post-MVP)

- **Discord post on session end**: post finished video / thumbnail via `discord_alerts.py`
- **Speed presets**: "10×  30×  60×  300×" instead of raw fps/interval numbers
- **Progress thumbnail**: preview of in-progress timelapse in Monitoring panel
- **Monthly compilation**: ffmpeg concat demuxer across all `timelapse_*.mp4` for a month
- **Retroactive build**: "Build from today's output folder" using ffmpeg `image2` input
- **GPU encoding**: optional `-c:v h264_nvenc` / `-c:v h264_amf` for faster encoding
- **Bundle in installer**: optional ffmpeg component in Inno Setup for offline installs
