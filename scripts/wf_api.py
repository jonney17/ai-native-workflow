#!/usr/bin/env python3
"""JSON interface for workflow state and commands.

  wf_api.py board                      the team board and the ticket list
  wf_api.py state TICKET               one ticket: agents, gates, files, next actions
  wf_api.py file TICKET PATH           text of a file in the run folder
  wf_api.py act                        JSON on stdin: {ticket, cmd, agent, repo, gate, text} -> runs a wf_run.py command
  wf_api.py intake TICKET              create the ticket run from config/backlog.yaml and mark it ready
  wf_api.py acp AGENT                   the ACP command for one agent (JSON list)
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
import new_ticket as N  # noqa: E402
import yaml  # noqa: E402

ROOT = S.ROOT
PY = sys.executable
NAME = re.compile(r"^[A-Za-z0-9._-]+$")
def agent_titles():
    """Agent -> title, from config/agents.yaml."""
    cfg = S.config()
    return {n: cfg.agent("", n)["title"] for n in cfg.agent_names()}
COMMANDS = ("ready", "approve", "reject", "answer", "retry", "accept", "deployed")


# ---------------------------------------------------------------- data

def backlog():
    p = ROOT / "config" / "backlog.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("tickets", []) if p.exists() else []


def ticket_dirs():
    d = ROOT / "tickets"
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("_") and any(p.glob("run-*"))) if d.is_dir() else []


def question_of(run, agent, repo=""):
    unit = f"{agent}-{repo}" if repo else agent
    for e in reversed(run.events()):
        d = e["detail"]
        if e["event"] == "question_raised" and (e["actor"] == unit or (not repo and d.get("agent") == agent) or (repo and d.get("repo") == repo)):
            return " ".join(str(d[k]) for k in ("question", "reason", "needed", "text", "blocker") if d.get(k))
    return ""


def last_log(run_dir, agent, repo=""):
    unit = f"{agent}-{repo}" if repo else agent
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
    for agent, rec in res["agents"].items():
        rec["question"] = question_of(run, agent) if rec["state"] == "blocked" else ""
        rec["log"] = last_log(run.dir, agent)
        for repo, r in (rec.get("repos") or {}).items():
            r["log"] = last_log(run.dir, agent, repo)
            r["question"] = question_of(run, agent, repo) if r["state"] == "blocked" else ""
    adir = run.dir / "artifacts"
    res["files"] = sorted(p.relative_to(run.dir).as_posix() for p in adir.rglob("*") if p.is_file() and p.name != ".gitkeep") if adir.is_dir() else []
    return res


RANK = {"idle": 0, "waiting": 1, "starting": 2, "working": 3, "waiting for you": 4, "blocked": 5, "failed": 6}


def board():
    """Each agent across all tickets and one-off tasks: an overall status and the lines behind it."""
    titles = agent_titles()
    agents = {l: {"title": t, "status": "idle", "lines": [], "ask_for": "", "questions": [], "gates": [], "live": None} for l, t in titles.items()}
    cfg = R.C.load().raw
    for l in titles:
        agents[l]["ask_for"] = str(((cfg.get("agents") or {}).get(l) or {}).get("ask_for", ""))

    def add(agent, status, text, ticket="", task=""):
        agents[agent]["lines"].append({"status": status, "text": text, "ticket": ticket, "task": task})
        if RANK[status] > RANK[agents[agent]["status"]]:
            agents[agent]["status"] = status

    summaries = {}
    for t in ticket_dirs():
        try:
            res = S.analyze(t)
        except Exception as e:  # a broken ticket must not hide the board
            summaries[t] = {"status": f"error: {e}", "detail": ""}
            continue
        arts = []
        for name, a in res["artifacts"].items():
            if a.get("per_repo"):
                arts += [{"name": f"{name}/{r}", "status": x["status"], "reason": x.get("reason")} for r, x in (a.get("repos") or {}).items()]
            else:
                arts.append({"name": name, "status": a["status"], "reason": a.get("reason")})
        gates = [{"gate": g, "state": x["state"], "artifacts": x["artifacts"]} for g, x in (res.get("gates") or {}).items() if x["state"] != "not_required"]
        summaries[t] = {"status": res["status"], "detail": res["detail"], "artifacts": arts, "gates": gates}
        if res["status"] in ("Done", "Escalated"):
            continue
        for agent, rec in res["agents"].items():
            st = rec["state"]
            if agent not in agents or st in ("done", "disabled"):
                continue
            who = "blocked" if st == "blocked" else "failed" if st == "failed" else "working" if st == "working" else "starting" if st in ("ready", "stale") else "waiting"
            add(agent, who, f"{t}: {rec['note'] or st}", ticket=t)
            run = W.Run(t, res["run"])
            units = [(repo, x["state"]) for repo, x in (rec.get("repos") or {}).items()] or [("", st)]
            for repo, ust in units:
                if ust == "blocked":
                    agents[agent].setdefault("questions", []).append({"ticket": t, "repo": repo, "question": question_of(run, agent, repo)})
                if ust == "working":
                    lg = last_log(run.dir, agent, repo)
                    if lg:
                        agents[agent]["live"] = {"unit": f"{t} {agent}{'-' + repo if repo else ''}", "text": tail(run.dir / lg)}
        g = res.get("gates") or {}
        for gl, gx in g.items():
            if gx.get("state") == "awaiting_approval" and gl in agents:
                add(gl, "waiting for you", f"{t}: approve {', '.join(gx['artifacts'])}", ticket=t)
                agents[gl]["gates"].append({"ticket": t, "gate": gl, "cmd": "approve", "label": ", ".join(gx["artifacts"])})
        last = list(res["agents"])[-1] if res["agents"] else None
        if res["status"] == "Awaiting acceptance" and last in agents:
            add(last, "waiting for you", f"{t}: accept the ticket", ticket=t)
            agents[last]["gates"].append({"ticket": t, "gate": "", "cmd": "accept", "label": "ticket"})
    items = []
    for b in backlog():
        s = summaries.get(b["id"])
        items.append({"id": b["id"], "title": b["title"], "repos": b.get("repos", []), "state": s["status"] if s else "Not started", "detail": s["detail"] if s else "",
                      "artifacts": s.get("artifacts", []) if s else [], "gates": s.get("gates", []) if s else []})
    return {"agents": agents, "tickets": items}


# ---------------------------------------------------------------- actions

def read_file(ticket, rel):
    res = S.analyze(ticket)
    base = (ROOT / res["path"]).resolve()
    p = (base / rel).resolve()
    if base not in p.parents:
        return "(not found)"
    if p.is_dir():   # a folder such as changes/<repo>: show its files one after another
        parts = [f"=== {f.relative_to(p).as_posix()} ===\n" + f.read_text(encoding="utf-8", errors="replace") for f in sorted(p.rglob("*")) if f.is_file() and f.name != ".gitkeep"]
        return "\n\n".join(parts)[-40000:] or "(empty)"
    if not p.is_file():
        return "(not found)"
    return p.read_text(encoding="utf-8", errors="replace")[-40000:]


def act(body):
    t, cmd = str(body.get("ticket", "")), str(body.get("cmd", ""))
    if not NAME.match(t) or cmd not in COMMANDS:
        return "refused: bad ticket or command"
    args = [PY, str(HERE / "wf_run.py"), cmd, "--ticket", t, "--by", "ui"]
    agent, repo, text, gate = (str(body.get(k) or "") for k in ("agent", "repo", "text", "gate"))
    if agent:
        if agent not in agent_titles():
            return "refused: bad agent"
        args += ["--agent", agent]
    if repo:
        if not NAME.match(repo):
            return "refused: bad repo"
        args += ["--repo", repo]
    if gate in agent_titles():
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
    if (ROOT / "tickets" / "_archive" / tid).exists():
        raise RuntimeError(f"{tid} is archived; restore it before starting from the backlog")

    kind = "story" if b.get("prd") else "chore"
    prd_refs = [str(b["prd"])] if b.get("prd") else []
    origin = "" if b.get("prd") else "tech-debt"
    ticket_dir = ROOT / "tickets" / tid
    run_number = S.latest_run(ticket_dir) if ticket_dir.is_dir() else None
    if run_number is None:
        run_number = 1
        run_dir = ticket_dir / f"run-{run_number}"
        if run_dir.exists():
            raise RuntimeError(f"{run_dir} exists but has no valid run manifest; inspect it before starting")
        run_cmd(
            HERE / "new_ticket.py", "--ticket", tid, "--run", str(run_number),
            "--kind", kind, *(["--prd", *prd_refs] if prd_refs else ["--origin", origin]),
        )

    ctx = ticket_dir / f"run-{run_number}" / "artifacts" / "ticket-context.md"
    templates = {
        N.CONTEXT[kind](tid, run_number, prd_refs, origin),
        N.CONTEXT[kind](tid, run_number, [], ""),
    }
    if not ctx.is_file() or ctx.read_text(encoding="utf-8") in templates:
        criteria = [str(value) for value in (b.get("criteria") or [])]
        rows = "\n".join(f"| J-{i} | {criterion} |" for i, criterion in enumerate(criteria, 1))
        if not rows:
            rows = "| J-1 | none provided |"
        repos = [str(value) for value in (b.get("repos") or [])]
        ctx.write_text(
            N.header(tid, run_number)
            + f"# {tid}: {b.get('title') or tid}\n\n"
            + f"- Jira: {tid}\n"
            + f"- PRD requirements: {', '.join(prd_refs) or 'none'}\n"
            + f"- Affected repos (first guess): {', '.join(repos) or 'none provided'}\n\n"
            + f"## Ticket description\n\n{str(b.get('description') or 'none provided').strip()}\n\n"
            + f"## {'Jira acceptance criteria and scenarios' if prd_refs else 'Done criteria'}\n\n"
            + "| Id | Criterion |\n|---|---|\n"
            + rows
            + "\n\n## Relevant PRD sections\n\n"
            + (f"{', '.join(prd_refs)}\n" if prd_refs else "none provided\n"),
            encoding="utf-8",
            newline="\n",
        )
        run_cmd(
            HERE / "wf_run.py", "ready", "--ticket", tid,
            "--run", str(run_number), "--by", "ui",
        )

    res = S.analyze(tid, run_number)
    context_status = (res.get("artifacts") or {}).get("ticket-context.md", {}).get("status")
    if context_status not in ("ready", "approved"):
        raise RuntimeError(
            f"{tid} already has a customized ticket-context.md that is not ready; "
            "review it and mark it ready before starting the workflow"
        )
    return {"path": res["path"], "run": run_number}


def acp(agent):
    if agent not in agent_titles():
        raise RuntimeError("bad agent")
    runner = R.load_cfg("runner.yaml")
    fmt = {"agent": agent, "root": str(ROOT), "repo_path": "", "repo": "", "ticket": "", "run": 0}
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
