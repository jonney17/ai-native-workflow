#!/usr/bin/env python3
"""Scripted stand-in for a lane agent. NO AI is involved.

It is started by scripts/wf_launch.py exactly like a real agent would be, so the demo
exercises the real launcher, the real status script and the real telemetry check.
What is real: git branches and commits in the fake repos, the test runs, the artifacts
(text comes from content/scripted.py), and the events.log lines.
What is fake: the thinking, and the telemetry numbers (marked demo.synthetic=true).

Environment: DEMO_WORKSPACE (required), DEMO_PACE (seconds between messages, default 0.5).
The lane, ticket, run, repo and attempt come from OTEL_RESOURCE_ATTRIBUTES, set by the launcher.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "content"))
import scripted as C  # noqa: E402

WS = Path(os.environ["DEMO_WORKSPACE"])
PACE = float(os.environ.get("DEMO_PACE", "0.5"))


def parse_attrs():
    out = {}
    for pair in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


ATTRS = parse_attrs()
TICKET = ATTRS["workflow.ticket"]
RUN = int(ATTRS["workflow.run"])
LANE = ATTRS["workflow.lane"]
REPO = ATTRS.get("workflow.repo", "")
ATTEMPT = int(ATTRS.get("workflow.attempt", "1"))
OVERLAY = [x for x in ATTRS.get("workflow.overlay", "").split("+") if x]
RUN_DIR = WS / "tickets" / TICKET / f"run-{RUN}"
ART = RUN_DIR / "artifacts"
NAME = f"{LANE}-{REPO}" if REPO else LANE
TOOL_CALLS = []


def say(msg, tool=None):
    print(f"  [{NAME}] {msg}", flush=True)
    if tool:
        TOOL_CALLS.append(tool)
    time.sleep(PACE)


def short_hash(p):
    h = hashlib.sha256()
    if p.is_dir():
        for f in sorted(x for x in p.rglob("*") if x.is_file()):
            h.update(f.relative_to(p).as_posix().encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    else:
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:8]


def event(name, detail):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(RUN_DIR / "events.log", "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{ts}\tagent:{NAME}\t{name}\t{json.dumps(detail, sort_keys=True)}\n")


def header(name, inputs):
    refs = ", ".join(f"{i}@{short_hash(ART / i)}" for i in inputs)
    return (f"---\nartifact: {name}\nticket: {TICKET}\nrun: {RUN}\nauthor: stub-{NAME} (scripted demo, no AI)\n"
            f"inputs: [{refs}]\noverlay_applied: [{', '.join(OVERLAY)}]\nstatus: ready\n---\n")


def write_artifact(name, body, inputs, base=None):
    path = (base or ART) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header(name, inputs) + "\n" + body, encoding="utf-8", newline="\n")
    say(f"wrote {path.relative_to(RUN_DIR).as_posix()}")


def read_skills():
    say("reading skills/_common.md", "read_file")
    say(f"reading skills/{LANE}/instructions.md", "read_file")
    if OVERLAY:
        say("reading overlay candidates: " + ", ".join(OVERLAY), "read_file")


def run_tests(cwd, start="tests", extra_path=None):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if extra_path:
        env["PYTHONPATH"] = str(extra_path)
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(start), "-v"],
                          cwd=cwd, env=env, capture_output=True, text=True)


def git(repo, *args, check=True):
    cmd = ["git", "-c", "user.name=stub-dev", "-c", "user.email=demo@example.com", *args]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=check)


# ------------------------------------------------------------------ lanes

def planner():
    read_skills()
    say("reading ticket-context.md", "read_file")
    say("exploring the repos listed in the registry", "search_code")
    write_artifact("spec.md", C.SPEC, ["ticket-context.md"])
    write_artifact("design.md", C.DESIGN, ["ticket-context.md", "spec.md"])
    write_artifact("tasks.md", C.TASKS, ["ticket-context.md", "spec.md", "design.md"])
    say("done: spec, design and tasks are ready; waiting for a human to approve them")


def qe():
    read_skills()
    say("reading spec.md and tasks.md (approved)", "read_file")
    write_artifact("test-plan.md", C.TEST_PLAN, ["spec.md", "tasks.md"])
    for repo, text in C.QE_TESTS.items():
        p = ART / "tests" / repo / "test_acceptance.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        say(f"wrote artifacts/tests/{repo}/test_acceptance.py", "write_file")
    say("done: test plan and acceptance tests are ready")


def dev():
    repo_path = Path(os.environ["WF_REPO_PATH"])
    read_skills()
    say("reading spec.md, design.md, tasks.md (approved)", "read_file")
    plan = C.DEV[(REPO, ATTEMPT)]
    branch = f"wf/{TICKET}-run{RUN}"
    if ATTEMPT == 1:
        git(repo_path, "checkout", "main")
        git(repo_path, "checkout", "-B", branch)
        say(f"created branch {branch} in {REPO}", "git")
    else:
        git(repo_path, "checkout", branch)
        rejected = [e for e in (RUN_DIR / "events.log").read_text(encoding="utf-8").splitlines() if "verify_failed" in e and REPO in e]
        say("reading the verify_failed proposal and verify-report.md", "read_file")
        say(f"continuing on branch {branch} ({len(rejected)} verify_failed on record)", "git")
    for rel, text in plan["files"].items():
        p = repo_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        say(f"edited {REPO}/{rel}", "write_file")
    res = run_tests(repo_path)
    tail = [l for l in res.stderr.strip().splitlines() if l][-1:] or ["(no output)"]
    say(f"ran the repo's own tests: {tail[0]}", "run_tests")
    if res.returncode != 0:
        print(res.stdout + res.stderr)
        say("own tests failed; stopping")
        sys.exit(1)
    git(repo_path, "add", "-A")
    git(repo_path, "commit", "-m", plan["commit"])
    say(f"committed: {plan['commit']}", "git")
    out = ART / "changes" / REPO
    out.mkdir(parents=True, exist_ok=True)
    diff = git(repo_path, "diff", f"main..{branch}").stdout
    (out / "diff.patch").write_text(diff, encoding="utf-8", newline="\n")
    ins = ["spec.md", "design.md", "tasks.md"]
    write_artifact("summary.md", f"# {REPO} (attempt {ATTEMPT})\n\n{plan['summary']}\n\nBranch: `{branch}`\n", ins, base=out)
    write_artifact("notes.md", f"# Notes for the Reviewer\n\n{plan['notes']}\n", ins, base=out)
    say("done: changes are ready for review")


def reviewer():
    read_skills()
    say("reading test-plan.md and changes/*", "read_file")
    paths = json.loads(os.environ.get("WF_REPO_PATHS", "{}"))
    tasks = (ART / "tasks.md").read_text(encoding="utf-8")
    repos = [r for r in paths if re.search(r"(?<![\w-])" + re.escape(r) + r"(?![\w-])", tasks)]
    branch = f"wf/{TICKET}-run{RUN}"
    rows, failed = [], {}
    for repo in repos:
        rp = Path(paths[repo])
        git(rp, "checkout", branch)
        own = run_tests(rp)
        say(f"{repo}: repo tests {'pass' if own.returncode == 0 else 'FAIL'}", "run_tests")
        qe = run_tests(rp, start=ART / "tests" / repo, extra_path=rp)
        for m in re.finditer(r"^(test_ac(\d)\w*) \(.*?\) \.\.\. (ok|FAIL|ERROR)", qe.stdout + qe.stderr, re.M):
            ok = m.group(3) == "ok"
            rows.append((f"AC-{m.group(2)}", repo, m.group(1), ok))
            if not ok:
                failed.setdefault(repo, []).append(f"AC-{m.group(2)}")
        say(f"{repo}: acceptance tests {'pass' if repo not in failed else 'FAIL ' + ', '.join(failed[repo])}", "run_tests")
    rows.sort()
    table = "\n".join(f"| {a} | {r} | {t} | {'pass' if ok else '**FAIL**'} |" for a, r, t, ok in rows)
    result = "FAIL" if failed else "PASS"
    body = (f"# Verify report (attempt {ATTEMPT})\n\nResult: **{result}**\n\n## Coverage matrix\n"
            f"| AC | Repo | Test | Result |\n|----|------|------|--------|\n{table}\n")
    if failed:
        body += "\n## Failures\n" + "\n".join(f"- {r}: {', '.join(a)}" for r, a in failed.items()) + "\n"
    write_artifact("verify-report.md", body, ["test-plan.md", "changes"])
    for repo, acs in failed.items():
        event("verify_failed", {"repo": repo, "acs": acs, "attempt": ATTEMPT})
        say(f"proposed verify_failed for {repo} ({', '.join(acs)})")
    if failed:
        say("done: verification failed; no review written")
        return
    say("reading the diffs and notes.md of each repo", "read_file")
    signals = []
    if re.search(r"deploy_order:\s*ordered", tasks) and len(repos) > 1:
        signals.append("multi_repo_ordered_deploy")
    for repo in repos:
        notes = (ART / "changes" / repo / "notes.md").read_text(encoding="utf-8")
        signals += [s for s in re.findall(r"Signal:\s*(\w+)", notes) if s not in signals]
    say("evaluated config/risk-rules.yaml: " + (", ".join(signals) or "no signal"), "read_file")
    head = f"human_required: {'true' if signals else 'false'}\nsignals: [{', '.join(signals)}]\n\n"
    write_artifact("review-report.md", head + C.REVIEW_BODY, ["test-plan.md", "changes"])
    say("done: review written; a human decides because the review is risk-flagged" if signals else "done")


def inspect_adapt():
    read_skills()
    events = (RUN_DIR / "events.log").read_text(encoding="utf-8")
    loops = events.count("\tverify_failed\t")
    metrics = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((RUN_DIR / "metrics").glob("*.json"))]
    tin = sum(m.get("tokens_in", 0) for m in metrics)
    tout = sum(m.get("tokens_out", 0) for m in metrics)
    say(f"read events.log ({loops} rework loop) and {len(metrics)} metrics files", "read_file")
    say("read the ledger and the current overlay", "read_file")
    write_artifact("inspection-report.md",
                   C.INSPECTION.format(ticket=TICKET, loops=loops, sessions=len(metrics), tin=f"{tin:,}", tout=f"{tout:,}"),
                   ["review-report.md"])
    lid = C.LEDGER["id"]
    ledger = WS / "ledger" / f"{lid}.yaml"
    ledger.parent.mkdir(exist_ok=True)
    ledger.write_text(
        f"id: {lid}\ncreated: {datetime.now().date().isoformat()}\nticket: {TICKET}\nkind: story\nrun: {RUN}\n"
        f"lane: {C.LEDGER['lane']}\nobservation: \"{C.LEDGER['observation']}\"\nevidence:\n"
        f"  - tickets/{TICKET}/run-{RUN}/artifacts/verify-report.md\n"
        f"  - tickets/{TICKET}/run-{RUN}/metrics/dev-shop-web-a1.json\n"
        f"  - tickets/{TICKET}/run-{RUN}/metrics/dev-shop-web-a2.json\n"
        f"candidate:\n  target: overlay/{C.LEDGER['lane']}/{lid}.md\n  change: \"{C.LEDGER['change']}\"\n"
        f"status: active\nactive_on: []\noutcome: {{}}\nretro_decision: null\nretro_reason: null\n",
        encoding="utf-8", newline="\n")
    say(f"wrote ledger/{lid}.yaml")
    ov = WS / "overlay" / C.LEDGER["lane"] / f"{lid}.md"
    ov.parent.mkdir(parents=True, exist_ok=True)
    ov.write_text(C.OVERLAY, encoding="utf-8", newline="\n")
    say(f"wrote overlay/{C.LEDGER['lane']}/{lid}.md (removable, advisory)")
    say("done")


# ------------------------------------------------------------------ synthetic telemetry

def write_telemetry():
    """Synthetic telemetry in the same shape the real Copilot CLI file exporter writes (one JSON object per line)."""
    dump = os.environ.get("COPILOT_OTEL_FILE_EXPORTER_PATH")
    if not dump:
        return
    calls, tin, tout, tools = C.TOKENS.get((LANE, REPO, ATTEMPT), (3, 20000, 2500, 5))
    conversation = str(uuid.uuid4())
    trace = uuid.uuid4().hex
    resource = {"attributes": {**ATTRS, "service.name": "github-copilot", "service.version": "demo", "demo.synthetic": True}}

    def span(name, attrs):
        return {"type": "span", "traceId": trace, "spanId": uuid.uuid4().hex[:16], "name": name, "kind": 0,
                "attributes": {"gen_ai.conversation.id": conversation, "demo.synthetic": True, **attrs},
                "status": {"code": 0}, "events": [], "resource": resource}

    rows = []
    for i in range(calls):
        rows.append(span("chat demo-model", {
            "gen_ai.operation.name": "chat", "gen_ai.response.model": "demo-model",
            "gen_ai.usage.input_tokens": tin // calls, "gen_ai.usage.output_tokens": tout // calls,
            "gen_ai.usage.cache_read.input_tokens": (tin // calls) * 3 // 4 if i else 0,
            "gen_ai.usage.cache_write.input_tokens": 0, "gen_ai.usage.reasoning.output_tokens": tout // calls // 2,
            "github.copilot.cost": 1, "github.copilot.nano_aiu": 70000000 * (1 + i % 3)}))
    for i in range(tools):
        rows.append(span("execute_tool " + (TOOL_CALLS[i] if i < len(TOOL_CALLS) else "read_file"),
                         {"gen_ai.operation.name": "execute_tool",
                          "gen_ai.tool.name": TOOL_CALLS[i] if i < len(TOOL_CALLS) else "read_file"}))
    rows.append(span("invoke_agent", {"gen_ai.operation.name": "invoke_agent",
                                      "gen_ai.usage.input_tokens": tin // calls * calls,
                                      "gen_ai.usage.output_tokens": tout // calls * calls,
                                      "github.copilot.cost": calls, "github.copilot.turn_count": calls}))
    rows.append({"type": "metric", "name": "gen_ai.client.inference.usage.input_tokens", "demo.synthetic": True})
    Path(dump).parent.mkdir(parents=True, exist_ok=True)
    with open(dump, "a", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def main():
    print(f"  [{NAME}] stub agent started (scripted demo, attempt {ATTEMPT})", flush=True)
    {"planner": planner, "qe": qe, "dev": dev, "reviewer": reviewer, "inspect-adapt": inspect_adapt}[LANE]()
    write_telemetry()


if __name__ == "__main__":
    main()
