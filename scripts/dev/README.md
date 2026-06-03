# scripts/dev/

Archaeological / experimental scripts — **not shipped, not called at runtime**. These live here so `scripts/` root stays clean and reflects only the build/release/admin tools that are actively used.

Subdirectories:
- `allsky/` — fisheye calibration experiments
- `image/` — colorize / stretch / noise-model experiments

Also here:
- `test_usb_reset.py` — interactive USB reset test, requires a physical camera. Not part of the pytest suite.

For the production entry points for any of the above, see `services/` or `ui/controllers/`.
