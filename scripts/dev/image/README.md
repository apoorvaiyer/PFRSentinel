# scripts/dev/image/

Ad-hoc image-processing experiments — colorization, stretching, noise modeling. None of these are called at runtime by the app.

For the production stretch/colorize pipeline, see `services/processor.py`.

| Path | Purpose |
|------|---------|
| `analyze_raw.py` | Standalone — load a FITS/TIFF and compare stretch algorithms side-by-side |
| `colorize/` | Package — reusable io/measurement/recipes/transforms helpers for colorize experiments |
| `colorize_from_lum.py` | Stretch + color tuning harness (v2.0) |
| `debug_color_fits.py` | Minimal scratch script — inspect a specific raw FITS dump |
| `noise_model_from_frames.py` | Fit a noise model from a sequence of dark frames |
| `stack_and_colorize.py` | Stack multiple FITS frames for SNR improvement, then colorize |
| `strech_recipes/` | Recipe experiments (note: folder name has a typo — "strech" not "stretch") |
| `stretch_tuer.py` | Contact-sheet tuner for luminance-driven stretch (filename has a typo — "tuer" not "tuner") |
