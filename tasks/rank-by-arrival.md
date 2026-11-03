# Rank usable trips by when you arrive, not by how long the trip takes

## Context
`plan.rank()` sorts by `(not usable, not verified, score)`, where `score` is door-to-door travel time
plus penalties (rough or cobbled walks cost more than their length, and every unverified station or
transfer walk adds `UNVERIFIED_PENALTY_MIN`). It ignores *when* a trip leaves. With live departures this
recommends the wrong train. Live runs on 2026-11-03, wheelchair, Deutschlandticket trains:
- Staatstheater → Alte Oper: RE60 (86 min, leave 17:24, arrive 18:51) was ranked above the S6 to
  Taunusanlage (89 min, leave 16:53, arrive 18:22), which arrives 29 minutes earlier and is just as
  verified.
- TU Darmstadt → Städel: RB68 (86 min, dep 18:30, arrive 19:05) was ranked above RE60 (87 min, dep 18:07,
  arrive 18:43).

A traveller wants the trip that gets them there first, among the ones whose lifts we could check.

## Change
In `barrierfrei/plan.py`:

1. New `Option` property `ranked_arrival -> datetime | None`: `arrive_at + timedelta(minutes=self.score -
   self.minutes)` when `arrive_at` is set, else `None`. Docstring: the arrival time, pushed later by the
   same penalties `score` adds (rough walks, unverified stations), so penalties keep steering the choice
   without changing the times we show.
2. `rank()` sorts with exactly this key:
   `(not o.usable, not o.verified, o.ranked_arrival is None, o.ranked_arrival.timestamp() if o.ranked_arrival else 0.0, o.score)`.
   Use `.timestamp()` so naive and aware datetimes, which some tests mix, are never compared directly.
   Update the docstring:
   usable first, then fully verified, then earliest (penalised) arrival, then score; trips without
   times (the typical timetable) keep today's order by score.
   How the penalty combines:
   - Within the fully verified tier, no station is "unverified", so the unverified penalty is 0 there.
     Only the rough-walk penalty can push a verified trip's ranked arrival later.
   - Within the not-verified tier, each unverified station or transfer walk pushes the ranked arrival
     10 minutes later, as today it pushes the score. So a trip with one unverified stop that arrives
     at 18:25 beats one with two unverified stops arriving at 18:20 (18:35 vs 18:40).
   - Unusable (NO) options stay last, ordered by the same key.
3. `collapse()` is unchanged. Groups keep the position of their best-ranked member, and the kept card
   is the earliest departure of that trip. For the same line, platforms and doors, the earliest
   departure also arrives first, so the kept card and the group's position agree. Add one sentence to
   `collapse()`'s docstring saying so.

In `barrierfrei/mapview.py`, `_why_best(option, others)` (the note on the recommended card):

4. Today it says "N min slower than the fastest option, but DB's live data shows every lift on it
   working." whenever a usable option has fewer `minutes`. After the ranking change, the recommended
   trip can be slower in minutes but arrive first, and then that note is misleading. New behaviour:
   - When the recommended option has `arrive_at` (an "HH:MM" string in the summary), compare arrival
     times. Convert "HH:MM" to minutes after midnight. `later_by` = recommended arrival minus the
     earliest `arrive_at` among the other usable options that have one. Only when `later_by > 0` and
     the recommended option is `fully_verified`, return
     `<br><small class='ok'>arrives {later_by} min later than the earliest option, but DB's live data shows every lift on it working.</small>`.
     Otherwise return "" (no "slower" note when it arrives first or at the same time).
   - When the recommended option has no `arrive_at` (the typical timetable), keep today's
     minutes-based note unchanged.
   - Known limitation, fine for this task: a trip arriving after midnight compares wrongly against one
     arriving before. Say so in a one-line comment.

## Tests
In `tests/test_plan.py`, offline. Build `Option`s directly with `transit.Leg` and `transit.Journey`,
`routing.WalkRoute(profile, length_m, cost, [], [], [])`, and
`StationCheck(name, platform, "ok" | "unverified")`, like `test_leave_and_arrive_times` does. Use the
wheelchair profile (60 m/min) and aware datetimes on 2026-11-03 (`datetime(..., tzinfo=ZoneInfo("Europe/Berlin"))`).
For walks with cost == length, `score == minutes`.
- `test_rank_prefers_earlier_arrival_among_verified` (live example 1). S6: walk_start 2199 m, ride
  17:35 → 18:12 (37 min) Darmstadt Hbf → Frankfurt (Main) Taunusanlage, walk_end 600 m → arrive 18:22,
  about 89 min. RE60: walk_start 2222 m, ride 18:07 → 18:25 (18 min) Darmstadt Hbf → Frankfurt (Main) Hbf,
  walk_end 1560 m → arrive 18:51, about 86 min. Both checks "ok". `rank([re60, s6])` puts the S6 first.
  It fails before the change.
- `test_rank_prefers_earlier_arrival_regional_pair` (live example 2). Both walk_start 2767 m and
  walk_end 1081 m. RE60 18:07 → 18:25 arrives 18:43. RB68 18:30 → 18:47 arrives 19:05. `rank([rb68, re60])`
  puts RE60 first. It fails before the change.
- `test_rank_keeps_verified_before_earlier_unverified`: the verified S6 above (18:22), and an S6 to
  Frankfurt (Main) Hauptwache whose alight check is "unverified" and which arrives 18:21. The verified
  one ranks first.
- `test_rank_penalties_shift_arrival`: two not-verified options, one with one unverified check arriving
  18:25 and one with two unverified checks arriving 18:20. The first ranks first. Also two verified
  options: one arriving 18:22 whose walk_end has cost = length + 900 (a 15 min rough-walk penalty), and
  one arriving 18:30 with cost = length. The 18:30 one ranks first.
- `test_rank_without_times_uses_score`: options whose rides have no departure/arrival (like
  `transit.Static`) keep the order by score. An option with times ranks before one without in the same
  tier.
- `test_rank_unusable_last`: an option with a "broken" check arriving 18:00 ranks after a usable one
  arriving 19:00.
- `test_collapse_order_matches_rank`: S6 to Taunusanlage at 17:35 and 18:05 (same line, platforms,
  doors), plus the RE60 option. `result = plan.collapse(plan.rank(options))` keeps the 17:35 card with
  `later == [18:05]`, and `plan.rank(result) == result`.
- `test_why_best_compares_arrivals`: call `mapview._why_best` on summary-shaped dicts (the keys it
  reads: `usable`, `fully_verified`, `minutes`, `arrive_at`).
  - A verified recommended option with 89 min arriving "18:22", against a usable one with 86 min
    arriving "18:51": returns "" (no "slower" note).
  - A verified recommended option arriving "18:30", against a usable one arriving "18:21": the note
    contains "arrives 9 min later than the earliest option".
  - With `arrive_at` None on the recommended option, the old "min slower than the fastest option"
    text still appears when another usable option has fewer minutes.
- All existing tests must keep passing unchanged. Tests built on `transit.Static` have no times, so
  they still rank by score. If an existing test asserts the old "slower" text for an option that has
  `arrive_at`, update only that assertion and say so in the summary.

## Out of scope
- Do not change `score`, `minutes`, `UNVERIFIED_PENALTY_MIN`, `collapse`'s grouping key, walking speeds
  or `BOARDING_BUFFER_MIN`.
- In mapview, change only `_why_best` as described above; nothing else there. Do not change
  routing, transit, stations, tickets or FaSta code.
- No new dependencies.

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
