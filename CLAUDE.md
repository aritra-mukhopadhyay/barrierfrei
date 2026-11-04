# Working on this repo with Claude Code

Read `AGENTS.md` first; its rules apply to every change.

## Workflow: plan, delegate, review

Changes larger than a few lines follow one loop: Claude Code plans and reviews, and Meta's Muse Code
writes the code from a written spec.

1. Write the task to `tasks/<slug>.md` using `tasks/TEMPLATE.md`: goal, files to touch, exact
   behaviour, tests to add, and what is out of scope.
2. Run `python tools/delegate.py run tasks/<slug>.md`. It works in `../barrierfrei-tasks/<slug>`,
   never in this checkout.
3. Review `../barrierfrei-tasks/<slug>.diff` like any pull request: correctness, tests that really
   test the change, no secrets, no scope creep, nothing that touches files or the network outside
   the task folder. Only then run `python tools/delegate.py check <slug>`, since it executes the
   agent's tests outside the sandbox.
4. If it needs changes: `python tools/delegate.py resume <slug> "<specific feedback>"`.
   After two failed rounds, fix it directly and note that in the pull request.
5. When it is right, commit in the task folder, push `muse/<slug>` and open a pull request that
   states which agent wrote the code and who reviewed it.

Small fixes (a typo, one line) are faster to make directly.
