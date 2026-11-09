#!/usr/bin/env python3
"""Demo workspace for the workflow skeleton: a copy of the project plus one ticket, DEMO-1.

  python demo/demo.py setup  [--workspace PATH]   build a fresh workspace (default demo/workspace, git-ignored)
  python demo/demo.py sync   [--workspace PATH]   copy the project's current config, skills, scripts and agents into it
  python demo/demo.py auto   [--workspace PATH]   fresh workspace, then one trigger: stub agents, a fake human at the gates, then one Auditor pass

To use the real Copilot agents instead, run in the workspace:
  python scripts/wf_run.py start --ticket DEMO-1 --verbose
and approve the gates yourself (the status shows the commands).
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
MARKER = ".demo_workspace"
TICKET = "DEMO-1"
PY = sys.executable
CONTEXT = """\
# DEMO-1: Show the tracking number on the order page

Kind: story. PRD refs: PRD-7#R2.

## Description
Customers ask support where their parcel is. Show the carrier tracking number on the order page.

## Jira criteria
| Id  | Criterion |
|-----|-----------|
| J-1 | The order API returns a tracking number once an order has shipped. |
| J-2 | The order page shows the tracking number for shipped orders. |

## Notes
Repos involved: shop-api, shop-web.
"""


def rmtree(path):
    def fix(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=fix)


def copy_project(ws):
    for name in ("config", "skills", "scripts", ".github"):
        shutil.copytree(PROJECT / name, ws / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "repos.yaml", "repos.local.yaml"))


def script(ws, name, *args):
    r = subprocess.run([PY, str(ws / "scripts" / name), *args], cwd=str(ws), capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"error: {name} {' '.join(args)}\n{r.stdout}{r.stderr}")
    return r.stdout


def setup(ws):
    if ws.exists() and any(ws.iterdir()):
        if not (ws / MARKER).exists():
            sys.exit(f"error: {ws} is not empty and is not a demo workspace. Refusing to delete it. Use --workspace.")
        rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / MARKER).write_text("Created by demo/demo.py. Safe to delete.\n", encoding="utf-8")
    copy_project(ws)
    shutil.copy(PROJECT / "requirements.txt", ws / "requirements.txt")
    (ws / "tickets").mkdir()
    shutil.copytree(PROJECT / "tickets" / "_template", ws / "tickets" / "_template")
    (ws / "config" / "repos.yaml").write_text(
        "repos:\n  shop-api:\n    deploy: independent\n  shop-web:\n    deploy: after shop-api\n", encoding="utf-8", newline="\n")
    script(ws, "new_ticket.py", "--ticket", TICKET, "--kind", "story", "--prd", "PRD-7#R2")
    art = ws / "tickets" / TICKET / "run-1" / "artifacts"
    (art / "ticket-context.md").write_text(CONTEXT, encoding="utf-8", newline="\n")
    script(ws, "wf_run.py", "ready", "--ticket", TICKET, "--by", "demo-human")
    print(f"Demo workspace ready: {ws}\nTicket {TICKET} is ready for the Planner.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "sync", "auto"])
    ap.add_argument("--workspace", default=str(HERE / "workspace"))
    args = ap.parse_args()
    ws = Path(args.workspace).resolve()
    if args.command == "sync":
        if not (ws / MARKER).exists():
            sys.exit(f"error: {ws} is not a demo workspace. Run setup first.")
        copy_project(ws)
        return print(f"Copied the project's config, skills, scripts and agents into {ws}. Tickets and repos.yaml are untouched.")
    setup(ws)
    if args.command == "auto":
        env = {**os.environ, "WF_AGENT_CMD": '["%s", "%s"]' % (PY.replace("\\", "\\\\"), str(HERE / "stub_agent.py").replace("\\", "\\\\")),
               "WF_ACP_CMD": '["%s", "%s"]' % (PY.replace("\\", "\\\\"), str(HERE / "stub_acp.py").replace("\\", "\\\\")), "DEMO_PACE": "1"}
        code = subprocess.call([PY, "scripts/wf_run.py", "start", "--ticket", TICKET, "--auto-approve"], cwd=str(ws), env=env)
        if code == 0:   # the ticket is Done: one pass of the team's Auditor (in real use it keeps watching)
            print("\nAuditor: one pass over the finished tickets")
            code = subprocess.call([PY, "scripts/wf_audit.py", "watch", "--once"], cwd=str(ws), env=env)
        sys.exit(code)
    print(f"Next, from {ws}:\n  python scripts/wf_run.py start --ticket {TICKET} --verbose")


if __name__ == "__main__":
    main()
