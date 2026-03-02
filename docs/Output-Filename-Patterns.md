# Output Filename Patterns

The **Filename** field in Output Settings controls what each saved image is named. By default it is `latestImage`, which overwrites the same file on every capture — useful for web dashboards and live feeds that always pull the latest image.

To build an archive of unique images, use one or more **tokens** in the filename. Tokens are replaced at save time with real values.

## Available Tokens

| Token | Example output | Description |
|-------|---------------|-------------|
| `{filename}` | `capture_001` | Original source filename, without extension |
| `{session}` | `2026-03-01` | Date the current session started (`YYYY-MM-DD`) |
| `{timestamp}` | `20260301_213045` | Exact capture time (`YYYYMMDD_HHMMSS`) |

Tokens can be combined freely with plain text and underscores.

## Examples

| Filename field | Result |
|----------------|--------|
| `latestImage` | `latestImage.jpg` — always overwritten |
| `{timestamp}` | `20260301_213045.jpg` — unique per capture |
| `{session}_{timestamp}` | `2026-03-01_20260301_213045.jpg` |
| `{filename}_{timestamp}` | `capture_001_20260301_213045.jpg` |
| `pfr_{session}` | `pfr_2026-03-01.jpg` — one file per day (overwritten each capture) |

## Notes

- The file extension is set separately by the **Format** dropdown (JPG or PNG) — do not include it in the filename field.
- `{filename}` reflects the original camera or watch-mode source file. In camera capture mode this will be the raw capture filename assigned by the driver.
- `{session}` rolls over at midnight, so a night starting before midnight and running past it will produce two session values. Use `{timestamp}` if you need a continuous unique key across midnight.
- Headless mode honours the same tokens via the same substitution logic.
