# PFR Sentinel — Code Quality & Structure Plan

Status: **Phases 1 + 2 + 3 complete** (2026-04-16) — Phase 4 deferred until WIP merge
Owner: Paul Fox-Reeks
Created: 2026-04-16
Last updated: 2026-04-16

Addresses findings from the 2026-04-16 codebase review. Raises the file-size cap to a realistic level, puts **machine-enforced rules** in `.claude/` (not just CLAUDE.md), closes the ML test-coverage gap, and cleans up `scripts/` sprawl.

## Status at a glance

| Phase | Status | Notes |
|-------|--------|-------|
| 1a — Cap update + CLAUDE.md / MEMORY.md trim | ✅ Complete | Rules moved from CLAUDE.md into `.claude/rules/`. CLAUDE.md is now project-description-only. MEMORY.md slimmed to project context. |
| 1b — `.claude/` scaffolding | ✅ Complete | Hooks, 8 rule files (added `allsky.md`), 3 commands, `pfr-reviewer` agent. Hook is **PreToolUse** (truly blocks) not PostToolUse. |
| 1b-i — `settings.local.json` cleanup | ✅ Complete | 65 entries → 19 wildcards. |
| 1c — `scripts/` archive + root cleanup | ✅ Complete | Dev experiments moved to `scripts/dev/{allsky,image}/`. Root utility scripts (`fix_cameras.py`, `reset_camera_sdk.py`, `test_usb_reset.py`, `analyze_raw.py`) relocated. `.bat` files + docs updated. |
| 2 — Config migration | ✅ Complete | `services/config_migrate.py` added, `Config.load()` calls it. Deprecated per-camera `zwo_*` keys removed from `DEFAULT_CONFIG` and from all production readers (`camera_controller`, `headless_runner`, `meteor_controller`, `capture_settings`). New `DEFAULT_CAMERA_PROFILE` constant seeds new profiles. `tests/test_config_migrate.py` covers migration semantics. |
| 3 — ML into pytest suite | ✅ Complete | `tests/test_ml_classifiers.py` with 6 smoke tests exercising both ONNX classifiers + the production `ui/controllers/ml_prediction.py` path. Gated behind `requires_ml_models` marker (now registered in `pytest.ini` along with `requires_network`). |
| 4 — Structural splits | ⏳ Planned | Detailed per-file breakdown added (see §4.1–§4.5 below). Items 4.1 + 4.3 can proceed on a cleanup branch now (no UI overlap); 4.2 + 4.4 wait for meteor/all-sky WIP merge. |

## Deviations from original plan

- **`globs:` frontmatter in rules is Cursor convention, not Claude Code.** `.claude/rules/*.md` files are not auto-loaded by Claude Code. Solution: CLAUDE.md contains a "When editing X, see `.claude/rules/Y.md`" table, and rule files are read on-demand by Claude. The `globs:` frontmatter is preserved as machine-readable metadata for tooling/audit.
- **Rules moved entirely out of CLAUDE.md** (per user preference). CLAUDE.md is now project description only — structure, architecture, dependencies, entry points. All conventions / rules / pitfalls live in `.claude/rules/`.
- **File-size hook switched from PostToolUse → PreToolUse.** PostToolUse runs after the write completes and can't undo it. PreToolUse evaluates content (Write) or simulates the edit (Edit) before the tool runs, so violations are truly blocked.
- **New rule file added: `.claude/rules/allsky.md`** — not in original plan, added based on doc survey (calibration grid search + coordinate frame conventions).
- **Existing rule files enriched** with conventions surfaced from docs/ + `.github/copilot-instructions.md`:
  - `services-camera.md` — disconnect cleanup pattern, per-camera profile keys, auto-exposure constants, USB reset
  - `services.md` — pipeline order (resize→overlays, LANCZOS), `_push_to_output_servers` dispatch, filename extension rule, allsky calibration JSON path/fields
  - `python-general.md` — PostHog analytics conventions
- **Reviewer subagent enriched** with the same checks (10 categories total).
- **`.github/copilot-instructions.md` deleted** (user no longer uses Copilot — was a stale duplicate of CLAUDE.md).

---

## Goals

1. **Make rules enforceable, not aspirational.** CLAUDE.md rules have drifted (e.g. `main_window.py` grew from 1565 → 1824 despite "must NOT grow further"). Fix by moving enforcement into `.claude/hooks/`.
2. **Raise the file-size cap to 750** (target 600, hard cap 750) — reflects current reality while preventing further unchecked growth.
3. **Close the ML + new-module test gap.**
4. **Reduce top-level sprawl** in `scripts/` and `services/config.py`.

---

## Phase 1 — Cap update + `.claude/` scaffolding + `scripts/` archive

Goal: rules that future Claude sessions (and the author) actually follow.

**Phase 1a + 1b status: ✅ Complete (2026-04-16). 1c (scripts archive) still pending.**

### 1a. Update size cap in CLAUDE.md + memory ✅

- **New rule**: target ≤600 lines, hard cap 750. Exceptions list frozen at current line count +5%.
- Update [CLAUDE.md](../CLAUDE.md) "Code Standards → File Size" section.
- Update `~/.claude/projects/D--Vibe-Coded-Projects-PFRSentinel/memory/MEMORY.md` — the "500 line max per file (hard cap 550)" line.

**Frozen exception list** (lines as of 2026-04-16):

| File | Current | Frozen ceiling (+5%) | Planned action |
|------|---------|---------------------|----------------|
| `ui/main_window.py` | 1824 | 1915 | Split nav/registration → Phase 4 |
| `services/processor.py` | 1355 | 1423 | Split stretch/weather/overlay → Phase 4 |
| `ui/panels/overlay_settings.py` | 1338 | 1405 | Extract CRUD → controller, Phase 4 |
| `ml/labeling_tool.py` | 1051 | 1104 | Dev tool — leave |
| `services/zwo_camera.py` | 962 | 1010 | Split SDK wrapper vs capture loop, Phase 4 |
| `ui/panels/image_processing.py` | 875 | 919 | Review candidates, Phase 4 |
| `ui/panels/capture_settings.py` | 842 | 884 | Review candidates, Phase 4 |
| `services/camera_connection.py` | 813 | 854 | Review candidates, Phase 4 |

### 1b. Build out `.claude/` directory ✅

Currently only `settings.local.json` exists. Add:

```
.claude/
├── settings.local.json          (existing — clean up permissions, see 1b-i)
├── settings.json                NEW — shared project settings + hooks, committed
├── hooks/
│   ├── check_file_size.py       NEW — blocks Write/Edit over cap
│   └── check_panel_purity.py    NEW — warns on business logic in ui/panels/
├── rules/
│   ├── python-general.md        NEW — imports, logging, no print()
│   ├── ui-panels.md             NEW — UI only, no business logic
│   ├── ui-controllers.md        NEW — business logic conventions
│   ├── services.md              NEW — threading, config access, logging
│   ├── services-camera.md       NEW — ZWO debayering, SDK rules
│   ├── tests.md                 NEW — markers, naming, fixtures
│   └── ml.md                    NEW — ONNX export, model paths
├── commands/
│   ├── audit-size.md            NEW — /audit-size slash command
│   ├── audit-tests.md           NEW — /audit-tests slash command
│   └── pre-commit-check.md      NEW — /pre-commit-check — runs before git commit
└── agents/
    └── pfr-reviewer.md          NEW — domain-aware code reviewer subagent
```

#### 1b-i. Clean up `settings.local.json` permissions

The current file has ~65 one-off permission entries accumulated over time. Consolidate to wildcards:

```json
{
  "permissions": {
    "allow": [
      "Bash(./venv/Scripts/python.exe -m pytest:*)",
      "Bash(.venv/Scripts/python.exe -m pytest:*)",
      "Bash(./venv/Scripts/python.exe -c:*)",
      "Bash(.venv/Scripts/python.exe -c:*)",
      "Bash(./venv/Scripts/python -m pytest:*)",
      "Bash(./venv/Scripts/python -c:*)",
      "Bash(venv/Scripts/pip.exe install:*)",
      "Bash(wc -l:*)",
      "Bash(xargs:*)",
      "Bash(python3:*)",
      "Bash(pip install:*)",
      "WebFetch(domain:raw.githubusercontent.com)",
      "WebFetch(domain:github.com)",
      "Read(//c/Users/Paul Fox-Reeks/.claude/**)"
    ]
  }
}
```

#### 1b-ii. `.claude/rules/` — file-type-scoped rules

Rules files use frontmatter `globs:` to scope rules to matching files. They load automatically when Claude works on those files — more targeted than CLAUDE.md, and all live in one auditable directory.

**`rules/python-general.md`** — applies to all Python files:
```markdown
---
globs: "**/*.py"
---
- Use `app_logger` for all logging — never `print()` from any module
- Relative imports within packages (`from .module import X`), absolute from outside (`from services.module import X`)
- Config access: always `config.set()` / `config.save()`, never edit JSON directly
- Config keys are nested: `output_config.get('webserver_enabled')` NOT `config.get('web_enabled')`
- Hard cap: 750 lines per file (target ≤600). Check before adding significant code.
```

**`rules/ui-panels.md`** — panels are UI only:
```markdown
---
globs: "ui/panels/**/*.py"
---
# Panel Rules — UI layout only, zero business logic
- Panels define layout, widgets, signals/slots, and display formatting
- All business logic belongs in `ui/controllers/` — panels call controller methods
- FORBIDDEN in panels: `requests`, `threading`, `subprocess`, `cv2` processing, `open()` for data files, direct network calls
- ALLOWED: Qt widgets, signal/slot wiring, layout code, display formatting, `numpy` for display scaling only
- If you need to add processing logic, create or extend a controller instead
```

**`rules/ui-controllers.md`** — business logic home:
```markdown
---
globs: "ui/controllers/**/*.py"
---
# Controller Rules — business logic lives here
- Controllers own all processing, I/O, and state management
- Communicate with panels via Qt signals/slots — never update widgets directly
- All GUI updates must go through signals or `QMetaObject.invokeMethod()` — never touch widgets from worker threads
- Heavy work runs in background threads; emit signals when done
```

**`rules/services.md`** — core services conventions:
```markdown
---
globs: "services/**/*.py"
---
# Services Rules
- All logging via `app_logger` singleton — never `print()`
- Config paths: always `app_config.get_config_dir()` — never hardcode `%APPDATA%` paths
- `cleanup.py`: only delete files (`os.path.isfile()` check) — NEVER delete folders
- Weather API: always use 10-minute cache in `weather.py` — never call API directly
- Threading: background work in threads, results via Qt signals or thread-safe queues
```

**`rules/services-camera.md`** — ZWO-specific pitfalls:
```markdown
---
globs: "services/zwo_camera.py,services/camera_*.py"
---
# ZWO Camera Rules — critical hardware constraints
- Debayering: ASI676MC uses BGGR = `cv2.COLOR_BayerBG2RGB` — NEVER use RGGB (causes red/blue swap)
- Exposure units: GUI uses milliseconds, SDK uses seconds — always convert at the boundary
- Always debayer RAW8 Bayer data before any processing — ZWO cameras output RAW8, not RGB
- SDK path: `ASICamera2.dll` in app root or custom path via Capture tab config
```

**`rules/tests.md`** — test conventions:
```markdown
---
globs: "tests/**/*.py"
---
# Test Rules
- Naming: `test_<module>.py` mirrors the module under test
- Markers: `@pytest.mark.requires_camera` for physical hardware, `@pytest.mark.requires_network` for network access, `@pytest.mark.requires_ml_models` for ONNX models
- Mock external I/O (network, disk, camera SDK) — never hit real services in unit tests
- Use `tmp_path` fixture for any file operations — never write to project directories
- Run full suite: `pytest -m "not requires_camera and not requires_network"`
```

**`rules/ml.md`** — ML module conventions:
```markdown
---
globs: "ml/**/*.py"
---
# ML Module Rules
- Models exported as ONNX — zero cloud dependency, local inference only
- Model paths resolved via `app_config.get_config_dir()` / models subdirectory
- Training data: NINA API (roof), weather API, all-sky camera + manual labels
- `ml/labeling_tool.py` is a standalone dev tool — different rules apply (no size cap enforcement)
- Community data: opt-in only, 256x256 FITS + calibration JSON
```

#### Hook 1 — File size enforcement (`.claude/hooks/check_file_size.py`)

Runs on `PostToolUse` for Edit and Write. Blocks if a file exceeds its ceiling.

```python
#!/usr/bin/env python
"""PostToolUse hook — enforce file size cap."""
import json, sys, os, re

HARD_CAP = 750
WARN_CAP = 600
EXCEPTIONS = {
    "ui/main_window.py": 1915,
    "services/processor.py": 1423,
    "ui/panels/overlay_settings.py": 1405,
    "ml/labeling_tool.py": 1104,
    "services/zwo_camera.py": 1010,
    "ui/panels/image_processing.py": 919,
    "ui/panels/capture_settings.py": 884,
    "services/camera_connection.py": 854,
}

data = json.load(sys.stdin)
path = data.get("tool_input", {}).get("file_path", "")
if not path.endswith(".py") or not os.path.exists(path):
    sys.exit(0)

# normalize to repo-relative, forward slashes
rel = os.path.relpath(path).replace("\\", "/")
with open(path, encoding="utf-8") as f:
    lines = sum(1 for _ in f)

ceiling = EXCEPTIONS.get(rel, HARD_CAP)
if lines > ceiling:
    print(f"BLOCK: {rel} is {lines} lines, exceeds ceiling {ceiling}. "
          f"Split by responsibility before adding more.", file=sys.stderr)
    sys.exit(2)
elif rel not in EXCEPTIONS and lines > WARN_CAP:
    print(f"WARN: {rel} is {lines} lines (target ≤{WARN_CAP}). "
          f"Consider splitting soon.", file=sys.stderr)
sys.exit(0)
```

#### Hook 2 — Panel purity (`.claude/hooks/check_panel_purity.py`)

Runs on `PostToolUse` for Edit/Write on `ui/panels/*.py`. Warns if business-logic markers appear.

Detection regex: `\b(requests\.|threading\.|subprocess\.|cv2\.|np\.|websocket\.|ThreadPoolExecutor|asyncio\.|open\()`

Warning only (exit 0), not block — some legitimate uses exist (e.g. `np` for display scaling in `live_monitoring.py`).

#### settings.json (shared)

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "python .claude/hooks/check_file_size.py" },
          { "type": "command", "command": "python .claude/hooks/check_panel_purity.py" }
        ]
      }
    ]
  }
}
```

#### Slash commands (examples)

- `/audit-size` — run `wc -l` over `services/ ui/ ml/`, flag any file over its ceiling, print a markdown table.
- `/audit-tests` — list `.py` files in `services/` and `ml/` with no matching `tests/test_*.py`.
- `/pre-commit-check` — runs `/audit-size` + `pytest -q -m "not requires_camera and not requires_network"` + `git diff --stat`.

### 1c. Archive `scripts/` + root cleanup ✅

Current `scripts/` mixes shipped build tools with ad-hoc experiments.

**Keep at root** (shipped / still actively called):
- `update_version.py`, `upload_to_virustotal.py`, `wiki-push.sh`, `Connect-SimplySign.ps1`, `create_icon.py`, `build_hip_coords.py`

**Move to `scripts/dev/allsky/`** (one-off calibration experiments):
- `allsky_debug.py`, `allsky_gridcal.py`, `allsky_manual_cal.py`, `allsky_multi.py`, `allsky_multi_cal.py`, `allsky_rectify.py`, `allsky_v5_cal.py`, `validate_calibration.py`, `analyze_calibration_data.py`, `analyze_modes.py`, `backfill_calibration.py`, `plate_solve_export.py`

**Move to `scripts/dev/image/`** (colorize/stretch experiments):
- `colorize/`, `colorize_from_lum.py`, `stack_and_colorize.py`, `strech_recipes/`, `stretch_tuer.py`, `debug_color_fits.py`, `noise_model_from_frames.py`

Add a one-paragraph `README.md` per `scripts/dev/*/` subdir explaining each tool's purpose. The goal: a new contributor can see at a glance what's shipped vs. archaeological.

**Additional scope (not in original plan):** also cleaned up the project root, which had drifted. Moved these out of root:

- `fix_cameras.py` + `fix_cameras.bat` → `scripts/` (rule files already pointed to this path)
- `reset_camera_sdk.py` + `reset_camera_sdk.bat` → `scripts/`
- `test_usb_reset.py` → `scripts/dev/` (marked "interactive, requires camera" — dev tool)
- `analyze_raw.py` → `scripts/dev/image/` (standalone stretch analysis tool)

Kept at root (core modules / entry points / launchers): `main.py`, `app_config.py`, `utils_paths.py`, `logging_config.py`, `version.py`, `start.bat`, `build_sentinel.bat`, `build_sentinel_installer.bat`, `run_tests.bat`, `PFRSentinel.spec`, `version.iss`.

Fix-ups required: `sys.path` shims in moved scripts updated to point at project root (parent of `scripts/`); `.bat` files updated with `cd /d "%~dp0\.."` and qualified script paths; docs (`PER_CAMERA_SETTINGS.md`, `CAMERA_USB_RESET.md`, `CLAUDE.md`) updated.

---

## Phase 2 — Config migration ✅ Complete

Eliminated the deprecated per-camera `zwo_*` keys. Old user configs are migrated silently at load time; production code now reads per-camera settings only from `camera_profiles[clean_name]`.

### What was done (vs original plan)

| Planned | Actual |
|---------|--------|
| Create `config_migrate.py` | ✅ `services/config_migrate.py` with `migrate_legacy_camera_keys()` |
| Call from `config.load()` | ✅ wired in `Config.load()` after JSON read, before DEFAULT merge |
| Delete deprecated block | ✅ 9 per-camera keys + `zwo_auto_wb` (dead — replaced by `white_balance.mode`) removed |
| Add migrate test | ✅ `tests/test_config_migrate.py` — 10 tests covering: no-op, fold semantics, idempotency, existing-profile-wins, dead-key stripping, global-key preservation, end-to-end load-time |

### Scope that grew beyond the original plan

The plan assumed removing the deprecated `DEFAULT_CONFIG` entries was sufficient, but production code still **read** those keys (as fallbacks or directly) in 4 more files. All were updated to read from the camera profile instead:

- `ui/controllers/camera_controller.py` — removed dual-write that mirrored profile values to top-level `zwo_*` (the source of the "cross-contamination" that `fix_cameras.py` exists to repair) plus the fallback reads
- `ui/panels/capture_settings.py` — `load_config()` now reads from the active camera's profile
- `services/headless_runner.py` — new `_active_profile()` helper, all camera init args read from profile
- `ui/controllers/meteor_controller.py` — `_get_exposure_sec()` reads from profile

### New canonical data model

- **Per-camera (in `camera_profiles[clean_name]`)**: `exposure_ms`, `gain`, `max_exposure_ms`, `target_brightness`, `wb_r`, `wb_b`, `offset`, `flip`, `bayer_pattern`
- **Global (top-level)**: `zwo_interval`, `zwo_auto_exposure`, `zwo_sdk_path`, `zwo_camera_index`, `zwo_camera_name`, `zwo_selected_camera`, `zwo_selected_camera_name`
- **`DEFAULT_CAMERA_PROFILE`**: new module-level constant in `services/config.py`, used by both `get_camera_profile()` seeding and as the fallback dict throughout production code

### Test results

`pytest -q -m "not requires_camera and not requires_network and not requires_ml_models"`:
- **319 passed**, 1 pre-existing failure in `test_allsky_calibration.py::test_synthetic_bright_stars` (verified unrelated to Phase 2 — reproducible on unmodified `main`).

---

## Phase 3 — ML into pytest suite ✅ Complete

ML now has coverage in the pytest suite — silent drift will be caught.

### What was done (vs original plan)

| Planned | Actual |
|---------|--------|
| Create `tests/test_ml_classifiers.py` with assertions | ✅ 6 tests across 3 classes |
| Gate behind `requires_ml_models` marker | ✅ Module-level `pytestmark` + per-test `_require(path)` helper that pytest-skips if the model file is absent |
| Register the marker in `pytest.ini` | ✅ added both `requires_ml_models` and `requires_network` (the latter was in use but unregistered) |
| Update CLAUDE.md testing table | ✅ new row + note about why `ml/test_classifier.py` stays standalone |
| Keep `ml/test_classifier.py` | ✅ untouched — it's an interactive accuracy eval against a user-specific labelled dataset at `E:/Pier Camera ML Data`, not a unit test |

### Test design

The 6 tests are **smoke tests**, not accuracy tests. Their job is to catch regressions in:

- ONNX model load + inference session creation
- Input preprocessing (image shape/dtype, metadata vector shape)
- Output contract (return types, confidence bounds, probability-sum invariant, expected dict keys for the production API)

Per-class coverage:
- `TestRoofClassifierONNX` — direct `ml.roof_classifier.RoofClassifier` exercise (2 tests)
- `TestSkyClassifierONNX` — direct `ml.sky_classifier.SkyClassifier` exercise (2 tests)
- `TestProductionPredictionAPI` — `ui/controllers/ml_prediction.py` public interface, with dev-mode gate mocked on via `monkeypatch` and classifier singletons reset per test (2 tests)

Accuracy regression remains the job of `ml/test_classifier.py` (standalone, manual).

### Test results

`pytest tests/test_ml_classifiers.py -m requires_ml_models` — **6 passed**.
Default CI (`pytest -m "not requires_camera and not requires_network and not requires_ml_models"`) — **319 passed** (unchanged from Phase 2), all 6 ML tests cleanly deselected.

---

## Phase 4 — Structural splits ⏳ Pending

Detailed analysis completed 2026-04-16. Sizes re-verified against current branch `claude/meteor-all-sky-overlay-nJlps`.

### Branch strategy

`overlay_settings.py` and `main_window.py` are the two files most likely to conflict with the active meteor/all-sky WIP since that work touches UI. Two safe sequencing options:

- **A (recommended)**: land `processor.py` and `zwo_camera.py` now on a separate branch — these have minimal UI overlap with the meteor WIP. Defer `overlay_settings.py` and `main_window.py` until WIP merges.
- **B**: wait for full WIP merge, then do all four in sequence on a single cleanup branch.

Each split: measure before/after, update exception ceilings in `.claude/hooks/check_file_size.py`, update relevant `.claude/rules/` files that reference moved symbols, add a short migration note to the new module docstrings.

---

### 4.1 — `services/processor.py` (1355 → ~300) **START HERE**

Lowest-risk split: all free functions, no class state. Four clean clusters.

| Cluster | Lines | Action | New home |
|---|---|---|---|
| Auto-stretch algorithms (`mtf_stretch`, `auto_stretch_image`, `_normalize_channel_medians`, `_apply_scnr`, `_stretch_linked_rgb`, `_stretch_channel`, `_calculate_mtf_midtone`) | ~500 | Move | `services/stretch.py` |
| PIL overlay drawing (`add_overlays`, `add_text_overlay`, `add_image_overlay`, `_add_compass_overlay`, `parse_color`, `get_text_bbox`, `calculate_position`) | ~450 | Move | `services/overlay_draw.py` |
| Sidecar parsing + token replacement (`parse_sidecar_file`, `derive_metadata`, `replace_tokens`) | ~70 | Move | `services/metadata.py` |
| Pipeline orchestration (`is_safe_path`, `save_image_atomic`, `build_output_filename`, `_inject_allsky_metadata`, `process_image`) | ~300 | Stay | — |

**After:** `processor.py` ~300 lines, three new modules each ≤500.

**Coupling notes:**
- `add_overlays` is the dual-input entry point. It stays reachable through `services/processor` via re-export — keeps `.claude/rules/services.md` "don't add a second entry point" intact.
- `replace_tokens` is called by overlay_draw; metadata module can stay independent or merge into overlay_draw if tight.

**Follow-up docs to update:**
- `.claude/rules/services.md` — "image processing pipeline order" section references `services/processor.py`. Update if entry-point wording needs clarification.

Estimated effort: **2 hours**.

---

### 4.2 — `ui/panels/overlay_settings.py` (1338 → ≤600)

Single class, ~50 methods. Four distinct responsibilities.

| Cluster | Lines | Action | New home |
|---|---|---|---|
| Preview rendering (`_render_overlay`, `_render_text_overlay`, `_render_compass_overlay`, `_render_image_overlay`, `_anchor_to_xy`, `_substitute_tokens`, `_generate_preview_background`, `_update_preview`) | ~300 | Move | `ui/renderers/overlay_preview.py` (new subfolder) |
| Overlay list CRUD + file I/O (`_add_text_overlay`, `_add_image_overlay`, `_add_compass_overlay`, `_duplicate_overlay`, `_delete_overlay`, `_browse_image`, `_save_overlays`, `load_from_config`) | ~250 | Move | `ui/controllers/overlay_editor_controller.py` |
| Three editor widget trees (`_create_text_editor`, `_create_image_editor`, `_create_compass_editor`) | ~200 | Move | `ui/widgets/overlay_editors/{text,image,compass}_editor.py` |
| Panel layout + thin signal routing (`_setup_ui`, `_create_list_card`, `_create_preview_card`, `_create_editor_card`, `_refresh_list`, `_on_*` handlers, `_load_overlay_to_editor`, `_block_all_signals`, `resizeEvent`) | ~550 | Stay | — |

**After:** panel ≤600 lines, one new controller, one new renderer, three new editor widgets.

**Coupling notes:**
- Preview renderer uses Qt types (`QPainter`, `QFont`, `QImage`) so it can't live in `services/` (rules: services Qt-free). `ui/renderers/` is the right folder.
- Controller owns the overlay list state; panel emits "user added/deleted/selected" signals.
- Editor widgets are self-contained Qt widget trees; panel composes them in `_create_editor_card`.

Estimated effort: **3 hours** (most of it is signal wiring).

---

### 4.3 — `services/zwo_camera.py` (962 → ~540 via Option A)

`ZWOCamera` class, ~35 methods. Two split options:

**Option A — extract capture-loop functions** (RECOMMENDED — low risk, fast):
- Pull `capture_single_frame` (137 lines) and `capture_loop` (282 lines) into `services/camera_capture_loop.py` as module-level functions that take a `ZWOCamera` instance.
- Drops `zwo_camera.py` to ~540 lines.

**Option B — SDK/capture class split** (clean, higher risk, follow-up):
- New `services/camera_sdk.py` — `ZWOSdkClient` wrapping `initialize_sdk`, `detect_cameras`, `connect_camera`, `disconnect_camera`, `reconnect_camera_safe`, `_configure_camera`, `set_raw16_mode`.
- `ZWOCamera` composes the SDK client; capture + exposure stays.

Start with A. Consider B later only if the seam still feels muddy.

**Coupling notes:**
- `.claude/rules/services-camera.md` mandates the disconnect safety net (`__del__`, `__enter__/__exit__`, `_cleanup_lock`) stays on `ZWOCamera`. These cannot move.
- 10-second thread join timeout in `stop_capture` — preserve across the split.

Estimated effort: **1 hour** (Option A). Option B adds ~2 more.

---

### 4.4 — `ui/main_window.py` (1824 → ~1100 after pass 1)

Most invasive file. The original plan's "split nav into `main_window_nav.py`" is too small — nav is only ~60 lines. Need to push coordination responsibilities out to controllers.

**Pass 1 — four extractions:**

| Cluster | Lines | Action | New home |
|---|---|---|---|
| Discord integration (`_on_test_discord`, `_send_discord_startup` / `_error` / `_shutdown` / `_capture_started` / `_periodic_update`, `_on_camera_error`) | ~200 | Move | `ui/controllers/discord_coordinator.py` |
| Output server lifecycle (`_start_web_server`, `_stop_web_server`, `_ensure_output_servers_started`, `_push_to_output_servers`) | ~150 | Move | Extend existing `ui/controllers/output_controller.py` |
| Update checker UI glue (`_init_update_checker`, `_do_startup_update_check`, `_on_update_available`, `_handle_update_available`, `_show_update_dialog`, `check_for_updates_now`) | ~80 | Move | `ui/controllers/update_controller.py` |
| UI/window construction (`_setup_window`, `_setup_ui`, `_apply_styles`, `set_accent_theme`, `_restore_splitter_sizes`, `_on_splitter_moved`, `_on_nav_changed`) | ~300 | Move | `ui/main_window_builder.py` — free functions called from `MainWindow.__init__` |

**After pass 1:** `MainWindow` ~1100 lines. Still over the 750 cap; exception ceiling drops from 1915 to ~1200.

**Pass 2 (follow-up)** — push capture lifecycle (`start_capture`, `stop_capture`, `_start_camera_capture`, `_start_watch_mode`, `_update_start_button`) and image-pipeline glue (`on_image_captured`, `_do_reprocess`, `_on_image_processed`, etc.) into existing `capture_controller` / `image_processor` to reach ≤750.

Estimated effort: **4-6 hours** across both passes.

---

### 4.5 — `ml/labeling_tool.py` (1051) — **SKIP**

Standalone dev tool, not in production. Per `.claude/rules/ml.md`: has its own size budget, doesn't follow the panel/controller split. Not worth the refactor risk for a tool that isn't shipped.

If ever revisited: extract FITS/JPEG loaders into `ml/labeling_io.py`, model invocation into `ml/labeling_model.py`.

---

### Proposed execution order

| Order | File | Risk | Hours | UI overlap with meteor WIP? |
|-------|------|------|-------|------------------------------|
| 1 | `services/processor.py` | Low | 2 | No |
| 2 | `services/zwo_camera.py` (Option A) | Low | 1 | No |
| 3 | `ui/panels/overlay_settings.py` | Medium | 3 | Possible |
| 4 | `ui/main_window.py` (pass 1) | High | 4-6 | Yes |
| (5) | `ml/labeling_tool.py` | — | — | Skipped |

**Total:** ~10-12 hours of focused work.

Per branch strategy: items 1 and 2 can land now on a cleanup branch. Items 3 and 4 wait for meteor WIP merge.

---

## Out of scope (for now)

- Rewriting panels/controllers to a cleaner MVVM split — too invasive, revisit once Phase 4 lands.
- Moving to `pyproject.toml` / removing `requirements.txt` — orthogonal, not blocking.
- CI setup (GitHub Actions running pytest on PRs) — valuable but separate workstream.
- Type annotations / mypy — would surface real bugs but is a multi-week effort.

---

## Rollout

Execute strictly in order. Don't start Phase 4 splits until current WIP branch is merged to main.

| Phase | Effort | Risk | Blocking? |
|-------|--------|------|-----------|
| 1 | ~2 hrs | Low — no runtime changes | No |
| 2 | ~30 min | Low — covered by migration test | No |
| 3 | ~1 hr | Low — new tests only | No |
| 4 | 1–2 days | Medium — touches large files | Wait for WIP merge |

**Total Phase 1-3: ~3.5 hours of focused work.**

## Success criteria

- [x] `check_file_size.py` blocks any new file exceeding its ceiling (verified via smoke test 2026-04-16)
- [x] `.claude/rules/` has rule files (now 8: added `allsky.md`). Each is short and scoped.
- [x] `settings.local.json` permissions consolidated (65 entries → 19 wildcards)
- [x] `services/config.py` has zero `DEPRECATED` comments
- [x] `pytest -q` includes ML classifier coverage via `tests/test_ml_classifiers.py` (opt-in via `-m requires_ml_models`)
- [x] `scripts/` root has build/release/admin tools only — dev experiments moved to `scripts/dev/{allsky,image}/`. Project root also cleaned (utility scripts moved out).
- [x] `/audit-size` slash command exists (`.claude/commands/audit-size.md`)
- [x] `pfr-reviewer` subagent exists (`.claude/agents/pfr-reviewer.md`)
- [x] `.github/copilot-instructions.md` removed (no longer used)
