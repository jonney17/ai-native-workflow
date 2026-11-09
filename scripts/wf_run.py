#!/usr/bin/env python3
"""Agent workers: start a run once, then every agent works on its own.

Each agent is a small loop that looks at the derived state (scripts/wf_status.py). When its agent is ready
it takes a lease, launches one agent session through scripts/wf_launch.py, records
the artifacts it produced in the manifest, and applies the events that agents proposed. There is no
central controller: workers only share the files in the run folder.

Commands (run from the project root):
  start    --ticket T [--run N] [--auto-approve] [--verbose]   start one worker per agent and wait
  worker   --ticket T --agent L                                 run one agent's loop by hand (what start spawns)
  ready    --ticket T [--artifact ticket-context.md]           intake: mark the ticket context ready
  approve  --ticket T [--gate planner|reviewer] [--by NAME]    human gate: approve and move on
  reject   --ticket T --gate planner|reviewer --reason TEXT    human gate: send the work back
  retry    --ticket T --agent L [--repo R]                      give a failed agent a fresh attempt budget and run it again
  answer   --ticket T --agent L [--text TEXT]                   unblock an agent after a question_raised
  accept   --ticket T [--by NAME]                              human: the ticket is accepted
  deployed --ticket T --repo R                                 record a deploy done outside (CI/CD)
  command  --ticket T --agent L [--repo R]                      print the agent command a worker would run
  status   --ticket T                                          same as wf_status.py

--auto-approve plays the human at every gate. It is for testing the flow only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wf_status as S  # noqa: E402
import wf_state as W  # noqa: E402
import wf_launch as L  # noqa: E402
import wf_config as C  # noqa: E402

try:
    import yaml
except ImportError:
    sys.exit("error: PyYAML is required. Install it with: python -m pip install -r requirements.txt")

ROOT = S.ROOT
PY = sys.executable
OK = S.OK
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PLACEHOLDER = re.compile(r"<(paste|the question|text|link to|ids|repos|what will|why this|for example|tech-debt)", re.I)


def say(who, msg):
    print(f"[{who}] {msg}", flush=True)


def load_cfg(name, default=None):
    p = ROOT / "config" / name
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else (default or {})


# ---------------------------------------------------------------- what to launch

def prompt_parts(runner, run, agent, repo, attempt):
    """The task message for one session, and the values it was built from."""
    repo_path = ""
    if repo:
        reg, loc = L.read_registry(ROOT / "config" / "repos.yaml"), L.read_registry(ROOT / "config" / "repos.local.yaml")
        found = L.resolve_repo(repo, reg, loc) if repo in reg else None
        repo_path = str(found) if found else ""
    answers = [e["detail"].get("text", "") for e in run.events()
               if e["event"] == "question_answered" and e["detail"].get("agent") == agent
               and (not repo or e["detail"].get("repo") in ("", None, repo)) and e["detail"].get("text")]
    notes = []
    if answers:
        notes.append("answers from a person to your earlier question(s), oldest first:\n" + "\n".join(f"  - {answer}" for answer in answers))
    if S.config().agent(str(run.load().get("kind") or "story"), agent)["gate"]:
        rejections = [event for event in run.events()
                      if event["event"] == "gate_rejected" and event["detail"].get("gate") == agent]
        artifacts = run.load().get("artifacts") or {}
        pending_rejections = [
            event for event in rejections
            if any((artifacts.get(name) or {}).get("status") == "rejected"
                   for name in event["detail"].get("artifacts") or [])
        ]
        if pending_rejections:
            rejection = pending_rejections[-1]["detail"]
            rejected = ", ".join(rejection.get("artifacts") or [])
            notes.append(
                "human feedback on your rejected work; address it in this rework:\n"
                f"  rejected artifacts: {rejected or 'your output'}\n"
                f"  reason: {rejection.get('reason') or '(no reason provided)'}"
            )
    extra = "\n  " + "\n  ".join(notes) if notes else ""
    repo_line = (f"\n  repo: {repo}" + (f", at {repo_path}" if repo_path else "")) if repo else ""
    fmt = {"agent": agent, "lane": agent, "instructions": agent_instructions(agent), "repo": repo, "ticket": run.ticket, "run": run.run, "attempt": attempt, "root": str(ROOT),
           "run_dir": str(run.dir), "repo_line": repo_line, "extra_line": extra, "repo_path": repo_path}
    return task_message(fmt), fmt


def task_message(fmt):
    """The first message of an agent session. Fixed on purpose: the role and the real task live in the agent file
    (.github/agents/<agent>.agent.md); this only names that file and gives the facts of this run."""
    return (f"You are the {fmt['instructions']} agent. Read .github/agents/{fmt['instructions']}.agent.md first and follow it exactly. "
            f"Do only that agent's work.\n"
            f"Work item:\n  root: {fmt['root']}\n  run_dir: {fmt['run_dir']}\n"
            f"  ticket: {fmt['ticket']}, run: {fmt['run']}, attempt: {fmt['attempt']}{fmt['repo_line']}{fmt['extra_line']}\n"
            f"Your agent file says what to read and what to write.")


def chat_message(agent, task_dir):
    """The first message of a chat with an agent outside any ticket (fixed, like task_message)."""
    return (f"This is a chat with a person outside any ticket, not a ticket run. You are the {agent} agent.\n"
            f"root: {ROOT}\ntask_dir: {task_dir}\n"
            f"Read .github/agents/{agent_instructions(agent)}.agent.md and skills/_common.md (section \"Ad-hoc chat\"). "
            f"Answer in your messages. Put any file you produce in task_dir.\n")


def agent_command(runner, run, agent, repo, attempt):
    """Argv of a one-shot session (copilot -p)."""
    prompt, fmt = prompt_parts(runner, run, agent, repo, attempt)
    repo_path = fmt["repo_path"]
    fmt2 = dict(fmt, prompt=prompt, repo="-" + repo if repo else "")
    override = os.environ.get("WF_AGENT_CMD")
    base = json.loads(override) if override else list((runner.get("agent") or {}).get("command") or [])
    if not base:
        sys.exit("error: no agent command. Set agent.command in config/runner.yaml or WF_AGENT_CMD")
    extra_args = list(((runner.get("agent") or {}).get("by_agent") or {}).get(agent) or []) + ([] if override else agent_args(agent))
    if repo and repo_path:
        extra_args += list((runner.get("agent") or {}).get("repo_args") or [])
    return [str(x).format(**fmt2) for x in base + extra_args]


# ---------------------------------------------------------------- ACP: an agent keeps one session across questions

sessions = {}   # unit -> wf_acp.Session, kept open while the agent waits for an answer
seen = {}       # unit -> how many answers the session has already been given


def use_acp(runner):
    return bool(os.environ.get("WF_ACP_CMD")) or runner.get("mode") == "acp"


def agent_instructions(agent):
    """The Copilot agent file an agent runs (config/agents.yaml `instructions`, default wf-<name>)."""
    cfg = S.config()
    return cfg.agent("", agent)["instructions"] if agent in cfg.agent_names() else f"wf-{agent}"


def agent_args(agent):
    """Extra Copilot options for an agent: `model` and `args` in config/agents.yaml."""
    cfg = S.config()
    if agent not in cfg.agent_names():
        return []
    l = cfg.agent("", agent)
    return (["--model", l["model"]] if l["model"] else []) + l["args"]


def acp_cmd(runner, fmt, repo):
    o = os.environ.get("WF_ACP_CMD")
    acp = runner.get("acp") or {}
    base = json.loads(o) if o else list(acp.get("command") or [])
    extra = list(acp.get("repo_args") or []) if (repo and fmt["repo_path"] and not o) else []
    fmt = dict(fmt, instructions=fmt.get("instructions") or agent_instructions(fmt["agent"]))
    return [str(x).format(**fmt) for x in base + extra] + ([] if o else agent_args(fmt["agent"]))


def answers_given(run, agent, repo):
    return [e["detail"].get("text", "") for e in run.events() if e["event"] == "question_answered" and e["detail"].get("agent") == agent
            and (e["detail"].get("repo") or "") in ("", repo)]


def acp_turn(run, runner, agent, repo, unit, attempt, log, who, verbose):
    """One turn of the agent's session: a new session with the task, or the person's answer sent to the open one."""
    import wf_acp as A
    prompt, fmt = prompt_parts(runner, run, agent, repo, attempt)
    echo = (lambda line: say(who, line)) if verbose else None
    given = answers_given(run, agent, repo)
    sess, rc, msg = sessions.get(unit), 0, ""
    with open(log, "a", encoding="utf-8", errors="replace") as lf:
        try:
            if sess is not None and sess.alive() and len(given) > seen.get(unit, 0):
                text = "Answer from a person: " + " | ".join(given[seen.get(unit, 0):]) + "\nContinue your work."
                sess.log, sess.echo = lf, echo
            else:
                if sess is not None:
                    sess.close()
                env = {"WF_TICKET": run.ticket, "WF_RUN": str(run.run), "WF_AGENT": agent, "WF_ATTEMPT": str(attempt)}
                if repo:
                    env["WF_REPO"] = repo
                sess = sessions[unit] = A.Session(acp_cmd(runner, fmt, repo), ROOT, env, lf, echo)
                sess.idle_seconds = int((runner.get("acp") or {}).get("idle_seconds") or 0)
                text = prompt
            seen[unit] = len(given)
            run.event("launcher", "agent_started", {"agent": agent, "repo": repo, "attempt": attempt, "mode": "acp"})
            _, msg = sess.prompt(text)
        except Exception as e:
            rc, msg = 1, str(e)
            lf.write(f"\nERROR: {e}\n")
            sessions.pop(unit, None)
        finally:
            sess and setattr(sess, "log", None)
            run.event("launcher", "agent_exited", {"agent": agent, "repo": repo, "returncode": rc})
    return rc, msg


def launchable(res, agent, run, max_attempts):
    """Agent instances (repo names, or '' for the agent itself) that may start now."""
    rec = (res["agents"] or {}).get(agent)
    if not rec or rec["unmet"] or res["status"] == "Escalated":
        return []
    units = []
    if rec.get("fan_out"):
        for repo, x in rec["repos"].items():
            if x["state"] in ("ready", "stale") or (x["state"] == "working" and run.last_outcome(agent, repo) in ("incomplete", "crashed", "blocked")):
                units.append(repo)
    elif rec["state"] in ("ready", "stale") or (rec["state"] == "working" and run.last_outcome(agent, "") in ("incomplete", "crashed", "blocked")):
        units.append("")
    return units


# ---------------------------------------------------------------- after a session ends

def reworks_used(events, name, agent):
    """Rework rounds an agent has already caused with this event, since a person last reset its attempts (retry)."""
    used = 0
    for e in events:
        d = e["detail"]
        if e["event"] == "rework_requested" and d.get("event") == name and d.get("from") == agent:
            used += 1
        elif e["event"] == "attempts_reset" and d.get("agent") == agent and not d.get("repo"):
            used = 0
    return used


def apply_proposals(run, who):
    """Apply pending agent proposals exactly once. Any worker may do it; the log says which ones are done."""
    events = run.events()
    done = {e["detail"].get("id") for e in events if e["event"] == "proposal_applied"}
    cfg = S.config()
    m = run.load()
    kind = str(m.get("kind") or "story")
    repos = S.ticket_repos(m, S.build_view(run.dir, m, cfg, kind))
    rules = cfg.events()
    names = S.proposal_names(cfg)
    agents = cfg.agent_names()
    arts = cfg.artifacts(kind)
    for e in events:
        if e["event"] not in names or e["actor"] in ("orchestrator", "launcher"):
            continue
        pid = S.proposal_id(e)
        if pid in done:
            continue
        d = e["detail"]
        name = e["event"]
        agent = d.get("agent") or next((l for l in sorted(agents, key=len, reverse=True) if l in e["actor"]), None)
        rule = rules.get(name) or {}
        allowed = list(rule.get("from") or [])
        if name in rules and allowed and agent not in allowed:
            run.event("orchestrator", "proposal_ignored", {"id": pid, "event": name, "from": agent or e["actor"],
                                                           "why": f"{name} may only come from {', '.join(allowed)}"})
            say(who, f"ignored {name} from {agent or e['actor']}: only {', '.join(allowed)} may raise it")
        elif name in rules:
            sent = []
            named = d.get("repo") or d.get("repos") or []
            named = [named] if isinstance(named, str) else list(named)
            for token in rule.get("rejects") or []:
                key, _, per_repo = C.parse(token)
                if key not in arts:
                    continue
                if per_repo or arts[key]["per_repo"]:
                    for r in (named or repos):
                        run.mark(key, "rejected", repo=r)
                        sent.append(f"{key}/{r}")
                else:
                    run.mark(key, "rejected")
                    sent.append(key)
            limit = int((load_cfg("budgets.yaml").get("per_run") or {}).get("max_reworks", 3))
            if sent and limit and reworks_used(events, name, agent) >= limit:
                run.set_agent(agent, "failed")
                run.event("orchestrator", "rework_limit", {"event": name, "from": agent, "limit": limit, "artifacts": sent})
                run.decide(f"{name} from {agent or e['actor']} refused: {limit} rework rounds for it are used up, so the ticket is escalated. A person decides (retry --agent {agent} resets the count).")
                say(who, f"{name}: {limit} rework rounds used up; {agent} marked failed, ticket escalated")
            elif not sent:
                run.event("orchestrator", "proposal_ignored", {"id": pid, "event": name, "why": "this kind of work has none of the artifacts it rejects"})
                say(who, f"ignored {name}: this kind of work has none of {', '.join(rule.get('rejects') or [])}")
            else:
                why = d.get("reason") or d.get("gap") or ", ".join(d.get("acs") or []) or "no detail given"
                run.event("orchestrator", "rework_requested", {"event": name, "artifacts": sent, "from": agent})
                run.decide(f"{name} from {agent or e['actor']} accepted: {', '.join(sent)} sent back ({why}).")
                say(who, f"applied {name}: {', '.join(sent)} go back to the agent that produces them")
        elif name == "question_raised" and agent:
            run.set_agent(agent, "blocked", d.get("repo") or None)
            say(who, f"{agent} is blocked on a question: {d.get('question', d.get('text', ''))}")
        elif name == "escalate" and agent:
            run.set_agent(agent, "failed")
            say(who, f"{agent} escalated: {d.get('reason', '')}")
        else:
            run.event("orchestrator", "proposal_unclear", {"id": pid, "event": name})
        run.event("orchestrator", "proposal_applied", {"id": pid, "event": name})


def copy_risk_decision(run, agent, gate, who):
    """For a `when: risk_flagged` gate: copy `human_required: true|false` from the agent's report into the manifest."""
    src = C.parse(gate.get("decision_in") or (gate.get("artifacts") or [""])[0])[0]
    p = run.path_of(src) if src else None
    if not p or not p.is_file():
        return
    m = re.search(r"^human_required:\s*(true|false)\b", p.read_text(encoding="utf-8"), re.M | re.I)
    if not m:
        return
    value = m.group(1).lower() == "true"
    if S.gate_decision(run.load(), agent) != value:
        run.update(lambda man: man.setdefault("gates", {}).setdefault(agent, {}).update(human_required=value))
        run.event("orchestrator", "risk_decision_applied", {"agent": agent, "human_required": value})
        say(who, f"copied {agent}'s risk decision from {src}: human_required = {str(value).lower()}")


def record_outputs(run, cfg, kind, agent, repo, started, who):
    """Mark every artifact this session wrote as ready, with its input hashes. Returns (marked, expected)."""
    l = cfg.agent(kind, agent)
    marked, expected = [], []
    for key in l["produces"]:
        expected.append(key)
        per_repo = key in l["per_repo"] and repo
        path = run.art / key / repo if per_repo else run.path_of(key)
        if not path.exists() or W.latest_mtime(path) < started - 1:
            continue
        if per_repo:
            run.mark(key, "ready", inputs=cfg.inputs(kind, key), repo=repo, version=run.attempts_done(agent, repo) + 1)
        else:
            run.mark(key, "ready", inputs=cfg.inputs(kind, key))
        marked.append(key)
    return marked, expected


# ---------------------------------------------------------------- one session

def run_unit_locked(holder, run, cfg, kind, runner, budgets, agent, repo, who, verbose):
    """run_unit, then free the repo lock taken for it (one writer per repo across tickets)."""
    try:
        run_unit(run, cfg, kind, runner, budgets, agent, repo, who, verbose)
    finally:
        if repo:
            W.repo_release(repo, holder)


def run_unit(run, cfg, kind, runner, budgets, agent, repo, who, verbose):
    unit = f"{agent}-{repo}" if repo else agent
    attempt = run.attempts_done(agent, repo) + 1
    max_attempts = int((budgets.get("per_agent") or {}).get("max_attempts", 3))
    if run.budget_used(agent, repo) >= max_attempts:
        run.set_agent(agent, "failed", repo or None)
        run.event("orchestrator", "agent_failed", {"agent": agent, "repo": repo, "reason": f"more than {max_attempts} attempts"})
        say(who, f"{unit}: gave up after {max_attempts} attempts; escalating")
        run.release(unit)
        return
    logdir = run.dir / "logs"
    logdir.mkdir(exist_ok=True)
    log = logdir / f"{unit}-a{attempt}.log"
    run.set_agent(agent, "working", repo or None)
    say(who, f"{unit}: starting attempt {attempt}")
    started = time.time()
    msg = None
    if use_acp(runner):
        rc, msg = acp_turn(run, runner, agent, repo, unit, attempt, log, who, verbose)
    else:
        cmd = agent_command(runner, run, agent, repo, attempt)
        launch = [PY, str(HERE / "wf_launch.py"), "--ticket", run.ticket, "--run", str(run.run), "--agent", agent,
                  "--attempt", str(attempt)] + (["--repo", repo] if repo else []) + ["--"] + cmd
        with open(log, "w", encoding="utf-8", errors="replace") as lf:
            p = subprocess.Popen(launch, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace")
            for line in p.stdout:
                lf.write(line)
                if verbose:
                    say(who, line.rstrip())
            rc = p.wait()
    secs = round(time.time() - started, 1)

    marked, expected = [], []
    if rc == 0:
        marked, expected = record_outputs(run, cfg, kind, agent, repo, started, who)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(started - 1))
    if msg and rc == 0 and len(marked) < len(expected) and not any(
            e["event"] in S.proposal_names(cfg) and e["actor"] in (unit, agent) and e["time"] >= stamp for e in run.events()[-50:]):
        if "?" in msg[-600:]:   # it stopped without finishing and asked something: that is its question
            run.event(unit, "question_raised", {"agent": agent, "repo": repo, "question": msg.strip()[-1500:]})
        else:                   # it just stopped: not a question; the attempt counts as incomplete and is retried
            say(who, f"{unit}: stopped without its artifacts and without a question: {' '.join(msg.split())[:200]}")
    apply_proposals(run, who)
    gate = cfg.agent(kind, agent)["gate"]
    if gate and gate.get("when") == "risk_flagged":
        copy_risk_decision(run, agent, gate, who)
    proposed = any(e["event"] in S.proposal_names(cfg) and e["actor"] not in ("orchestrator", "launcher")
                   and e["event"] != "question_raised" for e in run.events()[-50:]
                   if e["time"] >= time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(started - 1)))
    outcome = "crashed" if rc != 0 else ("ok" if (expected and len(marked) == len(expected)) or proposed else "incomplete")
    ent = (run.load().get("agents") or {}).get(agent)
    blocked = (((ent.get("repos") or {}).get(repo) if repo else ent.get("status")) if isinstance(ent, dict) else None)
    if blocked == "blocked" and outcome == "incomplete":
        outcome = "blocked"
    if blocked not in ("blocked", "failed"):
        run.set_agent(agent, "waiting", repo or None)
    if outcome != "blocked" and unit in sessions:   # a blocked agent keeps its session for the answer
        sessions.pop(unit).close()
    run.event(f"worker:{agent}", "agent_finished", {"agent": agent, "repo": repo, "attempt": attempt, "returncode": rc,
                                                  "outcome": outcome, "seconds": secs, "artifacts": marked,
                                                  "log": log.relative_to(ROOT).as_posix()})
    run.release(unit)
    say(who, f"{unit}: attempt {attempt} {outcome} in {secs}s; marked {', '.join(marked) or 'nothing'}")
    if outcome != "ok":
        say(who, f"{unit}: see {log.relative_to(ROOT).as_posix()}")


# ---------------------------------------------------------------- workers

def agent_worker(args):
    run = W.Run(args.ticket, args.run)
    cfg = S.config()
    runner, budgets = load_cfg("runner.yaml"), load_cfg("budgets.yaml")
    poll = float(runner.get("poll_seconds", 5))
    idle_limit = float(runner.get("idle_exit_minutes", 120)) * 60
    lease_min = float((budgets.get("per_agent") or {}).get("lease_minutes", 30))
    agent, who = args.agent, args.agent
    threads, last_busy, said = {}, time.time(), None
    while True:
        res = S.analyze(args.ticket, args.run)
        kind = res["kind"]
        rec = res["agents"].get(agent)
        if rec is None or rec["state"] == "disabled":
            say(who, "not part of this run (disabled or not in this kind); exiting")
            return
        for u in [u for u, t in threads.items() if not t.is_alive()]:
            del threads[u]
        for repo in launchable(res, agent, run, 3):
            if repo in threads:
                continue
            unit = f"{agent}-{repo}" if repo else agent
            if not run.acquire(unit, lease_min):
                continue
            holder = f"{args.ticket}/run-{args.run}/{unit}"
            if repo and W.repo_acquire(repo, holder, lease_min) is None:   # another ticket is changing this repo
                run.release(unit)
                msg = f"waiting for repo {repo} (in use by {W.repo_holder(repo) or 'another ticket'})"
                if msg != said:
                    say(who, msg)
                    said = msg
                continue
            t = threading.Thread(target=run_unit_locked, args=(holder, run, cfg, kind, runner, budgets, agent, repo, who, args.verbose), daemon=True)
            threads[repo] = t
            t.start()
            last_busy = time.time()
        if threads:
            last_busy = time.time()
        finished = res["status"] in ("Done", "Escalated")
        if finished and not threads and not launchable(res, agent, run, 3):
            say(who, f"run is {res['status'].lower()}; exiting")
            return
        if not threads and time.time() - last_busy > idle_limit:
            say(who, f"idle for {idle_limit / 60:.0f} minutes; exiting")
            return
        wait = f"{rec['state']}: {rec['note']}" if not threads else None
        if wait and wait != said:
            say(who, f"waiting ({wait[:110]})")
            said = wait
        time.sleep(poll)


def ci_worker(args):
    """CI/CD stand-in. Not an agent: it follows the Release order once the review has passed."""
    run = W.Run(args.ticket, args.run)
    runner = load_cfg("runner.yaml")
    cmd_t = json.loads(os.environ["WF_DEPLOY_CMD"]) if os.environ.get("WF_DEPLOY_CMD") else (runner.get("deploy") or {}).get("command")
    if not cmd_t:   # placeholder: the team plugs in the real merge/deploy command (deploy.command in runner.yaml)
        cmd_t = [PY, "-c", "print('placeholder deploy of {repo}')"]
    poll = float(runner.get("poll_seconds", 5))
    who, said = "ci", None
    while True:
        res = S.analyze(args.ticket, args.run)
        if res["status"] in ("Done", "Escalated"):
            say(who, f"run is {res['status'].lower()}; exiting")
            return
        if res["status"] == "Deploying" and cmd_t:
            m = run.load()
            rel = m.get("release") or {}
            repos = list(res.get("repos") or [])
            order = [str(x) for x in rel.get("sequence") or []] if rel.get("deploy_order") == "ordered" else repos
            state = rel.get("repos") or {}
            todo = [r for r in order if state.get(r) != "deployed"]
            if todo and state.get(todo[0]) != "failed":
                repo = todo[0]
                run.event("ci_cd", "deploy_started", {"repo": repo})
                cmd = [str(x).format(repo=repo, ticket=run.ticket, run=run.run, root=str(ROOT)) for x in (cmd_t if isinstance(cmd_t, list) else cmd_t.split())]
                r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
                ok = r.returncode == 0
                run.update(lambda man: man.setdefault("release", {}).setdefault("repos", {}).update({repo: "deployed" if ok else "failed"}))
                run.event("ci_cd", "deployed" if ok else "deploy_failed", {"repo": repo})
                say(who, f"{repo}: {'deployed' if ok else 'deploy FAILED'}" + ("" if ok else "\n" + (r.stdout + r.stderr)[-600:]))
                continue
        msg = "waiting for the review" if res["status"] != "Deploying" else ""
        if msg and msg != said:
            say(who, msg)
            said = msg
        time.sleep(poll)


def auto_human(args):
    """Plays the human at every gate. Testing only."""
    run = W.Run(args.ticket, args.run)
    poll = float(load_cfg("runner.yaml").get("poll_seconds", 5))
    who, by = "auto-human", "auto-approve (test)"
    while True:
        res = S.analyze(args.ticket, args.run)
        if res["status"] in ("Done", "Escalated"):
            return
        for gate, g in (res.get("gates") or {}).items():
            if g["state"] == "awaiting_approval":
                say(who, f"approving the {gate} gate")
                do_approve(run, gate, by)
        if res["status"] == "Awaiting acceptance":
            say(who, "accepting the ticket")
            do_accept(run, by)
        time.sleep(poll)


# ---------------------------------------------------------------- human commands

def release_from(run, artifact):
    """The repos and the Release section (deploy_order, sequence) written in an artifact such as tasks.md."""
    p = run.path_of(artifact)
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    order = re.search(r"deploy_order:\s*(\w+)", text)
    seq = re.search(r"sequence:\s*([\w\-, ]+)", text)
    sequence = [s.strip() for s in seq.group(1).split(",") if s.strip()] if seq else []
    reg = list((S.load_yaml(ROOT / "config" / "repos.yaml").get("repos") or {})) if (ROOT / "config" / "repos.yaml").exists() else []
    repos = [r for r in reg if re.search(r"(?<![\w-])" + re.escape(r) + r"(?![\w-])", text)]
    return (order.group(1) if order else "independent"), sequence, repos


def gate_of(run, gate):
    """(artifacts, kind, gate definition) of the gate on agent `gate` for this run's kind."""
    cfg = S.config()
    kind = str(run.load().get("kind") or "story")
    if gate not in cfg.kind_agents(kind) or not cfg.agent(kind, gate)["gate"]:
        with_gates = [l for l in cfg.kind_agents(kind) if cfg.agent(kind, l)["gate"]]
        sys.exit(f"error: no gate on {gate!r} for a {kind} (gates: {', '.join(with_gates) or 'none'})")
    g = cfg.agent(kind, gate)["gate"]
    return list(g["artifacts"]), kind, g


def gate_keys(run, gate):
    keys, kind, _ = gate_of(run, gate)
    return keys, kind


def ticket_context_problems(text, kind):
    """What makes a ticket not ready for the agents. The ticket is the spec, so it must carry its criteria and say which repos it touches."""
    if kind not in ("story", "chore"):
        return []
    problems = []
    rows = re.findall(r"^\|\s*[JC]-\d+\s*\|\s*(.*?)\s*\|?\s*$", text, re.M)
    if not [r for r in rows if r and r.lower() not in ("none provided", "none", "n/a")]:
        problems.append("it has no acceptance criteria: the table needs at least one row with an id (J-1, or C-1 for a chore) and the criterion, given / when / then")
    m = re.search(r"^[-*]\s*Affected repos[^:\n]*:\s*(.*)$", text, re.M | re.I)
    if not m or m.group(1).strip().lower() in ("", "none", "none provided", "n/a"):
        problems.append("it names no affected repo (the '- Affected repos:' line)")
    return problems


def record_clarification(run, agent, repo, answer, by):
    """Write a person's answer to an agent's question into ticket-context.md (section '## Clarifications'), so every agent
    reads it, and mark the context ready again. Only used for the first agent of the kind: nothing is built from the
    ticket yet, so no work goes stale."""
    path = run.path_of("ticket-context.md")
    if not path.is_file() or not (answer or "").strip():
        return False
    asked = ""
    for e in run.events():
        d = e["detail"]
        if e["event"] == "question_raised" and agent in (d.get("agent"), e["actor"], e["actor"].removeprefix("worker:")) and (d.get("repo") or "") == (repo or ""):
            asked = str(d.get("question") or d.get("text") or "").strip()
    flat = lambda t: " ".join(str(t).split())
    line = f"- {agent} asked: {flat(asked) or '(see events.log)'}\n  Answer ({by}): {flat(answer)}\n"
    content = path.read_text(encoding="utf-8")
    if re.search(r"(?m)^## Clarifications\s*$", content):
        m = re.search(r"(?m)^## Clarifications\s*$", content)
        nxt = re.search(r"(?m)^## ", content[m.end():])
        pos = m.end() + nxt.start() if nxt else len(content)
        updated = content[:pos].rstrip("\n") + "\n" + line + ("\n" + content[pos:] if nxt else "")
    else:
        updated = content.rstrip() + "\n\n## Clarifications\n\nAnswers from the ticket owner to questions the agents raised. They are part of the ticket.\n\n" + line
    path.write_text(updated, encoding="utf-8", newline="\n")
    run.mark("ticket-context.md", "ready")
    run.event(by, "artifact_ready", {"artifact": "ticket-context.md", "source": "clarification"})
    return True


def update_ticket_context_feedback(run, reason, artifact="ticket-context.md"):
    path = run.path_of(artifact)
    if not path.is_file():
        raise RuntimeError(f"Cannot write the rejection feedback: {path} is missing.")
    content = path.read_text(encoding="utf-8")
    heading = "## Plan rejection feedback"
    feedback = (
        f"{heading}\n\n"
        "The plan was rejected. Update this context to incorporate the feedback below, "
        "then regenerate the plan artifacts.\n\n"
        + "\n".join(f"> {line}" if line else ">" for line in reason.splitlines())
        + "\n"
    )
    match = re.search(r"(?m)^## Plan rejection feedback\s*$", content)
    if match:
        next_heading = re.search(r"(?m)^## (?!Plan rejection feedback\s*$)", content[match.end():])
        end = match.end() + next_heading.start() if next_heading else len(content)
        updated = content[:match.start()] + feedback + content[end:]
    else:
        updated = content.rstrip() + "\n\n" + feedback
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temp_path = Path(temporary.name)
            temporary.write(updated)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def do_approve(run, gate, by):
    keys, kind, gdef = gate_of(run, gate)
    res = S.analyze(run.ticket, run.run)
    g = (res.get("gates") or {}).get(gate)
    if not g or g["state"] != "awaiting_approval":
        sys.exit(f"error: the {gate} gate is not waiting for approval (it is {g['state'] if g else 'not applicable'}). "
                 f"Ticket status: {res['status']} - {res['detail']}")
    for k in keys:
        run.mark(k, "approved", approved_by=by)
    run.event(by, "gate_approved", {"gate": gate, "artifacts": keys})
    src = gdef.get("sets_repos_from")
    if src:
        order, seq, repos = release_from(run, src)
        fan = S.config().fan_out_agents(kind)
        def fn(m):
            m["repos"] = repos
            m.setdefault("release", {}).update(deploy_order=order, sequence=seq)
            for l in fan:
                m.setdefault("agents", {}).setdefault(l, {})["repos"] = {r: "waiting" for r in repos}
        run.update(fn)
        run.event("orchestrator", "manifest_updated", {"repos": repos, "deploy_order": order, "sequence": seq})
        run.decide(f"{by} approved {', '.join(keys)} together. Repos {repos} and Release ({order}: {', '.join(seq) or '-'}) copied from {src}.")
        print(f"approved. Repos from {src}: {', '.join(repos) or '(none found: check ' + src + ' and config/repos.yaml)'}; deploy_order: {order}")
    else:
        run.decide(f"{by} approved {', '.join(keys)}.")
        print(f"approved {', '.join(keys)}")



def do_accept(run, by):
    run.update(lambda m: m.setdefault("acceptance", {}).update(ticket_accepted=True, signed_off_by=by))
    run.event(by, "ticket_accepted", {})
    run.decide(f"{by} accepted the ticket.")
    print("ticket accepted")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["start", "worker", "command", "ready", "approve", "reject", "answer", "retry", "accept", "deployed", "status"])
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--run", type=int, default=None)
    ap.add_argument("--agent", "--lane", dest="agent", default="")
    ap.add_argument("--gate", default="", help="the agent whose gate to approve or reject (default: the first gate waiting)")
    ap.add_argument("--artifact", default="ticket-context.md")
    ap.add_argument("--repo", default="")
    ap.add_argument("--by", default=os.environ.get("USERNAME") or os.environ.get("USER") or "human")
    ap.add_argument("--reason", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--restart", action="store_true", help="restart workflow workers after rejecting the planner gate")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--auto-approve", action="store_true", help="play the human at every gate (testing only)")
    ap.add_argument("--verbose", action="store_true", help="show the agent's output as it runs")
    args = ap.parse_args()
    if args.run is None:
        tdir = ROOT / "tickets" / args.ticket
        args.run = S.latest_run(tdir) if tdir.is_dir() else None
        if args.run is None:
            sys.exit(f"error: no run found for {args.ticket}")

    if args.command == "status":
        sys.exit(subprocess.call([PY, str(HERE / "wf_status.py"), args.ticket, "--run", str(args.run)]))
    if args.command == "worker":
        if not args.agent:
            sys.exit("error: --agent is required")
        try:
            return ci_worker(args) if args.agent == "ci" else auto_human(args) if args.agent == "auto-human" else agent_worker(args)
        except KeyboardInterrupt:
            return
    run = W.Run(args.ticket, args.run)
    if args.command == "command":
        agent = args.agent or S.config().kind_agents(str(run.load().get("kind") or "story"))[0]
        cmd = agent_command(load_cfg("runner.yaml"), run, agent, args.repo, run.attempts_done(agent, args.repo) + 1)
        print(json.dumps(cmd, indent=2, ensure_ascii=False))
        return
    if args.command == "start":
        return start(args, run)
    if args.command == "ready":
        p = run.path_of(args.artifact)
        if not p.exists():
            sys.exit(f"error: {p} not found")
        if PLACEHOLDER.search(p.read_text(encoding="utf-8")) and not args.force:
            sys.exit(f"error: {args.artifact} still has template placeholders such as <paste ...>. Fill them in, or use --force.")
        if args.artifact == "ticket-context.md" and not args.force:
            problems = ticket_context_problems(p.read_text(encoding="utf-8"), str(run.load().get("kind") or "story"))
            if problems:
                sys.exit("error: the ticket is not ready for the agents: " + "; ".join(problems)
                         + ". Send it back to refinement, or fix ticket-context.md (or use --force).")
        run.mark(args.artifact, "ready")
        run.event(args.by, "artifact_ready", {"artifact": args.artifact})
        print(f"{args.artifact} marked ready")
    elif args.command in ("approve", "reject") and not args.gate:
        waiting = [g for g, x in (S.analyze(args.ticket, args.run).get("gates") or {}).items() if x["state"] == "awaiting_approval"]
        if not waiting:
            sys.exit("error: no gate is waiting for a decision")
        args.gate = waiting[0]
        return main_with(args, run)
    else:
        return main_with(args, run)


def main_with(args, run):
    if args.command == "approve":
        do_approve(run, args.gate, args.by)
    elif args.command == "reject":
        res = S.analyze(args.ticket, args.run)
        keys, kind, gdef = gate_of(run, args.gate)
        feedback_to = gdef.get("feedback_to")
        if args.restart and not feedback_to:
            sys.exit("error: --restart is only supported for a gate with feedback_to")
        gate_state = (res.get("gates") or {}).get(args.gate, {}).get("state")
        if gate_state != "awaiting_approval":
            sys.exit(f"error: the {args.gate} gate is not waiting for a decision (it is {gate_state or 'not applicable'}).")
        if feedback_to:
            if not args.reason.strip():
                sys.exit(f"error: a rejection reason is required to update {feedback_to}")
            run.set_agent(args.gate, "blocked")
            try:
                update_ticket_context_feedback(run, args.reason.strip(), feedback_to)
                run.mark(feedback_to, "ready")
                run.event(args.by, "artifact_ready", {"artifact": feedback_to, "source": "rejection_feedback"})
                for key in keys:
                    run.mark(key, "rejected")
                run.event(args.by, "gate_rejected", {"gate": args.gate, "artifacts": keys, "reason": args.reason.strip()})
                run.decide(f"{args.by} rejected {', '.join(keys)}: {args.reason.strip()}")
            finally:
                run.set_agent(args.gate, "waiting")
            print(f"{feedback_to} updated from the rejection feedback; {', '.join(keys)} rejected for rework")
            if args.restart:
                print("Restarting workflow workers...")
                return start(args, run)
        else:
            for k in keys:
                run.mark(k, "rejected")
            run.event(args.by, "gate_rejected", {"gate": args.gate, "artifacts": keys, "reason": args.reason})
            run.decide(f"{args.by} rejected {', '.join(keys)}: {args.reason or 'no reason given'}")
            print(f"rejected {', '.join(keys)}; the agent will start again")
    elif args.command == "answer":
        if not args.agent:
            sys.exit("error: --agent is required")
        run.set_agent(args.agent, "waiting", args.repo or None)
        run.event(args.by, "question_answered", {"agent": args.agent, "repo": args.repo, "text": args.text})
        first = S.config().kind_agents(str(run.load().get("kind") or "story"))[:1]
        if args.agent in first and record_clarification(run, args.agent, args.repo, args.text, args.by):
            print("the answer is also written into ticket-context.md (## Clarifications)")
        run.decide(f"{args.by} answered the {args.agent} question: {args.text or '(see events.log)'}")
        print(f"{args.agent} unblocked")
    elif args.command == "retry":
        if not args.agent:
            sys.exit("error: --agent is required")
        def fn(m):
            ent = m.setdefault("agents", {}).setdefault(args.agent, {})
            if isinstance(ent.get("repos"), dict):
                for r in list(ent.get("repos") or {}):
                    if not args.repo or r == args.repo:
                        ent["repos"][r] = "waiting"
                ent["status"] = "waiting"
            else:
                ent["status"] = "waiting"
        run.update(fn)
        run.event(args.by, "attempts_reset", {"agent": args.agent, "repo": args.repo})
        run.decide(f"{args.by} gave {args.agent}{' ' + args.repo if args.repo else ''} a fresh attempt budget.")
        print(f"{args.agent}{' ' + args.repo if args.repo else ''} will run again; start the workers: python scripts/wf_run.py start --ticket {args.ticket}")
    elif args.command == "accept":
        do_accept(run, args.by)
    elif args.command == "deployed":
        if not args.repo:
            sys.exit("error: --repo is required")
        rel = run.load().get("release") or {}
        seq = [str(x) for x in rel.get("sequence") or []]
        if rel.get("deploy_order") == "ordered" and args.repo in seq:
            for earlier in seq[:seq.index(args.repo)]:
                if (rel.get("repos") or {}).get(earlier) != "deployed":
                    sys.exit(f"error: release order says {earlier} must be deployed before {args.repo}")
        run.update(lambda m: m.setdefault("release", {}).setdefault("repos", {}).update({args.repo: "deployed"}))
        run.event("ci_cd", "deployed", {"repo": args.repo, "recorded_by": args.by})
        print(f"{args.repo} recorded as deployed")


def start(args, run):
    res = S.analyze(args.ticket, args.run)
    kind = res["kind"]
    agents = [l for l, r in res["agents"].items() if r["state"] != "disabled"]
    if S.config().deploys(kind):
        agents.append("ci")
    if args.auto_approve:
        agents.append("auto-human")
    if "--agent" in (load_cfg("runner.yaml").get("agent") or {}).get("command", []) and not os.environ.get("WF_AGENT_CMD"):
        missing = [l for l in agents if l != "ci" and l != "auto-human" and not (ROOT / ".github" / "agents" / f"{agent_instructions(l)}.agent.md").exists()]
        if missing:
            print("warning: no agent file for: " + ", ".join(missing) + " (expected .github/agents/wf-<name>.agent.md)\n")
    print(f"Starting run {args.ticket}/run-{args.run} ({kind}). Workers: {', '.join(agents)}")
    print("Each worker acts on its own when its agent is ready. Ctrl+C stops them all.\n")
    procs = []
    for agent in agents:
        cmd = [PY, str(HERE / "wf_run.py"), "worker", "--ticket", args.ticket, "--run", str(args.run), "--agent", agent]
        if args.verbose:
            cmd.append("--verbose")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", env=env)
        procs.append(p)
        threading.Thread(target=lambda p=p: [print(l.rstrip(), flush=True) for l in p.stdout], daemon=True).start()
    asked = None
    try:
        while any(p.poll() is None for p in procs):
            cur = S.analyze(args.ticket, args.run)
            humans = [a["text"] for a in cur["next"] if a["who"].lower().startswith("human")]
            key = (cur["status"], tuple(humans))
            if humans and key != asked and not args.auto_approve:
                print(f"\n>>> Waiting for a person ({cur['status']}):", flush=True)
                for h in humans:
                    print(f"    {h}", flush=True)
                asked = key
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping workers...", flush=True)
        for p in procs:
            if p.poll() is None:
                p.terminate()
        return
    final = S.analyze(args.ticket, args.run)
    print(f"\nRun finished: {final['status']} - {final['detail']}")
    print(f"Details: python scripts/wf_status.py {args.ticket}")
    sys.exit(0 if final["status"] == "Done" else 1)


if __name__ == "__main__":
    main()
