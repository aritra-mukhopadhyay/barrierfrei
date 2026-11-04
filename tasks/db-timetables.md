# Use DB's Timetables API for live train departures, with the existing fallbacks

## Context
`barrierfrei/plan.py` asks a transit provider (`barrierfrei/transit.py`) for journeys between
two stops. Today the live provider is `DbRest` (the free db.transport.rest), which has been down
for days, so `Auto` always falls back to `Static` (typical times, no real departures).
The project's DB API Marketplace app is subscribed to DB's **Timetables** API (free plan,
60 calls/minute). The same credentials already used for FaSta work for it:
`DB_CLIENT_ID` and `DB_API_KEY` in `Settings`, sent as the headers `DB-Client-Id` and `DB-Api-Key`
(see `barrierfrei/stations.py` around line 93).

Timetables is station-centric XML (the IRIS format). Base URL:
`https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1`
- `GET /station/{pattern}` returns `<stations><station name="..." eva="..." .../></stations>`.
- `GET /plan/{eva}/{YYMMDD}/{HH}` returns one hour of planned stops:
  `<timetable station="Darmstadt Hbf"><s id="..."><tl c="S" n="35321" .../>
  <ar pt="2611031215" pp="2" l="3" ppth="A|B"/><dp pt="2611031216" pp="2" l="3" ppth="C|D|E"/></s>...`
  `pt` is the planned time `YYMMDDHHmm` in German local time, `pp` the planned platform, `l` the line
  number, `ppth` the stations still to come (for `dp`) or already passed (for `ar`), `|`-separated.
- `GET /fchg/{eva}` returns current changes for the same `s` ids: `ct` changed time, `cp` changed
  platform, `cpth` changed path, `cs="c"` cancelled. Any attribute may be missing.

## Change
In `barrierfrei/transit.py`:

1. Add a provider class `DbTimetables(session, client_id: str, api_key: str, timeout: float = 8)`
   with the same `journeys(origin, destination, results=3, departure=None) -> list[Journey]`
   signature as `DbRest` and `Static`. It returns **direct trains only** (one ride leg).
   - Resolve each `Stop`'s EVA number: use `stop.eva` when set, otherwise look up
     `IRIS_NAMES[stop.name]` with `/station/{pattern}` and take the first result's `eva`.
     Add `IRIS_NAMES` for the three Frankfurt stops without an EVA number:
     `"Frankfurt(M)Taunusanlage"`, `"Frankfurt(M)Hauptwache"`, `"Frankfurt(M)Konstablerwache"`.
     Cache lookups on the instance. Return `[]` if either stop has no EVA number.
   - Fetch the origin board for the hour before `departure`, its hour and the next hour (default:
     now), so a train planned at 11:58 but delayed to 12:05 is still found; and
     the destination board for the same hours plus one more. Merge each board with that
     station's `/fchg` changes by `s` id. Cache every HTTP response on the instance, because
     `plan()` asks for several destinations from the same origin; stay well under 60 calls per run.
   - A candidate is an origin stop with a `dp`, not cancelled, whose departure time (changed
     if present, else planned) is at or after `departure`, and whose departure path
     (`cpth` if present, else `ppth`) contains the destination's IRIS station name (the
     `station` attribute of the destination board's `<timetable>` root).
   - Find the same train on the destination board: same `s` id with the last two `-`-separated
     parts removed (the id looks like `<trip>-<YYMMDDHHmm>-<index>`, and `<trip>` may start with `-`),
     or failing that the same `tl` `c` and `n`. It needs an `ar` that is not cancelled.
     Skip candidates without a match; never guess an arrival.
   - Return up to `results` journeys, sorted by departure time. Each has one `Leg`:
     mode `"suburban"` for `c == "S"`, `"regional"` for RE/RB, else `"national"`;
     line `f"{c}{l}"` when `l` is set, else `f"{c} {n}"`; origin and destination are **our**
     `Stop.name` values (plan.py looks stations up by these names), times are the changed
     times when present, platforms are `cp` when present else `pp`, `minutes` is arrival minus
     departure, `coords` are the two stops' `(lon, lat)`. Journey source:
     `"DB Timetables (live)"`.
   - Times must be timezone-aware in Europe/Berlin, because `plan()` compares them with an
     aware `datetime.now().astimezone()`. Use `zoneinfo.ZoneInfo("Europe/Berlin")`; do not fall
     back to the host's offset (wrong on a non-German host). If it raises `ZoneInfoNotFoundError`,
     raise `ValueError` so `Auto` falls back. Add `"tzdata; sys_platform == 'win32'"` to the
     dependencies in `pyproject.toml` (Windows has no system time zone database).
     Naive `departure` values are treated as Berlin time.
   - Parse with `xml.etree.ElementTree`. Raise `requests.HTTPError` for HTTP errors
     (`resp.raise_for_status()`) and `ValueError` for unparseable XML, so `Auto` can catch them.

2. Change `Auto` to try a list of live providers in order, each with its own circuit breaker,
   then `Static`: `Auto(session=None, *, live: list | None = None)` (keep `session` first, the
   existing tests call `Auto(fake_session)`). When `live` is None keep
   today's behaviour (`[DbRest(session, timeout=8)]`), so the existing tests keep passing
   unchanged. A provider that raises is marked down for the rest of the run; a provider that
   returns `[]` is not marked down, the next one is just asked.

In `barrierfrei/plan.py` `main()`: build `DbTimetables(session, client_id, api_key)` from the
settings and use `--transit` choices `auto` (default: `Auto(live=[DbTimetables(fast, ...), DbRest(fast, timeout=8)])`),
`live` (DbTimetables only), `rest` (DbRest only) and `static`. The credentials are already read
via `require(...)`; move those two lines above the provider choice. Never log the key.
`fast` is a plain `requests.Session()` with no retries: the shared `make_session()` session retries
five times with backoff, which would delay the fallback to the typical timetable by a minute.
Keep the shared session for FaSta.

Update the module docstring of `transit.py` to list the new provider.

## Tests
Add to `tests/test_transit.py`, with a fake session (no network, no keys), small XML fixtures
written inline in the test file:
- `test_timetables_direct_train`: an origin board with one S3 whose `ppth` contains
  `Frankfurt(M)Taunusanlage`, a destination board with the same trip, an `/fchg` that delays the
  departure by 3 minutes and changes the platform. Assert one journey with the changed departure,
  the planned arrival, the changed platform, line `"S3"`, our stop names, an aware datetime.
- `test_timetables_skips_trains_that_do_not_stop_there`: the train's path lacks the destination.
- `test_timetables_skips_cancelled_and_too_early`: one cancelled (`cs="c"`), one before `departure`.
- `test_timetables_resolves_eva_by_name_once`: two `journeys()` calls to the same Frankfurt stop
  hit `/station/` once.
- `test_auto_tries_next_live_provider`: the first live provider raises, the second returns a
  journey; assert the second's journey is returned and the first is not asked again.

## Out of scope
- No transfers (two-train journeys), no U-Bahn or bus (Timetables only covers DB rail).
- Do not change `plan()`, ranking, routing, mapview, stations.py or the FaSta code.
- Do not add dependencies other than the Windows-only `tzdata` above.

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
