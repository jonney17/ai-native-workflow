#!/usr/bin/env python3
"""Start one agent session and record it in the run's events.log (agent_started, agent_exited).

The orchestrator (wf_run.py) calls this; you can also call it by hand:
  python scripts/wf_launch.py --ticket PROJ-123 --run 1 --agent dev --repo repo-a --attempt 1 -- copilot ...
The command runs from the workflow root. A repo's local path, when known, is passed in $WF_REPO_PATH.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wf_config as C  # noqa: E402  (agents and kinds come from config/agents.yaml)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_event(events, actor, event, detail):
    with open(events, "a", encoding="utf-8") as f:
        f.write(f"{now()}\t{actor}\t{event}\t{json.dumps(detail, sort_keys=True)}\n")


def read_registry(path):
    """Read config/repos.yaml (or the optional repos.local.yaml): repos -> name -> key: value (two levels only)."""
    repos = {}
    if not path.exists():
        return repos
    in_repos, current = False, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #")[0].rstrip()
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_repos, current = text.startswith("repos:"), None
        elif in_repos and indent == 2 and text.endswith(":"):
            current = text[:-1].strip()
            repos[current] = {}
        elif in_repos and current and indent >= 4 and ":" in text:
            key, value = text.split(":", 1)
            repos[current][key.strip()] = value.strip().strip("\"'")
    return repos


def resolve_repo(name, registry, local=None):
    """Local path of a repo, first one that exists: the private override config/repos.local.yaml (optional, git-ignored),
    $WF_REPOS_ROOT/<name>, then `local_path` in config/repos.yaml (a relative path is under $WF_REPOS_ROOT, or else
    next to the workflow folder). None if not found."""
    base = os.environ.get("WF_REPOS_ROOT") or str(ROOT.parent)
    candidates = []
    if (local or {}).get(name, {}).get("local_path"):
        candidates.append((local[name]["local_path"], base))
    if os.environ.get("WF_REPOS_ROOT"):
        candidates.append((name, os.environ["WF_REPOS_ROOT"]))
    if (registry or {}).get(name, {}).get("local_path"):
        candidates.append((registry[name]["local_path"], base))
    for c, b in candidates:
        p = Path(os.path.expandvars(os.path.expanduser(c)))
        p = p if p.is_absolute() else Path(b) / p
        if p.is_dir():
            return p
    return None


def read_kind(manifest):
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.startswith("kind:"):
                return line.split(":", 1)[1].split("#")[0].strip() or "story"
    return "story"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--agent", "--lane", dest="agent", required=True)
    ap.add_argument("--repo", default="", help="for the dev agent: the repo this instance works on")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("command", nargs=argparse.REMAINDER, help="command to run, after --")
    args = ap.parse_args()

    run_dir = ROOT / "tickets" / args.ticket / f"run-{args.run}"
    if not run_dir.is_dir():
        sys.exit(f"error: {run_dir} not found; create it with scripts/new_ticket.py")
    kind = read_kind(run_dir / "manifest.yaml")
    cfg = C.load()
    if kind not in cfg.kinds():
        sys.exit(f"error: unknown kind {kind!r} in manifest")
    if args.agent not in cfg.kind_agents(kind):
        sys.exit(f"error: agent {args.agent!r} does not apply to a {kind} (allowed: {', '.join(cfg.kind_agents(kind))})")
    cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not cmd:
        sys.exit("error: no command given after --")
    exe = shutil.which(cmd[0])
    if exe is None:
        sys.exit(f"error: command not found: {cmd[0]}")

    registry = read_registry(ROOT / "config" / "repos.yaml")
    local = read_registry(ROOT / "config" / "repos.local.yaml")
    paths = {n: found for n in registry if (found := resolve_repo(n, registry, local)) is not None}
    env = {"WF_TICKET": args.ticket, "WF_RUN": str(args.run), "WF_AGENT": args.agent, "WF_ATTEMPT": str(args.attempt),
           "WF_REPO_PATHS": json.dumps({k: str(v) for k, v in paths.items()})}
    if args.repo:
        env["WF_REPO"] = args.repo
        if args.repo in paths:
            env["WF_REPO_PATH"] = str(paths[args.repo])

    events = run_dir / "events.log"
    log_event(events, "launcher", "agent_started", {"kind": kind, "agent": args.agent, "repo": args.repo, "attempt": args.attempt})
    started, code = time.time(), 1
    try:
        code = subprocess.call([exe] + cmd[1:], env={**os.environ, **env})
    except KeyboardInterrupt:
        code = 130
    finally:
        log_event(events, "launcher", "agent_exited", {"agent": args.agent, "repo": args.repo, "returncode": code,
                                                      "seconds": round(time.time() - started, 1)})
    sys.exit(code)


if __name__ == "__main__":
    main()
