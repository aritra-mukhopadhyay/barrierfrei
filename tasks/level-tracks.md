# Frankfurt Hbf's main-hall tracks need no lift; missing lift data is "unverified", not "broken"

## Context
A live run on 2026-11-03 (tu-darmstadt → staedel) rejected every RE, RB and ICE into Frankfurt (Main) Hbf
with "broken, no elevator listed for track 12" (or 10, 11). Tracks 1 to 24 at Frankfurt Hbf are the
terminus in the main hall at street level: you walk along the platform into the hall and out of
the north or south exit without any lift. Only the S-Bahn tracks 101 to 104 below ground need lifts,
and those are the only ones FaSta lists. So the planner calls a step-free arrival "broken" and the
trip from the TU to the Städel finds nothing. Separately, "no elevator listed" means we have no data,
which should be "unverified" (shown as OK?), never "broken" (which claims a lift is out of service).

## Change
1. `barrierfrei/transit.py`, `Stop`: new field
   `level_tracks: frozenset[str] = frozenset()  # tracks reached from the street without any lift`.
   Set it for "Frankfurt (Main) Hbf" to `frozenset(str(n) for n in range(1, 25))`, with a comment:
   terminus tracks in the main hall, level with the north and south exits. No other stop changes.
2. `barrierfrei/stations.py`, `Access`: new field `no_data: bool = False`. In `access()`, set it to
   True when the result is not ok and every entry in `missing` is a "no elevator listed ..." entry
   (none is "every elevator ... is out of service"). The early return for a station with no elevators
   at all also sets `no_data=True`.
3. `barrierfrei/plan.py`, `check_station()`:
   - Right after the `stop is None or stop.fasta is None` check, and before calling `lookup`: when `platform`
     is in `stop.level_tracks`, return
     `StationCheck(name, platform, "ok", "level access from the platform to the exits, no lift needed")`
     (no `use`, no `elevators`, `street_entrance=False`). Doing this before `lookup` also saves a FaSta call.
     This applies whether or not `transfer` is set.
   - When `result.ok` is False: status `"unverified"` if `result.no_data`, else `"broken"` (as today).
4. `barrierfrei/mapview.py`, page CSS: give `body` an explicit `background: #fff; color: #222;` so
   the panel stays readable in a browser in dark mode (braces are doubled in the template).

## Tests
- `tests/test_plan.py::test_frankfurt_hbf_main_hall_tracks_need_no_lift`:
  `plan.check_station("Frankfurt (Main) Hbf", "12", lookup)` where `lookup` raises `AssertionError`
  if called: status "ok", reason contains "level access", `use == []`. Track "101" with
  `lookup_with()` still uses its lifts, as in `test_full_lift_chain_is_reported`.
- `tests/test_plan.py::test_missing_lift_data_is_unverified_not_broken`: a FaSta fixture
  (built like the ones in the file) for Darmstadt Hbf whose only elevator serves track 5;
  `check_station("Darmstadt Hbf", "7", ...)` is "unverified" and its reason contains
  "no elevator listed for track 7". With that elevator out of service (`lookup_with({<its number>})`),
  `check_station("Darmstadt Hbf", "5", ...)` is "broken".
- `tests/test_stations.py::test_access_no_data_flag`: `no_data` is True for "no elevator listed"
  only, False when an elevator is out of service, False when ok.
- Keep every existing test passing; if one asserted "broken" for missing data, change it to
  "unverified" and say so in your summary.

## Out of scope
- No change to ranking, collapse, routing, DbTimetables, FaSta fetching, or other stops' data.
- No new dependencies.

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
