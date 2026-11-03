# Plan with Deutschlandticket trains by default; long-distance trains only on request

## Context
With live DB Timetables, the planner now recommends ICE trains Darmstadt Hbf → Frankfurt Hbf first
(live run 2026-11-03: "ICE 1092 ... OK, 85 min"). Most people on this corridor travel with a
Deutschlandticket, and disabled travellers with a free-travel pass ride local trains for free. Neither
covers ICE, IC or EC. A recommendation they can't use with their ticket is a bad default.

## Change
1. `barrierfrei/transit.py`:
   - `LONG_DISTANCE_MODES = frozenset({"national", "nationalExpress"})` (DbTimetables uses "national";
     db.transport.rest uses "nationalExpress" for ICE and "national" for IC/EC), with a comment.
   - `Journey.long_distance` property: True when any ride's `mode` is in `LONG_DISTANCE_MODES`.
2. `barrierfrei/plan.py`:
   - `plan(...)` gets a keyword argument `tickets: str = "regional"` (after `now`). With
     `"regional"`, skip journeys whose `long_distance` is True, right where empty journeys are
     skipped (`if not journey.rides: continue`). With `"any"`, keep them. Any other value raises `ValueError`.
   - `summary()` adds `"long_distance": o.journey.long_distance` to each option, and a
     `tickets` keyword argument (default `"regional"`) that is written as a top-level `"tickets"` key.
   - `main()`: `--tickets` with choices `regional` (default) and `any`, help
     `"regional: trains covered by the Deutschlandticket (S-Bahn, RE, RB); any: also ICE, IC and EC"`.
     Pass it to `plan()` and `summary()`. When `tickets == "regional"` and no option is found, also log
     at INFO: `No regional trains found; try --tickets any to include ICE, IC and EC.`
3. `barrierfrei/mapview.py` `_option_html`: when the option's `long_distance` is true, add
   `<p><small>Needs a long-distance ticket (ICE/IC/EC are not covered by the Deutschlandticket).</small></p>`
   after the leave-at line. Use `option.get("long_distance")` so older plan.json files still render.
4. `README.md`: in the "4. Plan a trip" row, mention `--tickets any`.

## Tests
- `tests/test_transit.py::test_long_distance_journeys`: a journey with an ICE leg (mode "national"),
  one with "nationalExpress", and one with only "suburban"/"regional" legs (and a walk leg); only the
  first two are `long_distance`.
- `tests/test_plan.py::test_regional_tickets_skip_long_distance_trains`: a fake provider (like the
  `Timed` class in the file) that returns one ICE journey (mode "national") and one S-Bahn journey for
  the same stations: default `plan(...)` returns only the S-Bahn option; `tickets="any"` returns both,
  and the ICE option's summary has `long_distance` True; `tickets="foo"` raises `ValueError`.
- `tests/test_plan.py::test_option_html_marks_long_distance`: `mapview._option_html` on a summary-shaped
  option dict with `long_distance=True` contains `Needs a long-distance ticket`; with the key missing it does not.
- Existing tests must keep passing unchanged.

## Out of scope
- No change to ranking, collapse, routing, stations, DbTimetables parsing or FaSta.
- No fares or prices. No new dependencies.

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
