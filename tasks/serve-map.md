# Serve the trip map over localhost so the map tiles load

## Context
`python -m barrierfrei.plan` writes `data/plans/plan.html` and the user double-clicks it, so the
browser opens it as `file://`. OpenStreetMap's tile servers require a Referer header and a page
opened from a file sends none, so every tile is a 403 "Access blocked" image (seen on
2026-11-03). CARTO now needs an API key, so switching providers is not an option. The fix
is to serve the page from `http://127.0.0.1`, which sends a normal Referer.

## Change
1. New module `barrierfrei/serve.py`:
   - `start(directory: Path, port: int = 8765) -> tuple[ThreadingHTTPServer, str]`: binds a
     `http.server.ThreadingHTTPServer` to `127.0.0.1` (never `0.0.0.0`) serving `directory` with
     `functools.partial(SimpleHTTPRequestHandler, directory=str(directory))`. If `port` is taken
     (`OSError`), bind port `0` instead (the OS picks a free one). Returns the server and its base
     URL `f"http://127.0.0.1:{server.server_address[1]}/"`. It does not start serving.
   - The handler class overrides `log_message` to log via `logging.getLogger("barrierfrei.serve")`
     at DEBUG, so the console is not flooded with one line per tile request.
   - `serve(directory: Path, page: str = "plan.html", port: int = 8765, open_browser: bool = True) -> None`:
     calls `start`, logs `Map: <url><page>  (Ctrl+C to stop)` at INFO, opens it with
     `webbrowser.open` when `open_browser`, then `serve_forever()`; on `KeyboardInterrupt` it calls
     `server.server_close()` and returns quietly.
   - `main()`: argparse with `--dir` (default `get_settings().data_dir / "plans"`), `--port`
     (default 8765) and `--no-browser`; calls `serve`. Module docstring says:
     `Run: python -m barrierfrei.serve   (re-open the last trip map)`. Add the
     `if __name__ == "__main__": main()` guard.
2. `barrierfrei/plan.py` `main()`: add `--no-serve` (`store_true`, help "only write the files, do not
   open the map"). After everything else in `main()` (including the `--explain` print), when
   `--no-serve` is not given and `result["options"]` is not empty, call
   `serve.serve(out)`. Replace the final `log.info("Map: %s", ...)` file path line by
   `log.info("Files: %s", out.resolve())`. Add the serve command to the module docstring.
3. `barrierfrei/mapview.py`, page template: right after the tile layer is added, add JavaScript that
   shows a notice when the page is opened from a file:
   `if (location.protocol === "file:") {{ ... }}` puts a `<div>` at the top of the map panel with the
   text `The map background needs the page served: run python -m barrierfrei.serve` (a
   `L.control({{position: "topright"}})` with white background and padding is fine). Remember
   `PAGE.format(...)`: braces in the template are doubled.
4. `README.md`: in the "4. Plan a trip" row, say the map opens in the browser via a local server,
   `--no-serve` only writes the files, and `python -m barrierfrei.serve` re-opens the last map.

## Tests
New `tests/test_serve.py` (no internet; localhost only):
- `test_start_serves_the_directory`: write `plan.html` with some text in `tmp_path`, `start(tmp_path, port=0)`,
  run `serve_forever` in a daemon thread, `urllib.request.urlopen(url + "plan.html")` returns 200
  and the text; then `shutdown()` and `server_close()`.
- `test_start_binds_localhost_only`: `server.server_address[0] == "127.0.0.1"`.
- `test_start_falls_back_when_port_taken`: hold a socket bound to `127.0.0.1` on some port, call
  `start(tmp_path, port=<that port>)`, the returned server uses a different port.
- `test_serve_does_not_open_browser_when_asked`: monkeypatch `webbrowser.open` to record calls and
  the server's `serve_forever` to raise `KeyboardInterrupt`; `serve(tmp_path, open_browser=False)`
  returns without calling it; with `open_browser=True` it is called once with a URL ending in `/plan.html`.
In `tests/test_plan.py::test_summary_and_map_render`: the page contains `location.protocol === "file:"`.

## Out of scope
- No new dependencies (standard library only). Do not change routing, transit, stations or ranking.
- Do not change the tile URL.

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
