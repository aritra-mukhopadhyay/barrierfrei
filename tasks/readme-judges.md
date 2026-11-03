# Rewrite the README for hackathon judges and add an MIT licence

## Context
The repo goes public for the Meta Global AI Developer Hackathon submission (due 2026-11-04). Judges
read the README first, usually for a minute, after watching a 3-minute video. Today the README starts
with setup steps and says little about the problem, the demo, or how the Meta models are used. The
submission must also be open source, and the repo has no licence file.

## Change
1. New file `LICENSE`: the standard MIT licence text, `Copyright (c) 2026 Aritra K. Mukhopadhyay`.
2. `README.md`: reorganise into these sections, in this order. Keep every command, key table row and
   pipeline row that exists today (they move under "Run it yourself"); only the top part is new text.
   Plain, short sentences; no marketing words ("revolutionary", "seamless", "cutting-edge").
   - `# Barrierefrei: step-free trips, checked live` followed by a two-sentence summary: it plans a
     door-to-door trip between central Darmstadt and central Frankfurt for wheelchair and stroller
     users, with walks that avoid stairs and high kerbs and train connections whose lifts DB reports
     as working right now.
   - `## Try it`: a line `Demo site: https://aritra-mukhopadhyay.github.io/barrierfrei/ (trips computed
     live, then published)` and `Video (3 min): <!-- VIDEO_URL -->` exactly like that (the link is added later).
   - `## The problem`: three or four sentences: route planners show a lift icon but not whether the lift
     works; when it doesn't, a wheelchair user is stuck on a platform; walking routes ignore kerbs and
     stairs that OpenStreetMap doesn't tag; checking all of this by hand before every trip takes long.
   - `## What it does`: a numbered list of the four steps of one trip: (1) step-free walk to a station
     on OpenStreetMap paths, avoiding segments tagged steps and segments where street photos show a
     barrier, (2) live DB departures (DB Timetables API), with Deutschlandticket trains by default,
     (3) every lift on the way (departure, transfer, arrival) checked against DB's live FaSta status,
     trips through a broken lift are rejected, and Frankfurt Hbf's main-hall tracks need no lift, (4)
     step-free walk to the destination; the result is a map with the walk, the ride, each lift and why
     the trip was chosen.
   - `## How the Meta models are used`: a table with columns Model, Where, What it does:
     - SAM 3.1, `barrierfrei/sam.py` / `perceive.py`: finds stairs, kerbs, ramps, e-scooters and
       construction barriers in Mapillary street photos.
     - Muse Spark (vision), `barrierfrei/judge.py`: looks at the photo with SAM's boxes drawn on it and
       returns schema-checked JSON: barriers, who can pass, confidence.
     - Muse Spark (text), `barrierfrei/plan.py --explain`: explains the chosen trip in plain words.
     - Muse Code: wrote most of the code from task specs in `tasks/`, which Claude wrote and reviewed
       (see "How this was built").
     Then one sentence: safety-critical facts (lift status, tagged steps) are decided by code from
     DB and OpenStreetMap data, never by a model; a photo verdict only adds a detour penalty, and only
     when the model is at least 70 % confident.
   - `## Honest limits`: a bullet list:
     - Lifts run by VGF (Frankfurt U-Bahn, e.g. Hauptwache, Konstablerwache) have no public live data;
       those stops show "OK?" (unverified).
     - Photo perception is evidence, not ground truth: in our evaluation against OpenStreetMap
       (`python -m barrierfrei.evaluate`) Muse Spark flagged about 29 % of photos of tagged steps as
       not wheelchair-passable, so the tags stay the primary source.
     - Coverage is central Darmstadt and central Frankfurt only (two ingested areas).
     - The demo site is a snapshot: lifts and trains change, so the pages say when they were computed.
     - No live web app: the keys stay on the machine that computes the trips.
   - `## How this was built`: two or three sentences: Claude planned and reviewed, Meta's Muse Code
     wrote the code from one spec per change in `tasks/`, each change went through a pull request;
     link `CLAUDE.md` and `tools/delegate.py`. Move the existing "Claude plans, Muse Code writes"
     paragraph here.
   - `## Run it yourself`: the existing "Setup", "Keys you need", "Hello-world checks", "Pipeline",
     "Demo site" content, as `###` subsections, unchanged except heading levels, plus the region and
     settings sentences.
   - `## Development`: the existing ruff/pytest block.
   - `## Licence`: `MIT, see [LICENSE](LICENSE).`
3. `pyproject.toml`: add `license = "MIT"` under `[project]` if there is no licence field (check the
   build backend accepts the string form; if it only accepts a table, use `license = {text = "MIT"}`).

## Tests
- `tests/test_readme.py` (new, offline): the README contains each `python -m barrierfrei.<module>`
  command whose module exists in `barrierfrei/`, contains `<!-- VIDEO_URL -->` or a `Video` line, links
  `LICENSE`, and the `LICENSE` file starts with `MIT License`. Every `barrierfrei/<name>.py` path
  mentioned in the README exists.
- Existing tests keep passing unchanged.

## Out of scope
- No code changes in `barrierfrei/`, `hello/` or `tools/`. No new dependencies. No images or badges
  that load from external services.

## Done when
- `python -m ruff check .`, `python -m ruff format --check .` and `python -m pytest -q` pass.
