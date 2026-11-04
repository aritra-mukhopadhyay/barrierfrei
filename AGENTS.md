# Barrierefrei: rules for coding agents

Step-free trip planner for Darmstadt and Frankfurt. Python 3.11+.

- Package code lives in `barrierfrei/`, tests in `tests/`, one-off key checks in `hello/`.
- All settings and secrets go through `barrierfrei/config.py` (pydantic-settings, `SecretStr`).
  Never hard-code a key, never read or write `.env`, never log a secret.
- Tests run offline: no network, no keys, no credits. Mock HTTP with recorded payloads, as the
  existing tests do. Add or update a test for every behaviour change.
- Safety-critical facts (elevator status, steps on a route) are decided by code, not by a model.
- Style: `ruff check .` and `ruff format .` must pass (line length 110). Small functions, type
  hints, a docstring that says why when the why is not obvious. No new dependency without asking.
- Keep diffs focused on the task. Do not reformat or rename unrelated code.
