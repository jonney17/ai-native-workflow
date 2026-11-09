#!/usr/bin/env python3
"""The Auditor: ONE agent for the whole team, separate from the ticket agents. Config: config/auditor.yaml.

While it runs it looks at every ticket every poll_seconds. When a run's status is in audit_when (default Done) and it
has no audit yet, it starts one agent session that reads the run (events.log, decisions.md, logs/, artifacts/) and
writes audits/<ticket>/run-<n>.md. It only reads tickets: it never blocks, reworks or changes one.
It is not started by default: start it from the playbook's Auditor card, with `watch` below, or set auto_start: true.

Commands (run from the project root):
  watch [--once] [--verbose]       keep auditing tickets as they become Done (--once: one pass, then exit)
  audit --ticket T [--run N]       audit one run now, even if it was audited before
  status                           JSON: the Auditor's view of every ticket
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wf_status as S  # noqa: E402
import wf_run as R  # noqa: E402

ROOT = S.ROOT
AUDITS = ROOT / "audits"
EVENTS = AUDITS / "events.log"


DEFAULTS = {"instructions": "wf-auditor", "model": "", "poll_seconds": 30, "audit_when": ["Done"], "auto_start": False}


def task_message(fmt):
    """The first message of an audit session. Fixed on purpose: how to audit lives in the agent file; this only names
    that file and gives the facts of this run, above all audit_file, which wf_audit.py checks afterwards."""
    return (f"You are the auditor. Read .github/agents/{fmt['instructions']}.agent.md first and follow it exactly.\n"
            f"Audit one finished ticket run. Read it, never change it.\n"
            f"root: {fmt['root']}\nrun_dir: {fmt['run_dir']}\n"
            f"ticket: {fmt['ticket']}, run: {fmt['run']}, attempt: {fmt['attempt']}\naudit_file: {fmt['audit_file']}\n")


def config():
    """config/auditor.yaml with defaults (older setups kept it under `auditor:` in runner.yaml)."""
    cfg = dict(DEFAULTS)
    cfg.update(R.load_cfg("runner.yaml").get("auditor") or {})
    cfg.update(R.load_cfg("auditor.yaml") or {})
    if cfg.get("agent") and not (R.load_cfg("auditor.yaml") or {}).get("instructions"):
        cfg["instructions"] = cfg["agent"]   # older auditor.yaml called the field `agent`
    cfg["audit_when"] = [str(s) for s in cfg.get("audit_when") or ["Done"]]
    return cfg


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_event(event, detail):
    AUDITS.mkdir(exist_ok=True)
    with open(EVENTS, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{now()}\tauditor\t{event}\t{json.dumps(detail, sort_keys=True)}\n")


def events():
    return S.read_events(EVENTS) if EVENTS.exists() else []


def report_path(ticket, run):
    return AUDITS / ticket / f"run-{run}.md"


def working_path(ticket, run):
    return AUDITS / ticket / f"run-{run}.working"


def attempts(ticket, run):
    """Failed tries since the last `audit` request (a request by hand starts the count again)."""
    n = 0
    for e in events():
        d = e["detail"]
        if d.get("ticket") == ticket and d.get("run") == run:
            if e["event"] == "audit_requested":
                n = 0
            elif e["event"] == "audit_finished" and d.get("outcome") != "ok":
                n += 1
    return n


def tickets():
    d = ROOT / "tickets"
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("_") and any(p.glob("run-*"))) if d.is_dir() else []


def status():
    """Every ticket as the Auditor sees it: state is one of not_done, pending, auditing, audited, failed."""
    max_tries = int((R.load_cfg("budgets.yaml").get("per_agent") or {}).get("max_attempts", 3))
    lease = float((R.load_cfg("budgets.yaml").get("per_agent") or {}).get("lease_minutes", 30)) * 60
    audit_when = config()["audit_when"]
    out = []
    for t in tickets():
        try:
            res = S.analyze(t)
        except Exception as e:  # a broken ticket must not stop the Auditor
            out.append({"ticket": t, "run": None, "ticket_status": f"error: {e}", "state": "not_done"})
            continue
        run = res["run"]
        w = working_path(t, run)
        item = {"ticket": t, "run": run, "ticket_status": res["status"], "report": "", "log": ""}
        if report_path(t, run).exists():
            item["state"], item["report"] = "audited", report_path(t, run).relative_to(ROOT).as_posix()
        elif w.exists() and time.time() - w.stat().st_mtime < lease:
            item["state"] = "auditing"
        elif res["status"] not in audit_when:
            item["state"] = "not_done"
        elif attempts(t, run) >= max_tries:
            item["state"] = "failed"
        else:
            item["state"] = "pending"
        logs = sorted((AUDITS / t).glob(f"run-{run}-a*.log"), key=lambda p: p.stat().st_mtime) if (AUDITS / t).is_dir() else []
        if logs:
            item["log"] = logs[-1].relative_to(ROOT).as_posix()
        out.append(item)
    return out


def audit(ticket, run, verbose=False):
    """One agent session for one finished run. Returns True when the report was written."""
    runner = R.load_cfg("runner.yaml")
    cfg = config()
    run_dir = ROOT / "tickets" / ticket / f"run-{run}"
    out = report_path(ticket, run)
    out.parent.mkdir(parents=True, exist_ok=True)
    attempt = attempts(ticket, run) + 1
    fmt = {"agent": "auditor", "lane": "auditor", "instructions": str(cfg["instructions"]), "repo": "", "repo_path": "", "repo_line": "", "extra_line": "", "ticket": ticket, "run": run,
           "attempt": attempt, "root": str(ROOT), "run_dir": str(run_dir), "audit_file": str(out)}
    prompt = task_message(fmt)
    log = out.parent / f"run-{run}-a{attempt}.log"
    working = working_path(ticket, run)
    working.write_text(now(), encoding="utf-8")
    env = {"WF_AGENT": "auditor", "WF_TICKET": ticket, "WF_RUN": str(run), "WF_ATTEMPT": str(attempt), "WF_AUDIT_FILE": str(out)}
    echo = (lambda line: print(f"[auditor] {line}", flush=True)) if verbose else None
    print(f"[auditor] auditing {ticket} run {run} (attempt {attempt})", flush=True)
    log_event("audit_started", {"ticket": ticket, "run": run, "attempt": attempt})
    started, rc, msg = time.time(), 0, ""
    with open(log, "a", encoding="utf-8", errors="replace") as lf:
        try:
            if R.use_acp(runner):
                import wf_acp as A
                model = ["--model", str(cfg["model"])] if cfg.get("model") and not os.environ.get("WF_ACP_CMD") else []
                sess = A.Session(R.acp_cmd(runner, fmt, "") + model, ROOT, env, lf, echo)
                sess.idle_seconds = int((runner.get("acp") or {}).get("idle_seconds") or 0)
                try:
                    _, msg = sess.prompt(prompt)
                finally:
                    sess.close()
            else:   # print mode: the same one-shot command the agents use
                import subprocess
                base = json.loads(os.environ["WF_AGENT_CMD"]) if os.environ.get("WF_AGENT_CMD") else list((runner.get("agent") or {}).get("command") or [])
                cmd = [str(x).format(**dict(fmt, prompt=prompt)) for x in base]
                if cfg.get("model") and not os.environ.get("WF_AGENT_CMD"):
                    cmd += ["--model", str(cfg["model"])]
                rc = subprocess.call(cmd, cwd=str(ROOT), stdout=lf, stderr=subprocess.STDOUT, env={**os.environ, **env})
        except Exception as e:
            rc, msg = 1, str(e)
            lf.write(f"\nERROR: {e}\n")
    working.unlink(missing_ok=True)
    ok = out.exists()
    outcome = "ok" if ok else ("crashed" if rc else "incomplete")
    log_event("audit_finished", {"ticket": ticket, "run": run, "attempt": attempt, "outcome": outcome,
                                 "seconds": round(time.time() - started, 1), "log": log.relative_to(ROOT).as_posix()})
    print(f"[auditor] {ticket} run {run}: {outcome}" + ("" if ok else f"; see {log.relative_to(ROOT).as_posix()}"), flush=True)
    return ok


def watch(once=False, verbose=False):
    cfg = config()
    poll = max(5.0, float(cfg.get("poll_seconds") or 30))
    print(f"[auditor] watching tickets every {poll:.0f}s for {', '.join(cfg['audit_when'])}; audits go to audits/<ticket>/run-<n>.md", flush=True)
    said = None
    while True:
        todo = [x for x in status() if x["state"] == "pending"]
        for x in todo:
            audit(x["ticket"], x["run"], verbose)
        if once:
            return
        if not todo:
            line = "idle: no finished ticket waits for an audit"
            if line != said:
                print(f"[auditor] {line}", flush=True)
                said = line
        else:
            said = None
        time.sleep(poll)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["watch", "audit", "status"])
    ap.add_argument("--ticket")
    ap.add_argument("--run", type=int)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if args.command == "status":
        print(json.dumps(status(), indent=2))
    elif args.command == "watch":
        try:
            watch(args.once, args.verbose)
        except KeyboardInterrupt:
            print("[auditor] stopped", flush=True)
    else:
        if not args.ticket:
            sys.exit("error: --ticket is required")
        run = args.run or S.analyze(args.ticket)["run"]
        log_event("audit_requested", {"ticket": args.ticket, "run": run})
        report_path(args.ticket, run).unlink(missing_ok=True)
        sys.exit(0 if audit(args.ticket, run, True) else 1)


if __name__ == "__main__":
    main()
