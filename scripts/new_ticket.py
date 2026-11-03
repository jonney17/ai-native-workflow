#!/usr/bin/env python3
"""Create a work item run folder from the template.

Kinds: story (default), chore (tech debt, refactor, upgrade), spike (investigation).

Usage:
  python scripts/new_ticket.py --ticket PROJ-123 [--run 1] [--prd PRD-12#R4 --prd PRD-12#R5]
  python scripts/new_ticket.py --ticket CHORE-7 --kind chore --origin tech-debt
  python scripts/new_ticket.py --ticket SPIKE-2 --kind spike --origin experiment
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = ("story", "chore", "spike")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def header(ticket, run):
    return (
        "---\n"
        "artifact: ticket-context.md\n"
        f"ticket: {ticket}\n"
        f"run: {run}\n"
        "author: system\n"
        "status: draft\n"
        "---\n"
        "# Ticket context\n\n"
    )


def context_story(ticket, run, prd, origin):
    return header(ticket, run) + (
        f"- Jira: <link to {ticket}>\n"
        f"- PRD requirements: {', '.join(prd) or '<ids>'}\n"
        "- Affected repos (first guess): <repos>\n\n"
        "## Ticket description\n\n<paste the ticket text>\n\n"
        "## Jira acceptance criteria and scenarios\n\n"
        "One row per criterion or scenario. Keep the ids stable: they are carried into the spec and the test plan.\n"
        "If the ticket has none, write 'none provided'.\n\n"
        "| Id | Criterion or scenario (given / when / then) |\n"
        "|---|---|\n"
        "| J-1 | <text> |\n\n"
        "## Relevant PRD sections\n\n<paste or link the sections>\n"
    )


def context_chore(ticket, run, prd, origin):
    return header(ticket, run) + (
        "- Kind: chore\n"
        f"- Origin: {origin or '<tech-debt | ops | experiment | other>'}\n"
        f"- Source: <Jira link for {ticket}, or 'none'>\n"
        "- Affected repos (first guess): <repos>\n\n"
        "## Intent\n\n<why this work is worth doing, in a few sentences>\n\n"
        "## Done criteria\n\n"
        "One row per criterion. Keep the ids stable: they are carried into the spec and the test plan.\n\n"
        "| Id | Done criterion (observable and testable) |\n"
        "|---|---|\n"
        "| C-1 | <text> |\n\n"
        "## Behavior-preserving?\n\n<yes or no. If yes, say what must not change.>\n\n"
        "## Out of scope\n\n<what this chore must not touch>\n"
    )


def context_spike(ticket, run, prd, origin):
    return header(ticket, run) + (
        "- Kind: spike\n"
        f"- Origin: {origin or '<tech-debt | ops | experiment | other>'}\n"
        f"- Source: <Jira link for {ticket}, or 'none'>\n\n"
        "## Question\n\n<the question to answer>\n\n"
        "## Decision it informs\n\n<what will be decided with the answer, and by whom>\n\n"
        "## Time box\n\n<for example 4 hours of agent work. Advisory until budgets are enforced.>\n\n"
        "## Starting points\n\n<repos, documents or links to look at first>\n"
    )


CONTEXT = {"story": context_story, "chore": context_chore, "spike": context_spike}

# A spike has no spec, design, tasks, code or tests: it replaces the lanes, artifacts and acceptance blocks.
SPIKE_BLOCKS = """\
lanes:
  planner:       { status: waiting }
  inspect-adapt: { status: waiting, enabled: false }   # set enabled: true to run it for this spike
# lane status: waiting | ready | working | blocked | done | stale | failed

artifacts:
  ticket-context.md:    { owner: system,        status: missing }
  findings.md:          { owner: planner,       status: missing }
  inspection-report.md: { owner: inspect-adapt, status: missing }
# artifact status: missing | draft | ready | approved | rejected | stale

acceptance:
  policy: findings_review     # a human reads findings.md and accepts it, rejects it, or asks for more
  accepted: null
  signed_off_by: null
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--kind", choices=KINDS, default="story")
    ap.add_argument("--origin", default="", help="chore and spike: tech-debt | ops | experiment | other")
    ap.add_argument("--prd", action="append", default=[], help="PRD requirement id, repeatable (stories)")
    args = ap.parse_args()

    src = ROOT / "tickets" / "_template" / "run-1"
    dst = ROOT / "tickets" / args.ticket / f"run-{args.run}"
    if dst.exists():
        sys.exit(f"error: {dst} already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)

    manifest = dst / "manifest.yaml"
    out = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if args.kind == "spike" and line.startswith("lanes:"):
            out.extend(SPIKE_BLOCKS.splitlines())
            break  # the spike blocks replace everything from here to the end of the file
        if line.startswith("ticket: "):
            line = f"ticket: {args.ticket}"
        elif line.startswith("run: "):
            line = f"run: {args.run}"
        elif line.startswith("kind:"):
            line = f"kind: {args.kind}                    # story | chore | spike"
        elif line.startswith("origin:"):
            line = f'origin: "{args.origin}"'
        elif line.startswith("prd_refs:"):
            refs = ", ".join(args.prd)
            line = f"prd_refs: [{refs}]" + line[line.index("#") - 1:] if "#" in line else f"prd_refs: [{refs}]"
        out.append(line)
    manifest.write_text("\n".join(out) + "\n", encoding="utf-8")

    (dst / "artifacts" / "ticket-context.md").write_text(
        CONTEXT[args.kind](args.ticket, args.run, args.prd, args.origin), encoding="utf-8")

    with open(dst / "events.log", "a", encoding="utf-8") as f:
        detail = {"ticket": args.ticket, "run": args.run, "kind": args.kind, "origin": args.origin, "prd_refs": args.prd}
        f.write(f"{now()}\tsystem\tticket_created\t{json.dumps(detail)}\n")

    print(f"created {dst}")
    print("next: fill artifacts/ticket-context.md, mark it ready, then launch the planner lane")


if __name__ == "__main__":
    main()
