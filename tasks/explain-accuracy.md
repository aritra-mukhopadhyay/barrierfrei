# Make Muse Spark's trip explanation factually exact

## Context
The `--explain` text goes into the demo video and is the traveller-facing summary. A live run on
2026-11-03 (luisenplatz → zeil) produced three wrong statements:
1. "Ride 37 minutes to Frankfurt (Main) Taunusanlage platform 2, arriving 23:34." 23:34 is the
   door-to-door arrival at the Zeil; the S6 reached Taunusanlage at about 23:12. `summary()` gives
   each ride a departure but no arrival, so the model attaches `arrive_at` to the train.
2. "a 1964 m walk ... over uneven and rough surface ... This avoids stairs, uneven surface and very
   uneven surface." `Option.avoided` merges both walks: "uneven surface" avoided on the last walk is
   still crossed on the first one, so the summary claims to avoid something the trip still has.
3. "The other options via Konstablerwache and Hauptwache were skipped because their arrival lifts are
   unverified." They are not skipped; they are shown as alternatives whose lifts can't be checked live.

## Change
1. `barrierfrei/plan.py` `summary()`:
   - Each ride gets `"arrival": leg.arrival.isoformat() if leg.arrival else None` (after `departure`).
   - `"avoided_on_shortest_path"` lists only the entries of `o.avoided` that are in neither
     `o.walk_start.notes` nor `o.walk_end.notes` (either may be `None`). Keep the order. Do not change
     `Option.avoided` itself or `routing.avoided`.
2. `barrierfrei/plan.py` `EXPLAIN_PROMPT`: keep the first two sentences and the format placeholders;
   replace the instructions with these rules (one line each):
   - Explain the recommended option (the first usable one) in 4-6 short sentences: the walk to the
     station, which lifts to use, the train with its departure and the time it reaches the station,
     the walk at the end, and the arrival at the destination.
   - `leave_at` is when to leave the start and `arrive_at` is the arrival at the destination after the
     last walk; a ride's own `arrival` is when the train reaches that station. Never mix them up.
   - Only say a trip avoids something if it is listed in `avoided_on_shortest_path`; surfaces in a
     walk's `notes` are still on the route.
   - Other options are still offered, not skipped or rejected, unless their `usable` is false. If an
     alternative arrives earlier but is not `fully_verified`, say in one sentence that it is faster but
     some of its lifts can't be checked live. If an option is not usable, say why from its `problems`.
   - Do not invent anything that is not in the plan. Use the 24-hour times exactly as given.
3. `barrierfrei/mapview.py` `_why_best`: fix the known midnight limitation. When an arrival is earlier
   in the day than `leave_at` of the same option (e.g. leave 23:20, arrive 00:13), treat it as the next
   day (+24 h) before comparing. Remove the "Known limitation" comment. Options without `leave_at`
   compare as today.

## Tests
- `tests/test_plan.py::test_summary_has_ride_arrivals`: a summary of an option whose ride has a known
  arrival contains that arrival's ISO time in `rides[0]["arrival"]`; a ride with `arrival=None` gives `None`.
- `tests/test_plan.py::test_avoided_excludes_what_the_trip_still_crosses`: an `Option` with
  `avoided=["stairs", "uneven surface"]` and a `walk_start` whose notes contain `"uneven surface"`:
  the summary's `avoided_on_shortest_path` is `["stairs"]`; with both walks `None` it is unchanged.
- `tests/test_plan.py::test_explain_prompt_rules`: `EXPLAIN_PROMPT` mentions `arrive_at`, `arrival`,
  `avoided_on_shortest_path` and "not skipped"; `EXPLAIN_PROMPT.format(profile="wheelchair", plan="{}")` works.
- `tests/test_plan.py` (or the mapview tests): `_why_best` for an option leaving 23:20 and arriving 00:13
  against an option arriving 23:50 says it arrives 23 min later (when fully verified), not a negative
  or huge number.
- Existing tests keep passing; if one asserts the old prompt wording, update it and say so.

## Out of scope
- No change to routing, ranking, collapse, transit, stations or the site builder. No new dependencies.
- No network calls in tests (do not call `explain()`).

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
