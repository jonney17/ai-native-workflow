#!/usr/bin/env python3
"""Launch one lane session with telemetry attributes attached.

It sets the Copilot OTel environment, tags the session with workflow.* resource
attributes, records lane_started / lane_exited in the ticket's events.log, and then
runs your command. Telemetry is written to otel-dump/ by default (file exporter).

Usage:
  python scripts/wf_launch.py --ticket PROJ-123 --run 1 --lane dev --repo repo-a --attempt 1 -- copilot ...
  python scripts/wf_launch.py --ticket PROJ-123 --lane planner --print-env      # show env only
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANES = ("planner", "qe", "dev", "reviewer", "inspect-adapt")  # keep in sync with config/lanes.yaml
KIND_LANES = {  # keep in sync with kinds in config/lanes.yaml
    "story": LANES,
    "chore": LANES,
    "spike": ("planner", "inspect-adapt"),
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value):
    """Resource attribute values must not contain , = or spaces."""
    return str(value).replace(",", "_").replace("=", "_").replace(" ", "_")


def log_event(events, actor, event, detail):
    with open(events, "a", encoding="utf-8") as f:
        f.write(f"{now()}\t{actor}\t{event}\t{json.dumps(detail, sort_keys=True)}\n")


def read_registry(path):
    """Read config/repos.yaml or repos.local.yaml: repos -> name -> key: value (two levels only)."""
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


def resolve_repo(name, local):
    """Local path of a repo: repos.local.yaml first, then $WF_REPOS_ROOT/<name>. None if not found."""
    candidates = []
    if local.get(name, {}).get("local_path"):
        candidates.append(local[name]["local_path"])
    if os.environ.get("WF_REPOS_ROOT"):
        candidates.append(os.path.join(os.environ["WF_REPOS_ROOT"], name))
    for c in candidates:
        p = Path(os.path.expandvars(os.path.expanduser(c)))
        if p.is_dir():
            return p
    return None


def read_kind(manifest):
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.startswith("kind:"):
                return line.split(":", 1)[1].split("#")[0].strip() or "story"
    return "story"


def active_overlay(lane):
    d = ROOT / "overlay" / lane
    return sorted(p.stem for p in d.glob("L-*.md")) if d.is_dir() else []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--lane", required=True, choices=LANES)
    ap.add_argument("--repo", default="", help="for the dev lane: the repo this instance works in")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--team", default="", help="optional workflow.team attribute")
    ap.add_argument("--exporter", choices=("file", "otlp-http"), default="file")
    ap.add_argument("--endpoint", default="http://localhost:14318", help="used with --exporter otlp-http")
    ap.add_argument("--print-env", action="store_true", help="print the environment and exit")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="command to run, after --")
    args = ap.parse_args()

    run_dir = ROOT / "tickets" / args.ticket / f"run-{args.run}"
    if not run_dir.is_dir():
        sys.exit(f"error: {run_dir} not found; create it with scripts/new_ticket.py")
    events = run_dir / "events.log"
    kind = read_kind(run_dir / "manifest.yaml")
    if kind not in KIND_LANES:
        sys.exit(f"error: unknown kind {kind!r} in manifest")
    if args.lane not in KIND_LANES[kind]:
        sys.exit(f"error: lane {args.lane!r} does not apply to a {kind} (allowed: {', '.join(KIND_LANES[kind])})")

    cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
    launch_id = uuid.uuid4().hex[:12]
    overlay = active_overlay(args.lane)

    registry = read_registry(ROOT / "config" / "repos.yaml")
    local = read_registry(ROOT / "config" / "repos.local.yaml")
    paths = {}
    for name in registry:
        found = resolve_repo(name, local)
        if found is not None:
            paths[name] = found
    repo_cwd = None
    if args.repo and registry:
        if args.repo not in registry:
            sys.exit(f"error: repo {args.repo!r} is not in config/repos.yaml (known: {', '.join(registry)})")
        repo_cwd = paths.get(args.repo)
        if repo_cwd is None:
            sys.exit(f"error: no local path found for {args.repo!r}. Set it in config/repos.local.yaml "
                     f"or put the repo at $WF_REPOS_ROOT/{args.repo}")
    elif args.repo:
        print("note: config/repos.yaml has no repos; running in the current directory", file=sys.stderr)

    attrs = {
        "workflow.ticket": args.ticket,
        "workflow.kind": kind,
        "workflow.run": args.run,
        "workflow.lane": args.lane,
        "workflow.attempt": args.attempt,
        "workflow.launch_id": launch_id,
    }
    if args.repo:
        attrs["workflow.repo"] = args.repo
    if args.team:
        attrs["workflow.team"] = args.team
    if overlay:
        attrs["workflow.overlay"] = "+".join(overlay)
    ours = ",".join(f"{k}={clean(v)}" for k, v in attrs.items())
    existing = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")

    env = {
        "COPILOT_OTEL_ENABLED": "true",
        "COPILOT_OTEL_EXPORTER_TYPE": args.exporter,
        "OTEL_RESOURCE_ATTRIBUTES": f"{existing},{ours}" if existing else ours,
    }
    env["WF_REPO_PATHS"] = json.dumps({k: str(v) for k, v in paths.items()})
    if args.repo:
        env["WF_REPO"] = args.repo
        if repo_cwd is not None:
            env["WF_REPO_PATH"] = str(repo_cwd)
    dump = None
    if args.exporter == "file":
        name = "-".join(p for p in (args.lane, args.repo) if p) + f"-a{args.attempt}-{launch_id}.jsonl"
        dump = ROOT / "otel-dump" / args.ticket / f"run-{args.run}" / name
        dump.parent.mkdir(parents=True, exist_ok=True)
        env["COPILOT_OTEL_FILE_EXPORTER_PATH"] = str(dump)
    else:
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = args.endpoint

    if args.print_env or not cmd:
        for k, v in env.items():
            print(f"{k}={v}")
        print(f"# launch_id={launch_id} (not recorded: nothing was launched)")
        return

    exe = shutil.which(cmd[0])
    if exe is None:
        sys.exit(f"error: command not found: {cmd[0]}")

    log_event(events, "launcher", "lane_started", {
        "launch_id": launch_id, "kind": kind, "lane": args.lane, "repo": args.repo, "attempt": args.attempt,
        "overlay": overlay, "exporter": args.exporter,
        "dump": dump.relative_to(ROOT).as_posix() if dump else None, "command": Path(cmd[0]).name,
    })
    started = time.time()
    code = 1
    try:
        code = subprocess.call([exe] + cmd[1:], env={**os.environ, **env},
                               cwd=str(repo_cwd) if repo_cwd is not None else None)
    except KeyboardInterrupt:
        code = 130
    finally:
        log_event(events, "launcher", "lane_exited", {
            "launch_id": launch_id, "returncode": code, "seconds": round(time.time() - started, 1),
        })
    sys.exit(code)


if __name__ == "__main__":
    main()
