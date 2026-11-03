# Show when to leave, and one card per route instead of one per train

## Context
With live departures from DB Timetables (PR #19), `python -m barrierfrei.plan --from luisenplatz --to zeil`
now returns 9 options: 3 departures (12:35, 13:05, 13:35) for each of 3 Frankfurt stations. The
map panel shows 9 near-identical cards, the log lines do not show the departure time, and nothing
tells the traveller when to leave. The typical-timetable fallback (`transit.Static`) also still
describes S3/S4 every 15 minutes via Frankfurt Hbf, but the live data shows only the S6 at :05 and
:35 from Darmstadt Hbf into the Frankfurt tunnel, entering from the east (Frankfurt Süd,
Konstablerwache first, then Hauptwache, then Taunusanlage).

## Change
In `barrierfrei/plan.py`:

1. `Option` gets a field `later: list[datetime] = field(default_factory=list)`: later departures of
   the same trip.
2. `Option` gets two properties:
   - `leave_at`: `rides[0].departure - timedelta(minutes=walk_minutes(walk_start) + BOARDING_BUFFER_MIN)`
     when the first ride has a departure and `walk_start` is set, else `None`.
   - `arrive_at`: `rides[-1].arrival + timedelta(minutes=walk_minutes(walk_end))` when the last ride
     has an arrival and `walk_end` is set, else `None`.
3. New function `collapse(options: list[Option]) -> list[Option]`, called on the result of
   `rank(options)` at the end of `plan()` (so `plan()` returns `collapse(rank(options))`).
   Two options are the same trip when these are all equal: the tuple of `(leg.line, leg.origin,
   leg.destination)` over `journey.rides`, `board_via`, `alight_via`, `usable` and `verified`.
   For each group, in the position of its best-ranked member, keep the member whose first ride
   departs earliest (a `None` departure counts as earliest), and set its `later` to the sorted
   first-ride departures of the other members (skip `None`). Groups of one are unchanged.
   Keep the order of groups as `rank` gave it.
4. `summary()` adds to each option: `"leave_at"` and `"arrive_at"` as `"HH:MM"` strings (or `None`),
   and `"later_departures"`: a list of `"HH:MM"` strings from `later`.
5. In `main()`, the per-option log line also shows the first ride's departure and the leave time
   when known, e.g. `OK  option 1: 97 min, dep 12:35, leave 12:02, S6 Darmstadt Hbf -> ...`.

In `barrierfrei/mapview.py` `_option_html`: when `leave_at` is set, put
`<p><b>Leave at HH:MM</b>, arrive about HH:MM</p>` (the arrive part only when `arrive_at` is set)
right after the card heading; when `later_departures` is not empty, add
`<small>Same trip later: 13:05, 13:35</small>` below the ride list. Escape with `html.escape` like
the rest of the function. Remember the page template uses `PAGE.format(...)`, but `_option_html`
builds plain f-strings, so no brace doubling there.

In `barrierfrei/transit.py`, update `STATIC_JOURNEYS` and the comment above it to the live
pattern: line `"S6"` (not `"S3/S4"`) for the direct trains, Konstablerwache 33 min, Hauptwache 35 min,
Taunusanlage 37 min; keep the RE/RB + S-Bahn via Frankfurt Hbf track 101 option as it is. The comment
should say the S6 runs at :05 and :35 from Darmstadt Hbf and enters the tunnel from Frankfurt Süd,
as seen in DB Timetables on 2026-11-03.

## Tests
In `tests/test_plan.py` (no network, no keys; reuse the existing fixtures and helpers there):
- `test_collapse_keeps_earliest_and_lists_later`: three options with the same ride and doors but
  departures 13:35, 12:35, 13:05 (in that ranked order) collapse to one option departing 12:35 with
  `later == [13:05, 13:35]`; an option to a different destination stays separate and keeps its position.
- `test_collapse_keeps_verified_and_unverified_apart`: same trip, one verified and one not, stays two options.
- `test_leave_and_arrive_times`: with a known departure, arrival, start walk and end walk, `leave_at`
  and `arrive_at` are as computed above; without a departure both are `None`.
- Extend `test_summary_and_map_render`: the summary options have the keys `leave_at`, `arrive_at`,
  `later_departures`.
- `test_option_html_shows_leave_time_and_later`: `mapview._option_html` on a summary-shaped dict with
  `leave_at="12:02"`, `arrive_at="13:39"`, `later_departures=["13:05"]` contains `Leave at 12:02`,
  `arrive about 13:39` and `Same trip later: 13:05`.
In `tests/test_transit.py`: `test_static_lines_match_live_pattern`: Static's direct journeys use line `"S6"`.

## Out of scope
- Do not change ranking (`rank`, `score`, `minutes`), routing, stations.py, the FaSta code or `DbTimetables`.
- Do not add dependencies.
- Keep lines that fit within 110 characters on one line (ruff format will join them otherwise).

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
