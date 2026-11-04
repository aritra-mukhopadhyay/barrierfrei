# Contributing

Setup and the API keys are described in the [README](README.md#run-it-yourself). The code rules are in
[AGENTS.md](AGENTS.md); they apply to people and coding agents alike.

## Before opening a pull request

```bash
ruff check . && ruff format --check .
pytest -q          # offline: no network, no keys, no credits
```

- One topic per pull request, with a test for every behaviour change.
- Describe what changed and how you checked it, including any run against live data.
- Never commit `.env`, `data/` or anything containing a key.

## Task specs and delegated changes

Larger changes start as a spec in `tasks/<slug>.md`, written from [tasks/TEMPLATE.md](tasks/TEMPLATE.md):
goal, files to touch, exact behaviour, tests to add, and what is out of scope. A spec can be
implemented by hand or delegated to Meta's [Muse Code](https://dev.meta.ai/docs/muse-code) agent with
`tools/delegate.py`, which needs `MODEL_API_KEY`:

1. `python tools/delegate.py run tasks/<slug>.md` lets the agent work in its own clone at
   `../barrierfrei-tasks/<slug>`, never in your checkout, and writes `../barrierfrei-tasks/<slug>.diff`.
2. Review the diff like any pull request: correctness, tests that exercise the change, no secrets, no
   scope creep, nothing that touches files or the network outside the task folder.
3. Only after that review, `python tools/delegate.py check <slug>` runs the linters and tests on the
   clone. This executes the agent's code on your machine, which is why the review comes first.
4. For changes, `python tools/delegate.py resume <slug> "<specific feedback>"`. If two rounds do not
   get it right, finish the change by hand and say so in the pull request.
5. Commit in the task folder, push `muse/<slug>` and open a pull request that says which parts the
   agent wrote and who reviewed them.

A typo or one-line fix is faster to make directly.
