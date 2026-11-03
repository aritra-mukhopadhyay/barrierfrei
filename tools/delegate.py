"""Hand one coding task to Meta's Muse Code agent and collect what it did, for review.

The split of work: Claude plans the change and writes it up as a task file (Markdown), Muse Code
(running Muse Spark on your Meta Model API credits) writes the code, and Claude reviews the diff
before anything is committed. This script is the hand-off in the middle:

  1. Creates an isolated git worktree on a new branch `muse/<slug>`, next to the repo in
     ../barrierfrei-tasks/<slug>, so the agent never touches your own checkout, .env or data/.
  2. Runs `muse exec` headless in that worktree with the task, the repo rules from AGENTS.md
     and a step cap. Muse Code's own guardrails stay on (OS sandbox + approval policy), so a
     risky command is refused rather than run. Every event is logged to <slug>.jsonl.
  3. Runs the repo's checks there (ruff, pytest) and writes the full diff to <slug>.diff,
     then prints a short report.

Usage (from the repo root, inside the venv):
  python tools/delegate.py run tasks/add-foo.md            # new task, slug from the file name
  python tools/delegate.py resume add-foo "Fix the failing test in test_plan.py"
  python tools/delegate.py check add-foo                   # re-run checks + diff only
  python tools/delegate.py clean add-foo                   # remove worktree and branch

Your key: MODEL_API_KEY from .env (or META_API_KEY from the environment) is passed to Muse Code
as META_API_KEY. This script never prints it or writes it to a file.

Accepting the work is a normal git step, done only after review:
  cd ../barrierfrei-tasks/<slug> && git add -A && git commit -m "..." && git push -u origin muse/<slug>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO.parent / "barrierfrei-tasks"
DEFAULT_MODEL = "muse-spark-1.3"  # Standard mode: Meta does not train on what we send.
DEFAULT_MAX_STEPS = 60

PREAMBLE = """You are the executor on a two-agent team. A reviewer planned this task and will read
your diff before anything is committed. Do exactly the task below, nothing more.

Rules:
- Do not commit, push, create branches, or edit files outside this directory.
- Do not read, print, or create .env files or any secret. Tests must run without keys or network.
- Keep the change as small as the task allows. Match the existing style; no drive-by refactors.
- When done, run `python -m ruff check .`, `python -m ruff format .` and `python -m pytest -q`
  from this directory and fix what fails.
- Finish with a short summary: what you changed and why, what you ran and its result, and
  anything you were unsure about or did not do.

Project rules (AGENTS.md):
{agents_md}

Task:
{task}
"""


def read_key() -> str:
    """META_API_KEY from the environment, else MODEL_API_KEY from the environment or .env."""
    for name in ("META_API_KEY", "MODEL_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*MODEL_API_KEY\s*=\s*(.*)", line)
            if m:
                value = m.group(1).split(" #")[0].strip().strip("'\"")
                if value:
                    return value
    sys.exit("No Meta key found: set MODEL_API_KEY in .env (or META_API_KEY in the environment).")


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def worktree(slug: str) -> Path:
    return TASKS_DIR / slug


def meta_path(slug: str) -> Path:
    return TASKS_DIR / f"{slug}.json"


def find_session_id(log: Path) -> str | None:
    """Best effort: the first session id field in Muse Code's JSONL events."""
    if not log.exists():
        return None

    def walk(obj: object) -> str | None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ("session_id", "sessionId") and isinstance(value, str):
                    return value
                found = walk(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = walk(item)
                if found:
                    return found
        return None

    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            found = walk(json.loads(line))
        except json.JSONDecodeError:
            continue
        if found:
            return found
    return None


def run_muse(slug: str, prompt: str, model: str, max_steps: int, session: str | None) -> int:
    muse = shutil.which("muse")
    if not muse:
        sys.exit("`muse` is not on PATH. Install Muse Code first (see the setup guide).")
    prompt_file = TASKS_DIR / f"{slug}.prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    cmd = [muse, "exec", "--model", model, "--max-model-steps", str(max_steps), "--json"]
    cmd += ["--prompt-file", str(prompt_file)]
    if session:
        cmd += ["--session-id", session]
    env = {**os.environ, "META_API_KEY": read_key()}
    env.pop("MODEL_API_KEY", None)  # the agent needs only its own credential
    log = TASKS_DIR / f"{slug}.jsonl"
    print(f"Muse Code is working on '{slug}' (model {model}, at most {max_steps} steps)...")
    with log.open("a", encoding="utf-8") as out:
        proc = subprocess.run(cmd, cwd=worktree(slug), env=env, stdout=out, stderr=subprocess.STDOUT)
    print(f"muse exec exited with {proc.returncode} (0 = the run finished, not that the work is right).")
    return proc.returncode


def check(slug: str) -> bool:
    """Run the repo's checks in the worktree and save the diff. Returns True when all pass."""
    wt = worktree(slug)
    # Put the worktree first on the import path; otherwise an editable install of the main
    # checkout would be imported and the tests would silently check the wrong code.
    env = {**os.environ, "PYTHONPATH": str(wt)}
    for secret in ("MODEL_API_KEY", "META_API_KEY", "MAPILLARY_TOKEN", "DB_API_KEY", "DB_CLIENT_ID"):
        env.pop(secret, None)  # tests must pass offline and without keys
    results = {}
    for name, cmd in {
        "ruff check": [sys.executable, "-m", "ruff", "check", "."],
        "ruff format": [sys.executable, "-m", "ruff", "format", "--check", "."],
        "pytest": [sys.executable, "-m", "pytest", "-q"],
    }.items():
        proc = subprocess.run(cmd, cwd=wt, env=env, capture_output=True, text=True)
        results[name] = proc.returncode == 0
        print(f"\n--- {name}: {'PASS' if results[name] else 'FAIL'}")
        if not results[name]:
            print("\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:]))
    git("add", "-A", "--intent-to-add", cwd=wt)  # so new files show up in the diff
    (TASKS_DIR / f"{slug}.diff").write_text(git("diff", "HEAD", cwd=wt), encoding="utf-8")
    print("\n--- changed files")
    print(git("diff", "HEAD", "--stat", cwd=wt).strip() or "(no changes)")
    print(f"\nFull diff: {TASKS_DIR / f'{slug}.diff'}")
    print(f"Agent log: {TASKS_DIR / f'{slug}.jsonl'}")
    return all(results.values())


def cmd_run(args: argparse.Namespace) -> int:
    task_file = Path(args.task_file)
    slug = args.slug or task_file.stem
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        sys.exit("Slug must be lowercase letters, digits and dashes (e.g. add-voice-input).")
    if worktree(slug).exists():
        sys.exit(f"{worktree(slug)} already exists. Use `resume {slug} ...` or `clean {slug}` first.")
    TASKS_DIR.mkdir(exist_ok=True)
    base = args.base or "HEAD"
    git("worktree", "add", "-b", f"muse/{slug}", str(worktree(slug)), base)
    agents = REPO / "AGENTS.md"
    prompt = PREAMBLE.format(
        agents_md=agents.read_text(encoding="utf-8") if agents.exists() else "(none)",
        task=task_file.read_text(encoding="utf-8"),
    )
    code = run_muse(slug, prompt, args.model, args.max_steps, session=None)
    meta = {
        "slug": slug,
        "base": git("rev-parse", base).strip(),
        "model": args.model,
        "session": find_session_id(TASKS_DIR / f"{slug}.jsonl"),
    }
    meta_path(slug).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    ok = check(slug)
    return 0 if code == 0 and ok else 1


def cmd_resume(args: argparse.Namespace) -> int:
    if not worktree(args.slug).exists():
        sys.exit(f"No task '{args.slug}'. Start it with `run`.")
    meta = json.loads(meta_path(args.slug).read_text(encoding="utf-8"))
    session = args.session or meta.get("session")
    if not session:
        sys.exit(f"No session id found in {args.slug}.jsonl; pass one with --session.")
    code = run_muse(args.slug, args.message, args.model or meta["model"], args.max_steps, session)
    ok = check(args.slug)
    return 0 if code == 0 and ok else 1


def cmd_check(args: argparse.Namespace) -> int:
    return 0 if check(args.slug) else 1


def cmd_clean(args: argparse.Namespace) -> int:
    if worktree(args.slug).exists():
        git("worktree", "remove", "--force", str(worktree(args.slug)))
    subprocess.run(["git", "branch", "-D", f"muse/{args.slug}"], cwd=REPO, capture_output=True)
    for suffix in (".json", ".jsonl", ".diff", ".prompt.md"):
        (TASKS_DIR / f"{args.slug}{suffix}").unlink(missing_ok=True)
    print(f"Removed task '{args.slug}'.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="start a new task in its own worktree")
    p.add_argument("task_file", help="Markdown file describing the task")
    p.add_argument("--slug", help="task name (default: the task file's name)")
    p.add_argument("--base", help="commit or branch to start from (default: HEAD)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("resume", help="send review feedback to the same Muse Code session")
    p.add_argument("slug")
    p.add_argument("message", help="what to fix")
    p.add_argument("--session", help="override the session id read from the log")
    p.add_argument("--model")
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("check", help="re-run checks and refresh the diff")
    p.add_argument("slug")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("clean", help="remove a task's worktree, branch and logs")
    p.add_argument("slug")
    p.set_defaults(func=cmd_clean)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
