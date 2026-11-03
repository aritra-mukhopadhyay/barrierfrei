# Fix the "Access blocked" map tiles when plan.html is opened from a file

## Context
`python -m barrierfrei.plan` writes `data/plans/plan.html` (built by `barrierfrei/mapview.py`), and
we open it straight from disk. Its Leaflet tile layer uses `https://tile.openstreetmap.org/...`.
OpenStreetMap's tile usage policy requires a Referer header; a page opened via `file://` sends
none, so every tile comes back as an "Access blocked" image.

## Change
- File: `barrierfrei/mapview.py`, the `L.tileLayer(...)` call inside the page template
  (around line 38). Remember the template is a Python f-string: JavaScript braces are doubled.
- Use CARTO's free Voyager basemap (same OSM data, no Referer requirement):
  URL `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png`,
  options `maxZoom: 20`, `subdomains: "abcd"`, attribution
  `&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors &copy; <a href='https://carto.com/attributions'>CARTO</a>`.
- Add a two-line JavaScript comment above it explaining why not OSM's own tiles.

## Tests
- In `tests/test_plan.py::test_summary_and_map_render`, assert the rendered page does not contain
  `tile.openstreetmap.org` and does contain `basemaps.cartocdn.com`.

## Out of scope
- Anything else in mapview.py or plan.py.

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
