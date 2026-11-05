#!/usr/bin/env python3
"""Demo driver for the AI-native workflow skeleton. Fake repos, scripted agents, real scripts.

It plays two roles that the real system will have: the ORCHESTRATOR (applies transitions
to the manifest, deploys) and the HUMAN (approves the plan and the review, accepts the ticket).
The lanes run through the real scripts/wf_launch.py with scripted stub agents (no AI).

Usage (from the project root):
  python demo/demo.py setup            build a fresh demo workspace (also what reset does)
  python demo/demo.py list             show the steps
  python demo/demo.py next             run the next step, then show the ticket status
  python demo/demo.py run              run all remaining steps, pausing between them
  python demo/demo.py status           show the ticket status now
  python demo/demo.py sync             copy the project's current config, skills, scripts and agents into an existing workspace
  python demo/demo.py auto             fresh workspace, then ONE trigger: lane workers run the whole story on their own
  python demo/demo.py stale on|off     edit the approved spec to show the stale cascade, then undo
  python demo/demo.py reset            wipe the workspace and start over

Options: --workspace PATH (default demo/workspace), --fast (no pauses or delays), --no-pause.
The workspace is separate from your real tickets. Nothing here touches tickets/ or ledger/ of the project.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
try:
    import yaml
except ImportError:
    sys.exit("error: the demo needs PyYAML. Install it with: python -m pip install -r requirements.txt")

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE / "content"))
import scripted as C  # noqa: E402

MARKER = ".demo_workspace"
STATE = ".demo_state.json"
T = C.TICKET
PY = sys.executable
OK = ("ready", "approved")
REPOS = ["shop-api", "shop-web"]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


# ------------------------------------------------------------------ small helpers

def rmtree(path):
    def onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=onerror)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short_hash(p):
    import hashlib
    h = hashlib.sha256()
    if p.is_dir():
        for f in sorted(x for x in p.rglob("*") if x.is_file()):
            h.update(f.relative_to(p).as_posix().encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    else:
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:8]


class Demo:
    def __init__(self, ws, fast):
        self.ws = ws
        self.fast = fast
        self.run_dir = ws / "tickets" / T / "run-1"
        self.art = self.run_dir / "artifacts"

    # ---- state
    def state(self):
        p = self.ws / STATE
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"step": 0}

    def save_state(self, **kw):
        s = self.state()
        s.update(kw)
        (self.ws / STATE).write_text(json.dumps(s, indent=2), encoding="utf-8")

    def require_ws(self):
        if not (self.ws / MARKER).exists():
            sys.exit(f"error: no demo workspace at {self.ws}. Run: python demo/demo.py setup")

    # ---- manifest and logs (orchestrator's job)
    def load(self):
        return yaml.safe_load((self.run_dir / "manifest.yaml").read_text(encoding="utf-8"))

    def save(self, m):
        text = yaml.safe_dump(m, sort_keys=False, default_flow_style=None, width=120)
        head = "# Written by the demo orchestrator. Agents never edit this file.\n"
        (self.run_dir / "manifest.yaml").write_text(head + text, encoding="utf-8", newline="\n")

    def event(self, actor, name, detail):
        with open(self.run_dir / "events.log", "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{now()}\t{actor}\t{name}\t{json.dumps(detail, sort_keys=True)}\n")

    def decide(self, text):
        with open(self.run_dir / "decisions.md", "a", encoding="utf-8", newline="\n") as f:
            f.write(f"\n- {datetime.now().date().isoformat()} - {text}\n")

    def mark(self, name, status, inputs=(), approved_by=None, repo=None, version=None):
        m = self.load()
        if repo:
            ch = m["artifacts"]["changes"]
            e = ch.setdefault("per_repo", {}).get(repo) or {}
            path = self.art / "changes" / repo
        else:
            e = m["artifacts"][name]
            path = self.art / name
        e["status"] = status
        if version:
            e["version"] = version
        if path.exists():
            e["hash"] = short_hash(path)
        if inputs:
            e["inputs"] = {i: short_hash(self.art / i) for i in inputs}
        if approved_by:
            e["approved_by"] = approved_by
        if repo:
            m["artifacts"]["changes"]["per_repo"][repo] = e
            vals = [x["status"] for x in m["artifacts"]["changes"]["per_repo"].values()]
            m["artifacts"]["changes"]["status"] = "ready" if all(v in OK for v in vals) else "draft"
        else:
            m["artifacts"][name] = e
        self.save(m)

    # ---- running things
    def script(self, name, *args, capture=False):
        cmd = [PY, str(self.ws / "scripts" / name), *args]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        if capture:
            return subprocess.run(cmd, cwd=self.ws, env=env, capture_output=True, text=True)
        return subprocess.run(cmd, cwd=self.ws, env=env)

    def launch_cmd(self, lane, repo=None, attempt=1):
        cmd = [PY, str(self.ws / "scripts" / "wf_launch.py"), "--ticket", T, "--run", "1", "--lane", lane,
               "--attempt", str(attempt)]
        if repo:
            cmd += ["--repo", repo]
        return cmd + ["--", PY, str(HERE / "stub_agent.py")]

    def launch_all(self, jobs):
        """jobs: list of (lane, repo, attempt). Runs them at the same time, like independent agents."""
        env = {**os.environ, "DEMO_WORKSPACE": str(self.ws), "DEMO_PACE": "0" if self.fast else "0.5",
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        lock = threading.Lock()
        fails = []

        def work(job):
            lane, repo, attempt = job
            p = subprocess.Popen(self.launch_cmd(lane, repo, attempt), cwd=self.ws, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
            for line in p.stdout:
                with lock:
                    print(line.rstrip(), flush=True)
            if p.wait() != 0:
                fails.append(job)

        label = ", ".join(f"{l}{'/' + r if r else ''} (attempt {a})" for l, r, a in jobs)
        print(f"\n$ launching in parallel: {label}" if len(jobs) > 1 else f"\n$ launching: {label}")
        threads = [threading.Thread(target=work, args=(j,)) for j in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if fails:
            sys.exit(f"error: lane session failed: {fails}")
        for lane, repo, attempt in jobs:
            args = ["--ticket", T, "--run", "1", "--lane", lane, "--attempt", str(attempt)]
            if repo:
                args += ["--repo", repo]
            r = self.script("otel_check.py", *args, capture=True)
            first = r.stdout.splitlines()[0] if r.stdout else "no output"
            tok = next((l.strip() for l in r.stdout.splitlines() if "tokens in/out" in l), "")
            print(f"  telemetry check {lane}{'/' + repo if repo else ''}: {first}  {tok}  (synthetic numbers)")

    def status(self):
        print()
        self.script("wf_status.py", T)

    # ---- repos
    def repo_path(self, name):
        return self.ws / "repos" / name

    def git(self, repo, *args):
        return subprocess.run(["git", "-c", "user.name=demo-ci", "-c", "user.email=demo@example.com", *args],
                              cwd=self.repo_path(repo), capture_output=True, text=True, check=True).stdout.strip()


# ------------------------------------------------------------------ setup

def setup(d):
    ws = d.ws
    if ws.exists() and any(ws.iterdir()):
        if not (ws / MARKER).exists():
            sys.exit(f"error: {ws} exists, is not empty and is not a demo workspace. Refusing to delete it.\n"
                     f"       Pick another folder with --workspace, or remove it yourself.")
        rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / MARKER).write_text("Created by demo/demo.py. Safe to delete.\n", encoding="utf-8")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".gitkeep")
    for name in ("config", "skills", "scripts", ".github"):
        shutil.copytree(PROJECT / name, ws / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "repos.local.yaml"))
    (ws / "tickets").mkdir()
    shutil.copytree(PROJECT / "tickets" / "_template", ws / "tickets" / "_template")
    (ws / "ledger").mkdir()
    shutil.copy(PROJECT / "ledger" / "_template.yaml", ws / "ledger" / "_template.yaml")
    for lane in ("planner", "qe", "dev", "reviewer", "inspect-adapt"):
        (ws / "overlay" / lane).mkdir(parents=True)
    (ws / "otel-dump").mkdir()
    shutil.copy(PROJECT / "requirements.txt", ws / "requirements.txt")
    # fake repos
    reg, loc = ["repos:"], ["repos:"]
    for name in REPOS:
        rp = ws / "repos" / name
        shutil.copytree(HERE / "seed" / name, rp, ignore=shutil.ignore_patterns("__pycache__"))
        (rp / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        run = lambda *a: subprocess.run(["git", "-c", "user.name=demo-seed", "-c", "user.email=demo@example.com", *a],
                                        cwd=rp, capture_output=True, text=True, check=True)
        run("init", "-b", "main")
        run("add", "-A")
        run("commit", "-m", "Initial commit (demo seed)")
        reg += [f"  {name}:", f"    remote: demo-only/{name}", "    default_branch: main",
                f"    deploy: {'independent' if name == 'shop-api' else 'after shop-api'}"]
        loc += [f"  {name}:", f"    local_path: {rp}"]
    (ws / "config" / "repos.yaml").write_text("\n".join(reg) + "\n", encoding="utf-8", newline="\n")
    (ws / "config" / "repos.local.yaml").write_text("\n".join(loc) + "\n", encoding="utf-8", newline="\n")
    d.save_state(step=0)
    print(f"Demo workspace ready: {ws}")
    print("It has its own copy of config, skills and scripts, two fake repos (shop-api, shop-web) and no tickets.")
    print("Next: python demo/demo.py list   or   python demo/demo.py next")


# ------------------------------------------------------------------ steps

def tasks_release(d):
    text = (d.art / "tasks.md").read_text(encoding="utf-8")
    order = re.search(r"deploy_order:\s*(\w+)", text)
    seq = re.search(r"sequence:\s*([\w\-, ]+)", text)
    sequence = [s.strip() for s in seq.group(1).split(",") if s.strip()] if seq else []
    repos = [r for r in REPOS if re.search(r"(?<![\w-])" + re.escape(r) + r"(?![\w-])", text)]
    return (order.group(1) if order else "independent"), sequence, repos


def step_intake(d):
    d.script("new_ticket.py", "--ticket", T, "--kind", "story", "--prd", "PRD-7#R2")
    (d.art / "ticket-context.md").write_text(C.TICKET_CONTEXT, encoding="utf-8", newline="\n")
    d.mark("ticket-context.md", "ready")
    d.event("orchestrator", "artifact_ready", {"artifact": "ticket-context.md"})
    print("Ticket context filled in (Jira criteria J-1..J-3) and marked ready.")


def step_planner(d):
    d.launch_all([("planner", None, 1)])
    d.mark("spec.md", "ready", inputs=["ticket-context.md"])
    d.mark("design.md", "ready", inputs=["ticket-context.md", "spec.md"])
    d.mark("tasks.md", "ready", inputs=["ticket-context.md", "spec.md", "design.md"])
    d.event("orchestrator", "artifacts_ready", {"artifacts": ["spec.md", "design.md", "tasks.md"]})


def step_approve_plan(d):
    for n in ("spec.md", "design.md", "tasks.md"):
        d.mark(n, "approved", approved_by="demo-human")
    order, seq, repos = tasks_release(d)
    m = d.load()
    m["release"].update(deploy_order=order, sequence=seq)
    m["lanes"]["dev"]["repos"] = {r: "waiting" for r in repos}
    d.save(m)
    d.event("demo-human", "gate_approved", {"artifacts": ["spec.md", "design.md", "tasks.md"]})
    d.event("orchestrator", "manifest_updated", {"repos": repos, "deploy_order": order, "sequence": seq})
    d.decide("demo-human approved spec, design and tasks together.")
    print(f"Human approved the plan. The orchestrator copied repos {repos} and Release ({order}: {', '.join(seq)}) from tasks.md into the manifest.")


def step_build(d):
    d.launch_all([("qe", None, 1), ("dev", "shop-api", 1), ("dev", "shop-web", 1)])
    d.mark("test-plan.md", "ready", inputs=["spec.md", "tasks.md"])
    for r in REPOS:
        d.mark("changes", "ready", inputs=["spec.md", "design.md", "tasks.md"], repo=r, version=1)
    d.event("orchestrator", "artifacts_ready", {"artifacts": ["test-plan.md", "changes/shop-api", "changes/shop-web"]})


def step_verify_fail(d):
    d.launch_all([("reviewer", None, 1)])
    d.mark("verify-report.md", "ready", inputs=["test-plan.md", "changes"])
    events = (d.run_dir / "events.log").read_text(encoding="utf-8").splitlines()
    bad = [json.loads(l.split("\t", 3)[3]) for l in events if "\tverify_failed\t" in l]
    for p in bad:
        d.mark("changes", "rejected", repo=p["repo"])
        d.event("orchestrator", "rework_requested", {"repo": p["repo"], "acs": p["acs"], "lane": "dev"})
        d.decide(f"verify_failed accepted: changes/{p['repo']} rejected ({', '.join(p['acs'])}). Only that repo is redone.")
    print(f"\nThe orchestrator applied the proposal: only {', '.join(p['repo'] for p in bad)} goes back to Dev. shop-api stays done.")
    print("(wf_status keeps listing the proposal below: events.log is append-only, and nothing applies proposals in the real system yet.)")


def step_rework(d):
    d.launch_all([("dev", "shop-web", 2)])
    d.mark("changes", "ready", inputs=["spec.md", "design.md", "tasks.md"], repo="shop-web", version=2)
    d.event("orchestrator", "artifacts_ready", {"artifacts": ["changes/shop-web"], "attempt": 2})
    print("\nNote: verify-report.md is now STALE, because it was built on the old changes. That is what sends the Reviewer back.")


def step_review(d):
    d.launch_all([("reviewer", None, 2)])
    d.mark("verify-report.md", "ready", inputs=["test-plan.md", "changes"])
    d.mark("review-report.md", "ready", inputs=["test-plan.md", "changes"])
    first = (d.art / "review-report.md").read_text(encoding="utf-8").split("---", 2)[2].strip().splitlines()[0]
    value = first.split(":", 1)[1].strip() == "true"
    m = d.load()
    m["review"]["human_required"] = value
    d.save(m)
    d.event("orchestrator", "risk_decision_applied", {"human_required": value})
    print(f"\nThe orchestrator copied the Reviewer's risk decision to the manifest: review.human_required = {str(value).lower()}")


def step_approve_review(d):
    d.mark("review-report.md", "approved", approved_by="demo-human")
    d.event("demo-human", "gate_approved", {"artifacts": ["review-report.md"]})
    d.decide("demo-human approved the review (risk-flagged: multi_repo_ordered_deploy, touches_api_contract).")
    print("Human read the review and approved it.")


def deploy(d, repo):
    m = d.load()
    seq = m["release"].get("sequence") or []
    if m["release"].get("deploy_order") == "ordered":
        for earlier in seq[:seq.index(repo)]:
            if (m["release"].get("repos") or {}).get(earlier) != "deployed":
                sys.exit(f"error: release order says {earlier} must be deployed before {repo}")
    branch = f"wf/{T}-run1"
    d.event("ci_cd", "deploy_started", {"repo": repo})
    d.git(repo, "checkout", "main")
    d.git(repo, "merge", "--no-ff", branch, "-m", f"Merge {branch} ({T})")
    res = subprocess.run([PY, "-m", "unittest", "discover", "-s", "tests"], cwd=d.repo_path(repo),
                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
    if res.returncode != 0:
        m["release"].setdefault("repos", {})[repo] = "failed"
        d.save(m)
        d.event("ci_cd", "deploy_failed", {"repo": repo})
        sys.exit(f"deploy of {repo} failed:\n{res.stderr}")
    m["release"].setdefault("repos", {})[repo] = "deployed"
    d.save(m)
    d.event("ci_cd", "deployed", {"repo": repo})
    print(f"CI/CD stand-in: merged {branch} into main of {repo} (a real git merge), tests on main pass, marked deployed.")
    print("Deploy is not a lane. It is plain CI/CD that follows the Release order from tasks.md.")


def step_deploy_api(d):
    deploy(d, "shop-api")


def step_deploy_web(d):
    deploy(d, "shop-web")


def step_accept(d):
    body = ("# Acceptance report\n\nPolicy: **demo default** (the real policy is still TBD).\n\n"
            "- All 5 ACs map to a passing test (see verify-report.md).\n- Both repos deployed in the Release order.\n"
            "- Review approved by a human (risk-flagged).\n- No blocking comments.\n\nTicket accepted by evidence, signed off by a human because the review was risk-flagged.\n")
    (d.art / "acceptance-report.md").write_text(body, encoding="utf-8", newline="\n")
    d.mark("acceptance-report.md", "ready")
    m = d.load()
    m["acceptance"].update(policy="demo-default-evidence", ticket_accepted=True, signed_off_by="demo-human")
    d.save(m)
    d.event("demo-human", "ticket_accepted", {"policy": "demo-default-evidence"})
    d.decide("demo-human accepted the ticket (evidence + sign-off). Acceptance policy is still TBD; the demo uses the suggested default.")
    print("Ticket accepted. This is the trigger for Inspect & Adapt.")


def step_inspect(d):
    d.launch_all([("inspect-adapt", None, 1)])
    d.mark("inspection-report.md", "ready", inputs=["review-report.md"])
    d.event("orchestrator", "artifacts_ready", {"artifacts": ["inspection-report.md", f"ledger/{C.LEDGER['id']}.yaml"]})
    print(f"\nNew in the workspace: ledger/{C.LEDGER['id']}.yaml and overlay/dev/{C.LEDGER['id']}.md")
    print("The retro will review the ledger and decide whether to promote it to the team skills.")


def step_next_ticket(d):
    d.script("new_ticket.py", "--ticket", C.NEXT_TICKET, "--kind", "story")
    print("\nWhat the next Dev session would be launched with (nothing is launched, this only prints the environment):\n")
    d.script("wf_launch.py", "--ticket", C.NEXT_TICKET, "--run", "1", "--lane", "dev", "--repo", "shop-web", "--print-env")
    print("\nLook for workflow.overlay=" + C.LEDGER["id"] + " in OTEL_RESOURCE_ATTRIBUTES. Telemetry on DEMO-2 can now be compared with DEMO-1.")


STEPS = [
    ("Intake: a ticket arrives", "A story with Jira criteria J-1..J-3 becomes a run folder with a manifest. Status is derived, never typed.", step_intake, T),
    ("Planner writes spec, design and tasks", "The Planner lane starts because ticket-context.md exists. It finds the repos and writes the Release order.", step_planner, T),
    ("Human approves the plan", "First human gate: spec, design and tasks are approved together. Nothing else needs a human until the review.", step_approve_plan, T),
    ("QE and two Dev lanes work in parallel", "Three agents, one shared plan, no hand-offs. Each Dev works in its own repo on its own branch.", step_build, T),
    ("Reviewer verifies: shop-web fails", "The Reviewer is independent of Dev and QE. It proposes verify_failed; the orchestrator applies it for shop-web only.", step_verify_fail, T),
    ("Dev reworks shop-web only", "shop-api is untouched. The old verify-report is now stale, which re-queues the Reviewer.", step_rework, T),
    ("Reviewer passes and flags risk", "All five ACs pass. Two risk signals match, so a human must approve the review.", step_review, T),
    ("Human approves the review", "Second human gate, only because the review is risk-flagged.", step_approve_review, T),
    ("Deploy shop-api (first in the Release order)", "Merge and deploy are CI/CD, not a lane. Order comes from tasks.md.", step_deploy_api, T),
    ("Deploy shop-web", "Second in order. Trying it first would be refused.", step_deploy_web, T),
    ("Acceptance: ticket is done", "Acceptance policy is TBD, so the demo uses the suggested default and says so.", step_accept, T),
    ("Inspect & Adapt learns from the run", "It reads the trail and telemetry, writes a ledger entry and a removable overlay candidate.", step_inspect, T),
    ("Next ticket picks up the overlay", "The launcher tags the next Dev session with the active candidate, so its effect can be measured.", step_next_ticket, C.NEXT_TICKET),
]


def list_steps(d):
    cur = d.state()["step"] if (d.ws / MARKER).exists() else 0
    for i, (title, why, _f, _t) in enumerate(STEPS, 1):
        mark = "done" if i <= cur else "next" if i == cur + 1 else "    "
        print(f"  {i:2}. [{mark}] {title}")


def do_step(d, n, pause=False):
    title, why, fn, ticket = STEPS[n - 1]
    print("\n" + "=" * 78)
    print(f"Step {n}/{len(STEPS)}: {title}")
    print(f"Shows: {why}")
    print("=" * 78)
    fn(d)
    d.save_state(step=n)
    print()
    if ticket == T:
        d.status()
    else:
        d.script("wf_status.py", "--all")


def auto(d, args):
    """One trigger: lane workers (scripts/wf_run.py) run the whole story. The stub agents stand in for Copilot."""
    setup(d)
    print("\nIntake (a person would do this):")
    step_intake(d)
    d.script("wf_run.py", "ready", "--ticket", T, "--by", "demo-human")
    env = {**os.environ, "DEMO_WORKSPACE": str(d.ws), "DEMO_PACE": "0" if args.fast else "0.3",
           "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
           "WF_AGENT_CMD": json.dumps([PY, str(HERE / "stub_agent.py")]),
           "WF_DEPLOY_CMD": json.dumps([PY, str(HERE / "ci_deploy.py"), "--workspace", str(d.ws), "--ticket", T, "--repo", "{repo}"])}
    cmd = [PY, str(d.ws / "scripts" / "wf_run.py"), "start", "--ticket", T, "--verbose"]
    if not args.gates_by_hand:
        cmd.append("--auto-approve")
    else:
        print("\nGates are yours. In another terminal, from the workspace folder:\n"
              f"  cd {d.ws}\n  python scripts/wf_run.py approve --ticket {T} --gate planner\n"
              f"  python scripts/wf_run.py approve --ticket {T} --gate reviewer\n"
              f"  python scripts/wf_run.py accept --ticket {T}\n")
    print("$ " + " ".join(cmd[1:]) + "\n")
    code = subprocess.call(cmd, cwd=d.ws, env=env)
    d.status()
    return code


def stale(d, mode):
    p = d.art / "spec.md"
    backup = d.ws / ".spec_backup"
    if mode == "on":
        if backup.exists():
            sys.exit("error: already on. Run: python demo/demo.py stale off")
        if not p.exists():
            sys.exit("error: there is no spec.md yet (run step 2 first)")
        backup.write_bytes(p.read_bytes())
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write("| AC-6 | Show the tracking number in the order e-mail. |\n")
        d.event("demo-human", "artifact_edited", {"artifact": "spec.md", "note": "edited after approval (demo)"})
        print("Edited the approved spec.md (added AC-6). Look at what becomes stale:")
    else:
        if not backup.exists():
            sys.exit("error: nothing to undo")
        p.write_bytes(backup.read_bytes())
        backup.unlink()
        d.event("demo-human", "artifact_edited", {"artifact": "spec.md", "note": "edit reverted (demo)"})
        print("Reverted spec.md. Everything built on it is current again.")
    d.status()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "reset", "list", "next", "run", "status", "stale", "auto", "sync"])
    ap.add_argument("arg", nargs="?", help="stale: on | off")
    ap.add_argument("--workspace", default=str(HERE / "workspace"))
    ap.add_argument("--fast", action="store_true", help="no delays and no pauses")
    ap.add_argument("--no-pause", action="store_true", help="run: do not wait for Enter between steps")
    ap.add_argument("--gates-by-hand", action="store_true", help="auto: do not play the human; approve with scripts/wf_run.py yourself")
    args = ap.parse_args()
    d = Demo(Path(args.workspace).resolve(), args.fast)

    if args.command in ("setup", "reset"):
        return setup(d)
    if args.command == "auto":
        return auto(d, args)
    if args.command == "sync":
        d.require_ws()
        for name in ("config", "skills", "scripts", ".github"):
            shutil.copytree(PROJECT / name, d.ws / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "repos.yaml", "repos.local.yaml"))
        return print(f"Copied the project's config, skills, scripts and agents into {d.ws}. Tickets, repos and repo config are untouched.")
    if args.command == "list":
        return list_steps(d)
    d.require_ws()
    if args.command == "status":
        return d.status() if d.run_dir.exists() else print("No ticket yet. Run: python demo/demo.py next")
    if args.command == "stale":
        if args.arg not in ("on", "off"):
            sys.exit("usage: demo.py stale on|off")
        return stale(d, args.arg)
    if (d.ws / ".spec_backup").exists():
        sys.exit("error: the stale detour is on (spec.md is edited). Run: python demo/demo.py stale off")
    cur = d.state()["step"]
    if cur >= len(STEPS):
        return print("All steps are done. Run: python demo/demo.py reset")
    if args.command == "next":
        return do_step(d, cur + 1)
    for n in range(cur + 1, len(STEPS) + 1):
        do_step(d, n)
        if n < len(STEPS) and not (args.fast or args.no_pause):
            input("\n-- press Enter for the next step (Ctrl+C to stop) -- ")


if __name__ == "__main__":
    main()
