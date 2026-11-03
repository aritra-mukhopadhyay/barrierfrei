"""Hand one coding task to Meta's Muse Code agent and collect what it did, for review.

The split of work: Claude plans the change and writes it up as a task file (Markdown), Muse Code
(running Muse Spark on your Meta Model API credits) writes the code, and Claude reviews the diff
before anything is committed. This script is the hand-off in the middle:

  1. Clones the repo (locally, fast) onto a new branch `muse/<slug>`, next to the repo in
     ../barrierfrei-tasks/<slug>, so the agent never touches your own checkout, .env or data/.
  2. Runs `muse exec` headless in that clone with the task, the repo rules from AGENTS.md
     and a step cap. Muse Code's own guardrails stay on (OS sandbox + approval policy), so a
     risky command is refused rather than run. Every event is logged to <slug>.jsonl.
  3. Writes the full diff to <slug>.diff for review. The repo's checks (ruff, pytest) run
     only when you call `check`, after reading the diff, because tests are agent-written code
     and would run outside the sandbox.

Usage (from the repo root, inside the venv):
  python tools/delegate.py run tasks/add-foo.md            # new task, slug from the file name
  python tools/delegate.py resume add-foo "Fix the failing test in test_plan.py"
  python tools/delegate.py check add-foo                   # after review: ruff + pytest
  python tools/delegate.py clean add-foo                   # remove the task's clone and logs

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
sys.path.insert(0, str(REPO))  # import the package from this checkout

from barrierfrei.config import Settings  # noqa: E402

TASKS_DIR = REPO.parent / "barrierfrei-tasks"
DEFAULT_MODEL = "muse-spark-1.3"  # Standard mode: Meta does not train on what we send.
DEFAULT_MAX_STEPS = 60
DEFAULT_TIMEOUT_MIN = 30  # a broken sandbox makes headless Muse Code wait forever instead of exiting
SECRETS = ("MODEL_API_KEY", "META_API_KEY", "MAPILLARY_TOKEN", "DB_API_KEY", "DB_CLIENT_ID")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")

PREAMBLE = """You are the executor on a two-agent team. A reviewer planned this task and will read
your diff before anything is committed. Do exactly the task below, nothing more.

Rules:
- Do not commit, push, create branches, or edit files outside this directory.
- Do not read, print, or create .env files or any secret. Tests must run without keys or network.
- Keep the change as small as the task allows. Match the existing style; no drive-by refactors.
- Do not run ruff, pytest or other verification commands: the reviewer runs the checks
  after reading your diff. Read the code carefully instead, and keep the formatting style.
- Finish with a short summary: what you changed and why, and anything you were unsure
  about or did not do.

Project rules (AGENTS.md):
{agents_md}

Task:
{task}
"""


def read_key() -> str:
    """The Meta key from the config layer: META_API_KEY if set, else MODEL_API_KEY (env or .env)."""
    settings = Settings(_env_file=REPO / ".env")
    secret = settings.meta_api_key or settings.model_api_key
    if secret is None:
        sys.exit("No Meta key found: set MODEL_API_KEY in .env (or META_API_KEY in the environment).")
    return secret.get_secret_value()


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def validate_slug(slug: str) -> None:
    """Slugs become paths and branch names, so reject anything that could escape TASKS_DIR."""
    if not SLUG.fullmatch(slug):
        sys.exit(f"Bad task name {slug!r}: use lowercase letters, digits and dashes (e.g. add-voice-input).")


def create_workspace(slug: str, base_sha: str) -> None:
    """Clone this repo into the task folder on a new branch `muse/<slug>` at base_sha.

    A full local clone, not a `git worktree`: a worktree's .git points back into this repo, and
    Muse Code's Windows sandbox then tries to change permissions inside our .git (and fails).
    The clone has its own .git, shares objects via hardlinks, and pushes straight to GitHub.
    """
    wt = worktree(slug)
    subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(REPO), str(wt)], check=True)
    git("checkout", "--quiet", "-b", f"muse/{slug}", base_sha, cwd=wt)
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO, capture_output=True, text=True)
    if origin.returncode == 0:
        git("remote", "set-url", "origin", origin.stdout.strip(), cwd=wt)


def _force_remove(func, path, _exc) -> None:
    """rmtree helper: git marks object files read-only, which Windows refuses to delete."""
    os.chmod(path, 0o700)
    func(path)


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
            # Muse Code 1.4 names the session in its stream header: {"stream": {"kind": "session", "id": ...}}
            if obj.get("kind") == "session" and isinstance(obj.get("id"), str):
                return obj["id"]
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


def kill_tree(proc: subprocess.Popen) -> None:
    """Terminate a process and all its descendants, then wait for it to exit."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        import signal

        os.killpg(proc.pid, signal.SIGKILL)  # start_new_session made it the group leader
    proc.wait()


def find_muse() -> str:
    muse = shutil.which("muse")
    if not muse:
        sys.exit("`muse` is not on PATH. Install Muse Code first (see the setup guide).")
    return muse


def sandbox_ready(muse: str) -> None:
    """On Windows, stop early with the fix when Muse Code's sandbox was never set up."""
    if sys.platform != "win32":
        return
    proc = subprocess.run([muse, "sandbox", "windows", "check"], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(
            "Muse Code's Windows sandbox is not set up, so every agent command would fail.\n"
            "Once, in PowerShell opened with 'Run as administrator':\n"
            "  muse sandbox windows setup\n"
            "  muse sandbox windows check    # should print status=ready\n\n" + proc.stdout.strip()
        )


def run_muse(
    slug: str, prompt: str, model: str, max_steps: int, session: str | None, timeout_min: int
) -> int:
    muse = find_muse()
    sandbox_ready(muse)
    prompt_file = TASKS_DIR / f"{slug}.prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    cmd = [muse, "exec", "--model", model, "--max-model-steps", str(max_steps), "--json"]
    cmd += ["--prompt-file", str(prompt_file)]
    cmd += ["--no-foreign-personal-context"]  # don't load your personal Claude Code / Codex rules
    if session:
        cmd += ["--session-id", session]
    key = read_key()
    env = {k: v for k, v in os.environ.items() if k not in SECRETS}  # agent gets only its own key
    env["META_API_KEY"] = key
    log = TASKS_DIR / f"{slug}.jsonl"
    errlog = TASKS_DIR / f"{slug}.stderr.log"  # kept apart so the .jsonl stays pure JSON events
    print(f"Muse Code is working on '{slug}' ({model}, at most {max_steps} steps / {timeout_min} min)...")
    with log.open("a", encoding="utf-8") as out, errlog.open("a", encoding="utf-8") as err:
        # Own process group, so on timeout we can stop Muse Code *and* every tool it spawned
        # before reading the diff; killing only the parent could leave children editing files.
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {}
        proc = subprocess.Popen(
            cmd, cwd=worktree(slug), env=env, stdout=out, stderr=err, start_new_session=True, **group
        )
        try:
            code = proc.wait(timeout=timeout_min * 60)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            print(f"Stopped Muse Code after {timeout_min} min; see {errlog.name} and the end of {log.name}.")
            return 124
    print(f"muse exec exited with {code} (0 = the run finished, not that the work is right).")
    return code


def save_diff(slug: str) -> None:
    """Write the task clone's full diff for review. Only git runs here, no agent-written code."""
    wt = worktree(slug)
    git("add", "-A", "--intent-to-add", cwd=wt)  # so new files show up in the diff
    (TASKS_DIR / f"{slug}.diff").write_text(git("diff", "HEAD", cwd=wt), encoding="utf-8")
    print("\n--- changed files")
    print(git("diff", "HEAD", "--stat", cwd=wt).strip() or "(no changes)")
    print(f"\nFull diff: {TASKS_DIR / f'{slug}.diff'}")
    print(f"Agent log: {TASKS_DIR / f'{slug}.jsonl'} (warnings: {slug}.stderr.log)")


def check(slug: str) -> bool:
    """Run the repo's checks in the task clone. Returns True when all pass.

    This executes agent-written code (tests) on your machine outside Muse Code's sandbox, so
    `run` and `resume` never call it: read the diff first, then run `check` yourself.
    """
    wt = worktree(slug)
    # Put the task clone first on the import path; otherwise an editable install of the main
    # checkout would be imported and the tests would silently check the wrong code.
    env = {**os.environ, "PYTHONPATH": str(wt)}
    for secret in SECRETS:
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
    return all(results.values())


def cmd_run(args: argparse.Namespace) -> int:
    task_file = Path(args.task_file)
    slug = args.slug or task_file.stem
    validate_slug(slug)
    if worktree(slug).exists():
        sys.exit(f"{worktree(slug)} already exists. Use `resume {slug} ...` or `clean {slug}` first.")
    sandbox_ready(find_muse())  # before creating anything, so a failed preflight leaves no half task
    TASKS_DIR.mkdir(exist_ok=True)
    base = args.base or "HEAD"
    create_workspace(slug, git("rev-parse", base).strip())
    agents = REPO / "AGENTS.md"
    prompt = PREAMBLE.format(
        agents_md=agents.read_text(encoding="utf-8") if agents.exists() else "(none)",
        task=task_file.read_text(encoding="utf-8"),
    )
    code = run_muse(slug, prompt, args.model, args.max_steps, None, args.timeout)
    meta = {
        "slug": slug,
        "base": git("rev-parse", base).strip(),
        "model": args.model,
        "session": find_session_id(TASKS_DIR / f"{slug}.jsonl"),
    }
    meta_path(slug).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    save_diff(slug)
    print(f"\nReview the diff, then run checks: python tools/delegate.py check {slug}")
    return code


def cmd_resume(args: argparse.Namespace) -> int:
    if not worktree(args.slug).exists():
        sys.exit(f"No task '{args.slug}'. Start it with `run`.")
    meta = json.loads(meta_path(args.slug).read_text(encoding="utf-8"))
    session = args.session or meta.get("session")
    if not session:
        sys.exit(f"No session id found in {args.slug}.jsonl; pass one with --session.")
    code = run_muse(
        args.slug, args.message, args.model or meta["model"], args.max_steps, session, args.timeout
    )
    save_diff(args.slug)
    print(f"\nReview the diff, then run checks: python tools/delegate.py check {args.slug}")
    return code


def cmd_check(args: argparse.Namespace) -> int:
    return 0 if check(args.slug) else 1


def cmd_clean(args: argparse.Namespace) -> int:
    if worktree(args.slug).exists():
        shutil.rmtree(worktree(args.slug), onerror=_force_remove)
    for suffix in (".json", ".jsonl", ".stderr.log", ".diff", ".prompt.md"):
        (TASKS_DIR / f"{args.slug}{suffix}").unlink(missing_ok=True)
    print(f"Removed task '{args.slug}'.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="start a new task in its own clone")
    p.add_argument("task_file", help="Markdown file describing the task")
    p.add_argument("--slug", help="task name (default: the task file's name)")
    p.add_argument("--base", help="commit or branch to start from (default: HEAD)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_MIN, help="minutes before giving up")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("resume", help="send review feedback to the same Muse Code session")
    p.add_argument("slug")
    p.add_argument("message", help="what to fix")
    p.add_argument("--session", help="override the session id read from the log")
    p.add_argument("--model")
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_MIN, help="minutes before giving up")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("check", help="re-run checks and refresh the diff")
    p.add_argument("slug")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("clean", help="remove a task's clone and logs")
    p.add_argument("slug")
    p.set_defaults(func=cmd_clean)

    args = parser.parse_args()
    if getattr(args, "slug", None):
        validate_slug(args.slug)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
