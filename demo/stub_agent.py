#!/usr/bin/env python3
"""Stand-in for a lane agent: writes the same placeholder artifacts the real agent files ask for. No AI.

The orchestrator starts it through WF_AGENT_CMD, exactly like Copilot. Lane, ticket and repo come from the
environment that scripts/wf_launch.py sets (WF_LANE, WF_TICKET, WF_RUN, WF_REPO). Run from the workflow root.
"""
import os
import re
import sys
import time
from pathlib import Path

LANE, TICKET, RUN = os.environ["WF_LANE"], os.environ["WF_TICKET"], os.environ["WF_RUN"]
REPO = os.environ.get("WF_REPO", "")
RUN_DIR = Path.cwd() / "tickets" / TICKET / f"run-{RUN}"
ART = RUN_DIR / "artifacts"
REPOS = ["shop-api", "shop-web"]


def write(path, inputs, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (f"---\nartifact: {path.name}\nticket: {TICKET}\nrun: {RUN}\nauthor: {LANE}{'-' + REPO if REPO else ''}\n"
            f"inputs: [{', '.join(inputs)}]\nstatus: draft\n---\n")
    path.write_text(head + body, encoding="utf-8", newline="\n")
    print(f"  wrote {path.relative_to(RUN_DIR).as_posix()}", flush=True)


def planner():
    write(ART / "spec.md", ["ticket-context.md"],
          "# Spec\n\n## Behavior\nPlaceholder.\n\n## Acceptance criteria\n| Id | Criterion | PRD requirement | Source |\n|----|-----------|-----------------|--------|\n"
          "| AC-1 | Given ..., when ..., then ... | PRD-0#R0 | J-1 |\n\n## Traceability\n| Source id | Spec AC | Status | Reason |\n|---|---|---|---|\n| J-1 | AC-1 | kept | |\n")
    write(ART / "design.md", ["ticket-context.md", "spec.md"], "# Design\n\nPlaceholder: one change per repo.\n")
    rows = "\n".join(f"| T-{i} placeholder | {r} | AC-1 | none |" for i, r in enumerate(REPOS, 1))
    write(ART / "tasks.md", ["ticket-context.md", "spec.md", "design.md"],
          f"# Tasks\n\n## Tasks\n| Task | Repo | Covers | Depends on |\n|---|---|---|---|\n{rows}\n\n"
          f"## Release\ndeploy_order: ordered\nsequence: {', '.join(REPOS)}\nreason: placeholder order\n")


def qe():
    write(ART / "test-plan.md", ["spec.md", "tasks.md"],
          "# Test plan\n\n## Coverage matrix\n| AC | Level | Repo | Test |\n|---|---|---|---|\n| AC-1 | unit | " + REPOS[0] + " | placeholder |\n")
    for r in REPOS:
        write(ART / "tests" / r / "README.md", ["spec.md", "tasks.md"], "Placeholder: the tests for this repo go here.\n")


def dev():
    ins = ["spec.md", "design.md", "tasks.md"]
    write(ART / "changes" / REPO / "summary.md", ins, f"# {REPO}\n\nPlaceholder: what would change in {REPO}.\n")
    write(ART / "changes" / REPO / "notes.md", ins, "# Notes for the Reviewer\n\nPlaceholder.\n")


def reviewer():
    write(ART / "verify-report.md", ["test-plan.md", "changes"],
          "# Verify report\n\nResult: **PASS**\n\n| AC | Repo | Test | Result |\n|---|---|---|---|\n| AC-1 | " + REPOS[0] + " | placeholder | pass |\n")
    write(ART / "review-report.md", ["test-plan.md", "changes"],
          "human_required: false\nsignals: []\n\nVerdict: pass\n\n## Review\nPlaceholder.\n")


def inspect_adapt():
    write(ART / "inspection-report.md", ["review-report.md"],
          "# Inspection report\n\n- What went well: placeholder\n- What got in the way: placeholder\n- Suggested change: placeholder\n")


def main():
    print(f"  [{LANE}{'-' + REPO if REPO else ''}] stub agent (placeholder, no AI)", flush=True)
    time.sleep(float(os.environ.get("DEMO_PACE", "1")))
    {"planner": planner, "qe": qe, "dev": dev, "reviewer": reviewer, "inspect-adapt": inspect_adapt}[LANE]()


if __name__ == "__main__":
    main()
