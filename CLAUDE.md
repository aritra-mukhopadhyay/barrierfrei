# Working on this repo with Claude

Read `AGENTS.md` first; its rules apply to you too.

## Delegating code to Muse Code (Meta)

Aritra wants Claude to plan and review, and Meta's Muse Code to write the code, so the work runs
on their Meta Model API credits. For any task larger than a few lines:

1. Write the task to `tasks/<slug>.md` using `tasks/TEMPLATE.md`: goal, files to touch, exact
   behaviour, tests to add, and what is out of scope.
2. Run `python tools/delegate.py run tasks/<slug>.md`. It works in `../barrierfrei-tasks/<slug>`,
   never in this checkout.
3. Review `../barrierfrei-tasks/<slug>.diff` like a pull request from a junior colleague: correctness,
   tests that really test the change, no secrets, no scope creep, nothing that touches files or
   the network outside the task folder. Only then run `python tools/delegate.py check <slug>`, since
   it executes the agent's tests outside the sandbox.
4. If it needs changes: `python tools/delegate.py resume <slug> "<specific feedback>"`.
   After two failed rounds, fix it yourself and say so.
5. When it is right, commit in the task folder, push `muse/<slug>` and open a PR that says Muse Code
   wrote the code and Claude reviewed it.

Small fixes (a typo, one line) are faster to do directly.
