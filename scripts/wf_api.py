#!/usr/bin/env python3
"""JSON for the web UI (ui/server.js). The UI is JavaScript; everything that knows about runs and lanes is here.

  wf_api.py board                      the team board and the ticket list
  wf_api.py state TICKET               one ticket: lanes, gates, files, next actions
  wf_api.py file TICKET PATH           text of a file in the run folder
  wf_api.py act                        JSON on stdin: {ticket, cmd, lane, repo, gate, text} -> runs a wf_run.py command
  wf_api.py intake TICKET              create the ticket run from config/backlog.yaml and mark it ready
  wf_api.py acp LANE                   the ACP command for one lane (JSON list)
All output is one JSON document on stdout.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wf_status as S  # noqa: E402
import wf_state as W  # noqa: E402
import wf_run as R  # noqa: E402
import yaml  # noqa: E402

ROOT = S.ROOT
PY = sys.executable
NAME = re.compile(r"^[A-Za-z0-9._-]+$")
LANES = ("planner", "dev", "qe", "reviewer", "inspect-adapt")
TITLES = {"planner": "Planner (architecture)", "dev": "Dev", "qe": "QE", "reviewer": "Reviewer", "inspect-adapt": "Inspect & Adapt"}
COMMANDS = ("ready", "approve", "reject", "answer", "retry", "accept", "deployed")


# ---------------------------------------------------------------- data

def backlog():
    p = ROOT / "config" / "backlog.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("tickets", []) if p.exists() else []


def ticket_dirs():
    d = ROOT / "tickets"
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("_") and any(p.glob("run-*"))) if d.is_dir() else []


def question_of(run, lane, repo=""):
    unit = f"{lane}-{repo}" if repo else lane
    for e in reversed(run.events()):
        d = e["detail"]
        if e["event"] == "question_raised" and (e["actor"] == unit or (not repo and d.get("lane") == lane) or (repo and d.get("repo") == repo)):
            return " ".join(str(d[k]) for k in ("question", "reason", "needed", "text", "blocker") if d.get(k))
    return ""


def last_log(run_dir, lane, repo=""):
    unit = f"{lane}-{repo}" if repo else lane
    logs = sorted((run_dir / "logs").glob(f"{unit}-a*.log"), key=lambda p: p.stat().st_mtime) if (run_dir / "logs").is_dir() else []
    return logs[-1].relative_to(run_dir).as_posix() if logs else ""


def tail(path, n=3000):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-n:]


def ticket_state(ticket):
    res = S.analyze(ticket)
    run = W.Run(ticket, res["run"])
    for lane, rec in res["lanes"].items():
        rec["question"] = question_of(run, lane) if rec["state"] == "blocked" else ""
        rec["log"] = last_log(run.dir, lane)
        for repo, r in (rec.get("repos") or {}).items():
            r["log"] = last_log(run.dir, lane, repo)
            r["question"] = question_of(run, lane, repo) if r["state"] == "blocked" else ""
    adir = run.dir / "artifacts"
    res["files"] = sorted(p.relative_to(run.dir).as_posix() for p in adir.rglob("*") if p.is_file() and p.name != ".gitkeep") if adir.is_dir() else []
    return res


RANK = {"idle": 0, "waiting": 1, "starting": 2, "working": 3, "waiting for you": 4, "blocked": 5, "failed": 6}


def board():
    """Each lane across all tickets and one-off tasks: an overall status and the lines behind it."""
    lanes = {l: {"title": TITLES[l], "status": "idle", "lines": [], "ask_for": "", "questions": [], "live": None} for l in LANES}
    cfg = S.load_yaml(ROOT / "config" / "lanes.yaml")
    for l in LANES:
        lanes[l]["ask_for"] = str(((cfg.get("lanes") or {}).get(l) or {}).get("ask_for", ""))

    def add(lane, status, text, ticket="", task=""):
        lanes[lane]["lines"].append({"status": status, "text": text, "ticket": ticket, "task": task})
        if RANK[status] > RANK[lanes[lane]["status"]]:
            lanes[lane]["status"] = status

    summaries = {}
    for t in ticket_dirs():
        try:
            res = S.analyze(t)
        except Exception as e:  # a broken ticket must not hide the board
            summaries[t] = {"status": f"error: {e}", "detail": ""}
            continue
        summaries[t] = {"status": res["status"], "detail": res["detail"]}
        if res["status"] in ("Done", "Escalated"):
            continue
        for lane, rec in res["lanes"].items():
            st = rec["state"]
            if lane not in lanes or st in ("done", "disabled"):
                continue
            who = "blocked" if st == "blocked" else "failed" if st == "failed" else "working" if st == "working" else "starting" if st in ("ready", "stale") else "waiting"
            add(lane, who, f"{t}: {rec['note'] or st}", ticket=t)
            run = W.Run(t, res["run"])
            units = [(repo, x["state"]) for repo, x in (rec.get("repos") or {}).items()] or [("", st)]
            for repo, ust in units:
                if ust == "blocked":
                    lanes[lane].setdefault("questions", []).append({"ticket": t, "repo": repo, "question": question_of(run, lane, repo)})
                if ust == "working":
                    lg = last_log(run.dir, lane, repo)
                    if lg:
                        lanes[lane]["live"] = {"unit": f"{t} {lane}{'-' + repo if repo else ''}", "text": tail(run.dir / lg)}
        g = res.get("gates") or {}
        if (g.get("planner") or {}).get("state") == "awaiting_approval":
            add("planner", "waiting for you", f"{t}: approve the plan", ticket=t)
        if (g.get("reviewer") or {}).get("state") == "awaiting_approval":
            add("reviewer", "waiting for you", f"{t}: approve the review", ticket=t)
        if res["status"] == "Awaiting acceptance":
            add("inspect-adapt", "waiting for you", f"{t}: accept the ticket", ticket=t)
    items = []
    for b in backlog():
        s = summaries.get(b["id"])
        items.append({"id": b["id"], "title": b["title"], "repos": b.get("repos", []), "state": s["status"] if s else "Not started", "detail": s["detail"] if s else ""})
    return {"lanes": lanes, "tickets": items}


# ---------------------------------------------------------------- actions

def read_file(ticket, rel):
    res = S.analyze(ticket)
    base = (ROOT / res["path"]).resolve()
    p = (base / rel).resolve()
    if base not in p.parents or not p.is_file():
        return "(not found)"
    text = p.read_text(encoding="utf-8", errors="replace")
    return text[-20000:]


def act(body):
    t, cmd = str(body.get("ticket", "")), str(body.get("cmd", ""))
    if not NAME.match(t) or cmd not in COMMANDS:
        return "refused: bad ticket or command"
    args = [PY, str(HERE / "wf_run.py"), cmd, "--ticket", t, "--by", "ui"]
    lane, repo, text, gate = (str(body.get(k) or "") for k in ("lane", "repo", "text", "gate"))
    if lane:
        if lane not in LANES:
            return "refused: bad lane"
        args += ["--lane", lane]
    if repo:
        if not NAME.match(repo):
            return "refused: bad repo"
        args += ["--repo", repo]
    if gate in ("planner", "reviewer"):
        args += ["--gate", gate]
    if cmd == "answer":
        args += ["--text", text]
    if cmd == "reject":
        args += ["--reason", text]
    r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout + r.stderr).strip() or "done"


def run_cmd(*args):
    r = subprocess.run([PY, *map(str, args)], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError((r.stdout + r.stderr).strip())
    return r.stdout


def intake(tid):
    b = next((x for x in backlog() if x["id"] == tid), None)
    if b is None or not NAME.match(tid):
        raise RuntimeError("unknown ticket")
    if not (ROOT / "tickets" / tid).exists():
        run_cmd(HERE / "new_ticket.py", "--ticket", tid, "--kind", "story" if b.get("prd") else "chore", *(["--prd", b["prd"]] if b.get("prd") else ["--origin", "tech-debt"]))
        crit = "\n".join(f"| J-{i} | {c} |" for i, c in enumerate(b.get("criteria", []), 1))
        ctx = ROOT / "tickets" / tid / "run-1" / "artifacts" / "ticket-context.md"
        ctx.write_text(f"# {tid}: {b['title']}\n\nPRD refs: {b.get('prd') or 'none'}.\n\n## Description\n{b.get('description', '')}\n\n"
                       f"## {'Jira criteria' if b.get('prd') else 'Done criteria'}\n| Id | Criterion |\n|----|-----------|\n{crit}\n\n"
                       f"## Notes\nRepos involved: {', '.join(b.get('repos', []))}.\n", encoding="utf-8", newline="\n")
        run_cmd(HERE / "wf_run.py", "ready", "--ticket", tid, "--by", "ui")
    return {"path": S.analyze(tid)["path"]}


def acp(lane):
    if lane not in LANES:
        raise RuntimeError("bad lane")
    runner = R.load_cfg("runner.yaml")
    fmt = {"lane": lane, "root": str(ROOT), "repo_path": "", "repo": "", "ticket": "", "run": 0}
    return R.acp_cmd(runner, fmt, "")


def main():
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    try:
        if cmd == "board":
            out = board()
        elif cmd == "state":
            out = ticket_state(args[0])
        elif cmd == "file":
            out = {"text": read_file(args[0], args[1])}
        elif cmd == "act":
            out = {"message": act(json.loads(sys.stdin.read() or "{}"))}
        elif cmd == "intake":
            out = intake(args[0])
        elif cmd == "acp":
            out = acp(args[0])
        else:
            sys.exit(__doc__)
    except Exception as e:   # one JSON error the server can show
        out = {"error": str(e)}
    sys.stdout.write(json.dumps(out, default=str))


if __name__ == "__main__":
    main()
