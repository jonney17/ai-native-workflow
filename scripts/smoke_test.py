#!/usr/bin/env python3
"""Smoke test: run whole tickets with the stub agents (demo/stub_*.py, no AI, no network) in a temporary copy of the
workflow, and check the result.

  python scripts/smoke_test.py            # all checks, a few seconds
  python scripts/smoke_test.py --keep     # keep the temporary copy and print where it is

Checks: 1. two tickets on the same repos run to Done and take turns on each repo (one writer per repo)
        2. an agent that keeps sending work back is stopped by per_run.max_reworks and the ticket is Escalated
        3. a design_gap sends design.md and tasks.md back, and a criterion that no task covers is reported
        4. a ticket without criteria or repos is refused, and an answer to the Architect is written into the ticket
        5. a config, manifest and event log written with the old name "lane" are still read
The copy is made first, so your tickets, audits and config are never touched.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {".git", "__pycache__", "audits", "locks", "patches", "tasks", "workspace", "workspace2", "workspace3"}
failures = []


def check(ok, what):
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        failures.append(what)


def run(args, cwd, env, timeout=300):
    r = subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


def status(cwd, env, ticket):
    """(status word, {agent: state}) from wf_status.py."""
    _, out = run(["scripts/wf_status.py", ticket], cwd, env)
    word = re.search(r"^Status:\s+(\S+)", out, re.M)
    agents = {}
    section = False
    for line in out.splitlines():
        if line.startswith("Agents"):
            section = True
        elif line.startswith("Artifacts"):
            section = False
        elif section:
            m = re.match(r"^  (\S+)\s+(\S+)", line)
            if m:
                agents[m.group(1)] = m.group(2)
    return (word.group(1) if word else "?"), agents


def new_ticket(cwd, env, ticket):
    code, out = run(["scripts/new_ticket.py", "--ticket", ticket, "--kind", "story"], cwd, env)
    if code:
        sys.exit(f"new_ticket.py failed:\n{out}")
    ctx = cwd / "tickets" / ticket / "run-1" / "artifacts" / "ticket-context.md"
    ctx.write_text("# Ticket context\n\nSmoke test.\n\n- Affected repos: shop-api, shop-web\n\n| Id | Criterion |\n|---|---|\n| J-1 | Given a, when b, then c |\n| J-2 | Given d, when e, then f |\n", encoding="utf-8")
    run(["scripts/wf_run.py", "ready", "--ticket", ticket], cwd, env)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true", help="keep the temporary copy")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="wf-smoke-"))
    cwd = tmp / "wf"
    shutil.copytree(ROOT, cwd, ignore=lambda d, names: [n for n in names if n in SKIP or (Path(d).name == "tickets" and n != "_template")])
    runner = cwd / "config" / "runner.yaml"
    runner.write_text(re.sub(r"(?m)^poll_seconds:.*$", "poll_seconds: 1", runner.read_text(encoding="utf-8")), encoding="utf-8")
    env = {**os.environ, "DEMO_PACE": "0",
           "WF_ACP_CMD": json.dumps([sys.executable, str(cwd / "demo" / "stub_acp.py")]),
           "WF_AGENT_CMD": json.dumps([sys.executable, str(cwd / "demo" / "stub_agent.py")])}
    print(f"smoke test in {tmp}")
    try:
        print("1. two tickets, same repos")
        for t in ("S-1", "S-2"):
            new_ticket(cwd, env, t)
        procs = [subprocess.Popen([sys.executable, "scripts/wf_run.py", "start", "--ticket", t, "--auto-approve"], cwd=cwd, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for t in ("S-1", "S-2")]
        outs = []
        for p in procs:
            try:
                outs.append(p.communicate(timeout=240)[0])
            except subprocess.TimeoutExpired:
                p.kill()
                outs.append(p.communicate()[0])
        for t in ("S-1", "S-2"):
            word, agents = status(cwd, env, t)
            check(word == "DONE" and agents and all(v == "done" for v in agents.values()), f"{t} is Done, every agent done ({word}, {agents})")
        check(any("waiting for repo" in o for o in outs), "one ticket waited for a repo the other was changing")
        check(not list((cwd / "locks").glob("*.json")) if (cwd / "locks").exists() else True, "no repo lock is left behind")

        shutil.copytree(cwd / "tickets" / "S-2", cwd / "tickets" / "S-0")   # a finished run, for check 4

        print("2. rework cap")
        code = ("import sys; sys.path.insert(0, 'scripts'); import wf_run as R, wf_state as W\n"
                "run = W.Run('S-1', 1)\n"
                "for _ in range(3): run.event('orchestrator', 'rework_requested', {'event': 'verify_failed', 'artifacts': ['changes/shop-api'], 'from': 'reviewer'})\n"
                "run.event('worker:reviewer', 'verify_failed', {'agent': 'reviewer', 'repo': 'shop-api', 'reason': 'again'})\n"
                "R.apply_proposals(run, 'smoke')\n")
        run(["-c", code], cwd, env)
        word, agents = status(cwd, env, "S-1")
        check(word == "ESCALATED" and agents.get("reviewer") == "failed", f"the fourth verify_failed escalates ({word}, reviewer {agents.get('reviewer')})")

        print("3. design gap")
        code = ("import sys; sys.path.insert(0, 'scripts'); import wf_run as R, wf_state as W\n"
                "run = W.Run('S-2', 1)\n"
                "run.event('worker:qe', 'design_gap', {'agent': 'qe', 'reason': 'tasks miss a repo'})\n"
                "R.apply_proposals(run, 'smoke')\n")
        run(["-c", code], cwd, env)
        _, out = run(["scripts/wf_status.py", "S-2"], cwd, env)
        check(re.search(r"^\s*design\.md\s+rejected", out, re.M) and re.search(r"^\s*tasks\.md\s+rejected", out, re.M), "design_gap sends design.md and tasks.md back to the Architect")
        check("no task covers J-2" in out, "a criterion no task covers is reported (the stub tasks only cover J-1)")

        print("4. an incomplete ticket, and a clarification")
        run(["scripts/new_ticket.py", "--ticket", "S-3", "--kind", "story"], cwd, env)
        ctx = cwd / "tickets" / "S-3" / "run-1" / "artifacts" / "ticket-context.md"
        ctx.write_text("# Ticket context\n\n- Affected repos: none provided\n\n| Id | Criterion |\n|---|---|\n| J-1 | none provided |\n", encoding="utf-8")
        code, out = run(["scripts/wf_run.py", "ready", "--ticket", "S-3"], cwd, env)
        check(code != 0 and "no acceptance criteria" in out and "no affected repo" in out, "a ticket without criteria or repos is refused at ready")
        ctx.write_text("# Ticket context\n\n- Affected repos: shop-api\n\n| Id | Criterion |\n|---|---|\n| J-1 | Given a, when b, then c |\n", encoding="utf-8")
        code, out = run(["scripts/wf_run.py", "ready", "--ticket", "S-3"], cwd, env)
        check(code == 0, "the same ticket with a criterion and a repo is accepted")
        with open(cwd / "tickets" / "S-3" / "run-1" / "events.log", "a", encoding="utf-8") as f:
            f.write("2026-01-01T00:00:00+00:00\tplanner\tquestion_raised\t" + json.dumps({"question": "Is J-1 for guests too?"}) + "\n")
        run(["scripts/wf_run.py", "answer", "--ticket", "S-3", "--agent", "planner", "--text", "Yes, guests too."], cwd, env)
        text = ctx.read_text(encoding="utf-8")
        check("## Clarifications" in text and "Is J-1 for guests too?" in text and "Yes, guests too." in text, "the answer is written into ticket-context.md under Clarifications")

        print("5. old names (lanes.yaml, lanes:, lane_* events)")
        d = cwd / "tickets" / "S-0" / "run-1"
        man = d / "manifest.yaml"
        man.write_text(re.sub(r"(?m)^agents:", "lanes:", man.read_text(encoding="utf-8")).replace("ticket: S-2", "ticket: S-0"), encoding="utf-8")
        ev = d / "events.log"
        ev.write_text(re.sub(r"agent_(started|exited|finished|failed)", r"lane_\1", ev.read_text(encoding="utf-8")).replace('"agent":', '"lane":'), encoding="utf-8")
        cfg = cwd / "config" / "agents.yaml"
        (cwd / "config" / "lanes.yaml").write_text(re.sub(r"(?m)^(\s*)agents:", r"\1lanes:", cfg.read_text(encoding="utf-8")), encoding="utf-8")
        cfg.unlink()
        word, agents = status(cwd, env, "S-0")
        check(word == "DONE" and agents and all(v == "done" for v in agents.values()), f"an old-style run and config still read as Done ({word})")
    finally:
        if args.keep:
            print(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print("\nFAILED: " + "; ".join(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
