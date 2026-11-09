#!/usr/bin/env python3
"""Stand-in for an agent: writes the same placeholder artifacts the real agent files ask for. No AI.

The orchestrator starts it through WF_AGENT_CMD, exactly like Copilot. Agent, ticket and repo come from the
environment that scripts/wf_launch.py sets (WF_AGENT, WF_TICKET, WF_RUN, WF_REPO). Run from the workflow root.
"""
import os
import re
import sys
import time
from pathlib import Path

AGENT = os.environ.get("WF_AGENT") or os.environ["WF_LANE"]
TASK_DIR = os.environ.get("WF_TASK_DIR")
TICKET, RUN, REPO = os.environ.get("WF_TICKET", ""), os.environ.get("WF_RUN", ""), os.environ.get("WF_REPO", "")
RUN_DIR = Path.cwd() / "tickets" / TICKET / f"run-{RUN}"
ART = RUN_DIR / "artifacts"
REPOS = ["shop-api", "shop-web"]


def write(path, inputs, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (f"---\nartifact: {path.name}\nticket: {TICKET}\nrun: {RUN}\nauthor: {AGENT}{'-' + REPO if REPO else ''}\n"
            f"inputs: [{', '.join(inputs)}]\nstatus: draft\n---\n")
    path.write_text(head + body, encoding="utf-8", newline="\n")
    print(f"  wrote {path.relative_to(RUN_DIR).as_posix()}", flush=True)


def planner():
    write(ART / "design.md", ["ticket-context.md"], "# Design\n\nPlaceholder: one change per repo.\n")
    rows = "\n".join(f"| T-{i} placeholder | {r} | J-1 | none |" for i, r in enumerate(REPOS, 1))
    write(ART / "tasks.md", ["ticket-context.md", "design.md"],
          f"# Tasks\n\n## Tasks\n| Task | Repo | Covers | Depends on |\n|---|---|---|---|\n{rows}\n\n"
          f"## Release\ndeploy_order: ordered\nsequence: {', '.join(REPOS)}\nreason: placeholder order\n")


def qe():
    write(ART / "test-plan.md", ["ticket-context.md", "tasks.md"],
          "# Test plan\n\n## Coverage matrix\n| Id | Level | Repo | Test |\n|---|---|---|---|\n| J-1 | unit | " + REPOS[0] + " | placeholder |\n")
    for r in REPOS:
        write(ART / "tests" / r / "README.md", ["ticket-context.md", "tasks.md"], "Placeholder: the tests for this repo go here.\n")


def dev():
    ins = ["ticket-context.md", "design.md", "tasks.md"]
    write(ART / "changes" / REPO / "summary.md", ins, f"# {REPO}\n\nPlaceholder: what would change in {REPO}.\n")
    write(ART / "changes" / REPO / "notes.md", ins, "# Notes for the Reviewer\n\nPlaceholder.\n")


def reviewer():
    write(ART / "verify-report.md", ["test-plan.md", "changes"],
          "# Verify report\n\nResult: **PASS**\n\n| Id | Repo | Test | Result |\n|---|---|---|---|\n| J-1 | " + REPOS[0] + " | placeholder | pass |\n")
    write(ART / "review-report.md", ["test-plan.md", "changes"],
          "human_required: false\nsignals: []\n\nVerdict: pass\n\n## Review\nPlaceholder.\n")


def auditor():
    out = Path(os.environ["WF_AUDIT_FILE"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"---\nartifact: audit\nticket: {TICKET}\nrun: {RUN}\nauthor: auditor\n---\n# Audit of {TICKET} run {RUN}\n\n"
                   "- What went well: placeholder\n- What got in the way: placeholder\n- Suggested change: placeholder\n",
                   encoding="utf-8", newline="\n")
    print(f"  wrote {out}", flush=True)


def generic():
    """Any agent the config defines: write a placeholder for each artifact it produces (per repo when it fans out)."""
    sys.path.insert(0, str(Path.cwd() / "scripts"))
    import wf_config as C
    kind = "story"
    m = RUN_DIR / "manifest.yaml"
    if m.exists():
        kind = next((l.split(":", 1)[1].split("#")[0].strip() for l in m.read_text(encoding="utf-8").splitlines() if l.startswith("kind:")), "story")
    cfg = C.load()
    l = cfg.agent(kind, AGENT)
    ins = [k for k, _ in l["requires"]]
    for key in l["produces"]:
        if key in l["per_repo"]:
            write(ART / key / REPO / "summary.md", ins, f"# {AGENT} for {REPO}\n\nPlaceholder.\n")
        elif "." in key:
            write(ART / key, ins, f"# {key}\n\nPlaceholder written by the {AGENT} agent.\n")
        else:
            write(ART / key / "README.md", ins, "Placeholder.\n")


def run_agent():
    known = {"planner": planner, "qe": qe, "dev": dev, "reviewer": reviewer, "auditor": auditor}
    if AGENT in known and not (AGENT == "planner" and _spike()):
        known[AGENT]()
    else:
        generic()


def main():
    print(f"  [{AGENT}{'-' + REPO if REPO else ''}] stub agent (placeholder, no AI)", flush=True)
    time.sleep(float(os.environ.get("DEMO_PACE", "1")))
    run_agent()

def _spike():
    m = RUN_DIR / "manifest.yaml"
    return m.exists() and "kind: spike" in m.read_text(encoding="utf-8")


if __name__ == "__main__":
    main()
