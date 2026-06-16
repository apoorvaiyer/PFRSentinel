"""
Self-documenting API: one OpenAPI spec, two ways to read it.

``build_openapi_spec()`` is the single source of truth for the HTTP API shape.
It is served verbatim at ``/openapi.json`` (machine-readable) and rendered to a
dependency-free HTML reference at ``/docs`` (human-readable, fully offline — no
CDN, no JS, suitable for an isolated observatory PC).

The ``capture`` response schema is generated from ``api_status.CAPTURE_FIELDS``,
the same catalog the live payload is built from, so the docs cannot drift from
what ``/status`` actually returns.
"""
from __future__ import annotations

import html as _html

from .api_status import CAPTURE_FIELDS

try:
    from version import __version__ as VERSION
except Exception:  # pragma: no cover - version module always present in app
    VERSION = "unknown"


def _capture_schema_properties() -> dict:
    """OpenAPI ``properties`` for the capture block, from the shared catalog."""
    props = {}
    for name, typ, desc in CAPTURE_FIELDS:
        props[name] = {"type": typ, "description": desc, "nullable": True}
    return props


def build_openapi_spec(*, image_path: str = "/latest", status_path: str = "/status",
                       docs_path: str = "/docs", openapi_path: str = "/openapi.json") -> dict:
    """Build the OpenAPI 3.0 spec describing the live server's actual routes."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "PFR Sentinel HTTP API",
            "version": VERSION,
            "description": (
                "Read-only HTTP API for PFR Sentinel. Serves the latest processed "
                "all-sky / observatory frame and a rich status report covering capture "
                "state, schedule, and health."
            ),
        },
        "paths": {
            image_path: {
                "get": {
                    "summary": "Latest processed image",
                    "description": (
                        "Returns the most recently processed frame (JPEG or PNG). "
                        "Supports ETag/If-None-Match conditional requests. Response "
                        "headers X-PFR-Image-Age-Seconds and X-PFR-Image-Stale signal "
                        "freshness without parsing /status."
                    ),
                    "responses": {
                        "200": {"description": "Image bytes",
                                "content": {"image/jpeg": {}, "image/png": {}}},
                        "304": {"description": "Not Modified (ETag matched)"},
                        "404": {"description": "No image available yet"},
                    },
                }
            },
            status_path: {
                "get": {
                    "summary": "Server, capture, and health status",
                    "description": (
                        "JSON status. Top-level keys (status, uptime_seconds, "
                        "images_served, image_age_seconds, image_stale, metadata) are "
                        "the HTTP-server view kept for backward compatibility. The "
                        "'capture' block reports mode/schedule/next-frame, and 'health' "
                        "summarises whether capture is actually working."
                    ),
                    "responses": {
                        "200": {
                            "description": "Status report",
                            "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/Status"}}},
                        }
                    },
                }
            },
            openapi_path: {
                "get": {"summary": "This OpenAPI spec (JSON)",
                        "responses": {"200": {"description": "OpenAPI 3.0 document"}}}
            },
            docs_path: {
                "get": {"summary": "Human-readable API docs (HTML)",
                        "responses": {"200": {"description": "HTML reference page"}}}
            },
        },
        "components": {
            "schemas": {
                "Status": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string",
                                   "description": "HTTP server liveness ('running'). Not capture health."},
                        "uptime_seconds": {"type": "integer"},
                        "images_served": {"type": "integer"},
                        "latest_image": {"type": "string", "nullable": True},
                        "image_age_seconds": {"type": "integer", "nullable": True},
                        "image_stale": {"type": "boolean"},
                        "stale_threshold_seconds": {"type": "integer"},
                        "metadata": {"type": "object"},
                        "timestamp": {"type": "string", "format": "date-time"},
                        "capture": {"type": "object", "properties": _capture_schema_properties()},
                        "health": {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string",
                                           "enum": ["ok", "idle", "degraded", "recovering", "error"]},
                                "reasons": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                }
            }
        },
    }


# --- HTML rendering -------------------------------------------------------

_DOCS_CSS = """
:root { color-scheme: dark light; }
body { font-family: 'Segoe UI', system-ui, sans-serif; max-width: 920px; margin: 0 auto;
       padding: 2rem 1.5rem 4rem; line-height: 1.55; background: #0f1115; color: #e6e8eb; }
h1 { font-size: 1.6rem; margin-bottom: .25rem; }
h2 { font-size: 1.15rem; margin-top: 2rem; border-bottom: 1px solid #2a2f3a; padding-bottom: .35rem; }
.sub { color: #9aa3b2; margin-top: 0; }
.endpoint { background: #161a22; border: 1px solid #242a36; border-radius: 8px;
            padding: 1rem 1.1rem; margin: 1rem 0; }
.method { display: inline-block; font-weight: 700; font-size: .75rem; letter-spacing: .05em;
          background: #1f6feb; color: #fff; border-radius: 4px; padding: .15rem .5rem; margin-right: .6rem; }
code, .path { font-family: 'Cascadia Code', Consolas, monospace; }
.path { font-weight: 600; }
table { border-collapse: collapse; width: 100%; margin-top: .6rem; font-size: .9rem; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #242a36; vertical-align: top; }
th { color: #9aa3b2; font-weight: 600; }
td.type { color: #79c0ff; white-space: nowrap; font-family: 'Cascadia Code', Consolas, monospace; }
pre { background: #0b0d12; border: 1px solid #242a36; border-radius: 8px; padding: 1rem;
      overflow-x: auto; font-size: .85rem; }
a { color: #79c0ff; }
.tag { font-size: .7rem; color: #9aa3b2; }
"""

_EXAMPLE_STATUS = """{
  "status": "running",
  "uptime_seconds": 3725,
  "images_served": 442,
  "image_age_seconds": 7,
  "image_stale": false,
  "metadata": { "EXPOSURE": "2.0s", "GAIN": "300" },
  "capture": {
    "mode": "camera",
    "enabled": true,
    "running": true,
    "state": "waiting",
    "interval_seconds": 5.0,
    "effective_interval_seconds": 5.0,
    "schedule": { "mode": "gated", "start_time": "17:00", "end_time": "09:00", "in_window": true },
    "last_capture_age_seconds": 7,
    "next_capture_in_seconds": 3,
    "recovery": { "in_progress": false, "attempts": 0, "unrecoverable": false },
    "last_error": null
  },
  "health": { "status": "ok", "reasons": [] }
}"""


def _esc(text: str) -> str:
    return _html.escape(str(text))


def render_docs_html(spec: dict) -> str:
    """Render the OpenAPI spec to a self-contained HTML reference page."""
    info = spec.get("info", {})
    title = _esc(info.get("title", "API"))
    version = _esc(info.get("version", ""))
    description = _esc(info.get("description", ""))

    endpoints_html = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            summary = _esc(op.get("summary", ""))
            desc = _esc(op.get("description", ""))
            desc_html = f'<p class="sub">{desc}</p>' if desc else ""
            endpoints_html.append(
                f'<div class="endpoint">'
                f'<span class="method">{_esc(method.upper())}</span>'
                f'<span class="path">{_esc(path)}</span>'
                f'<div><strong>{summary}</strong></div>{desc_html}</div>'
            )

    # Capture fields table from the shared catalog (via the spec's schema).
    capture_props = (
        spec.get("components", {}).get("schemas", {})
        .get("Status", {}).get("properties", {})
        .get("capture", {}).get("properties", {})
    )
    rows = []
    for name, meta in capture_props.items():
        rows.append(
            f"<tr><td class='path'>{_esc(name)}</td>"
            f"<td class='type'>{_esc(meta.get('type', ''))}</td>"
            f"<td>{_esc(meta.get('description', ''))}</td></tr>"
        )
    capture_table = (
        "<table><thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{_DOCS_CSS}</style></head>
<body>
<h1>{title} <span class="tag">v{version}</span></h1>
<p class="sub">{description}</p>
<p class="tag">Machine-readable spec: <a href="/openapi.json">/openapi.json</a></p>

<h2>Endpoints</h2>
{''.join(endpoints_html)}

<h2><code>/status</code> &rarr; <code>capture</code> fields</h2>
{capture_table}

<h2><code>health.status</code> values</h2>
<table><thead><tr><th>Value</th><th>Meaning</th></tr></thead><tbody>
<tr><td class="path">ok</td><td>Capture is running and producing fresh frames.</td></tr>
<tr><td class="path">idle</td><td>Capture is off or intentionally paused (e.g. outside the scheduled window).</td></tr>
<tr><td class="path">degraded</td><td>Capture is running but frames have stalled past the expected cadence.</td></tr>
<tr><td class="path">recovering</td><td>Auto-recovery is in progress after a camera fault.</td></tr>
<tr><td class="path">error</td><td>Capture has failed; may need manual intervention.</td></tr>
</tbody></table>

<h2>Example <code>/status</code> response</h2>
<pre>{_esc(_EXAMPLE_STATUS)}</pre>
</body></html>"""
