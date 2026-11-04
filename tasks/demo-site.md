# Build a static demo site of live trip plans for GitHub Pages

## Context
Visitors need a link they can open without installing anything. A hosted live planner would need our
DB and Meta keys on a server, so instead we compute a handful of demo trips live on a local machine and
publish the resulting map pages (the same pages `plan` writes) as a static site on GitHub Pages.
Served over https, the pages send a Referer, so OpenStreetMap tiles load.

## Change
1. `barrierfrei/plan.py`: move the trip work out of `main()` so it can be reused, without changing
   what `main()` prints or writes:
   - `make_lookup(session, client_id: str, api_key: str, cache_dir: Path, outages: set[int])`
     returns the `lookup(number)` function `main()` defines today.
   - `run_trip(origin: str, destination: str, profile: str, network, provider, lookup,
     tickets: str = "regional") -> tuple[dict, str, list[Option]]`: does `place()` for both ends,
     `check_on_map()` for both, `plan()`, `summary()` plus `"from"`/`"to"`, and
     `mapview.render(...)`. Returns `(result, html, options)`. `main()` calls these two and
     keeps its logging, files, `--explain` and serving exactly as they are.
2. New module `barrierfrei/site.py`:
   - `DEMO_TRIPS`: a list of dataclass entries `DemoTrip(slug, title, origin, destination,
     profile="wheelchair", out_of_service=(), tickets="regional", note="")`, with:
     - `luisenplatz-zeil`: "Luisenplatz, Darmstadt → Zeil, Frankfurt (wheelchair)"
     - `luisenplatz-zeil-broken-lift`: same trip with `out_of_service=(10472990,)`, note
       "Simulated: the street lift at Frankfurt Taunusanlage is out of service."
     - `tu-darmstadt-staedel`: "TU Darmstadt → Städel Museum"
     - `staatstheater-alte-oper`: "Staatstheater Darmstadt → Alte Oper"
     - `darmstadtium-roemer`: "darmstadtium → Römer"
     - `luisenplatz-zeil-stroller`: the first trip with `profile="stroller"`
   - `build(out_dir: Path, trips=DEMO_TRIPS, *, network, provider, make_lookup_for, now=None) -> list[dict]`:
     for each trip, calls `plan.run_trip` with `make_lookup_for(trip.out_of_service)`, writes
     `out_dir/<slug>.html` and `out_dir/<slug>.json`, and collects an index entry: slug, title,
     note, profile, and for the first option (if any) `usable`, `fully_verified`, `minutes`,
     `leave_at`, `arrive_at`, and the first ride's line. A trip that raises `SystemExit` or
     `Exception` is logged and listed with `error` text instead of crashing the build.
     Then writes `out_dir/index.html` and returns the entries.
   - `index_html(entries, generated_at: datetime) -> str`: a self-contained page (no external
     scripts; inline CSS with an explicit white background and dark text, readable on a phone):
     title "Barrierefrei: step-free trips, checked live", one paragraph on what the project does
     (step-free walks from OpenStreetMap and street photos read by Meta's SAM 3.1 and Muse Spark,
     DB's live lift status, live DB departures), the line
     `Computed live on <YYYY-MM-DD HH:MM> (Europe/Berlin) from DB's data; lifts and trains may have changed since.`,
     then one card per trip linking to `<slug>.html` with the title, the note, and a verdict line
     (`Step-free, every lift checked` / `Step-free, some lifts can't be checked` / `No step-free trip right now`
     / the error), plus "Leave HH:MM, arrive about HH:MM" and the line when known. A link to the
     GitHub repo `https://github.com/aritra-mukhopadhyay/barrierfrei` at the bottom.
     Escape every value with `html.escape`.
   - `main()`: argparse `--out` (default `site`) and `--only <slug>...`; builds the provider and
     lookups like `plan.main()` does (live `Auto` chain, keys from settings via `require`), loads
     the network once, calls `build`. Module docstring: `Run: python -m barrierfrei.site`.
3. `.github/workflows/pages.yml`: on push to `main` touching `site/**`, and `workflow_dispatch`;
   permissions `pages: write`, `id-token: write`, `contents: read`; jobs use
   `actions/configure-pages@v5`, `actions/upload-pages-artifact@v3` with `path: site`, and
   `actions/deploy-pages@v4`. No secrets, no Python install: it only publishes the committed folder.
4. `README.md`: a short "Demo site" section: `python -m barrierfrei.site` on a machine with the keys
   and the ingested data, commit `site/`, and Pages publishes it.

## Tests
New `tests/test_site.py`, offline, reusing the fixtures/helpers style of `tests/test_plan.py`
(tiny `Network`, `transit.Static()` or a fake provider, `lookup_with`-style lookup):
- `test_build_writes_pages_and_index`: two trips whose places exist in the fake network (monkeypatch
  `plan.PLACES` or pass `lat,lon` strings), `build(tmp_path, trips, ...)` writes both `.html`/`.json`
  files and `index.html`; the index links to both slugs.
- `test_build_survives_a_failing_trip`: one trip far off the map (check_on_map raises SystemExit) is
  listed with an error and the other trip is still built.
- `test_index_escapes_and_has_no_secrets`: `index_html` with a title containing `<script>` escapes it;
  a built page and the index never contain a fake API key string passed to `make_lookup_for`'s closure.
- `test_broken_lift_trip_passes_outages`: `make_lookup_for` receives `(10472990,)` for the
  broken-lift trip and `()` for the others.
- `tests/test_plan.py`: `run_trip` returns a result with `from`/`to` and HTML containing the map.
- Existing tests keep passing unchanged.

## Out of scope
- Don't change routing, ranking, transit, stations, mapview rendering or serve.
- No new dependencies. Do not generate or commit `site/` itself (it is built locally, where the keys are).

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
