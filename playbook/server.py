#!/usr/bin/env python3
"""Serve a local workflow playbook for viewing and triggering ticket runs."""
import errno
import json
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import wf_status  # noqa: E402
import wf_state  # noqa: E402
import wf_run  # noqa: E402
import wf_api  # noqa: E402
import wf_audit  # noqa: E402

PLAYBOOK = ROOT / "playbook" / "index.html"
TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
import wf_config  # noqa: E402

CONFIG_PAGE = ROOT / "playbook" / "config.html"


def agent_names():
    return wf_config.load().agent_names()


def kind_names():
    return wf_config.load().kinds()
jobs = {}
jobs_lock = threading.Lock()
work_item_lock = threading.Lock()


class PlaybookHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def valid_ticket(value):
    if not isinstance(value, str) or not TICKET_RE.fullmatch(value):
        raise ValueError("Ticket IDs may contain letters, numbers, dots, underscores and hyphens.")
    return value


def analyze(ticket, run):
    valid_ticket(ticket)
    if run is not None and (not isinstance(run, int) or isinstance(run, bool) or run < 1):
        raise ValueError("Run must be a positive integer.")
    return wf_status.analyze(ticket, run)


def run_log(ticket, run_number):
    """The live output of the whole run: the latest start command if this server started it, else the tails of every agent log."""
    with jobs_lock:
        starts = [j for j in jobs.values() if j.get("ticket") == ticket and j.get("action") == "start"]
        if starts:
            job = starts[-1]
            return {"path": f"{job['label']} · {job['status']}", "text": job["output"] or "(no output yet)"}
    status = analyze(ticket, run_number)
    log_dir = (ROOT / status["path"] / "logs").resolve()
    if not log_dir.is_dir():
        return None
    parts = []
    for path in sorted(log_dir.glob("*.log"), key=lambda q: q.stat().st_mtime_ns):
        if path.is_symlink() or path.resolve().parent != log_dir:
            continue
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size > 12 * 1024:
                stream.seek(-12 * 1024, os.SEEK_END)
            parts.append(f"===== {path.name} =====\n" + stream.read().decode("utf-8", errors="replace"))
    return {"path": "all agent logs", "text": "\n".join(parts)} if parts else None


def latest_agent_log(ticket, run_number, agent):
    if agent not in agent_names():
        raise ValueError("Unsupported workflow agent.")
    status = analyze(ticket, run_number)
    log_dir = (ROOT / status["path"] / "logs").resolve()
    if not log_dir.is_dir():
        return None
    pattern = f"{agent}*-a*.log"
    log_pattern = re.compile(rf"{re.escape(agent)}(?:-[A-Za-z0-9._-]+)?-a(\d+)\.log")
    candidates = []
    for path in log_dir.glob(pattern):
        match = log_pattern.fullmatch(path.name)
        if (
            match
            and path.is_file()
            and not path.is_symlink()
            and path.resolve().parent == log_dir
        ):
            candidates.append((int(match.group(1)), path.stat().st_mtime_ns, path))
    if not candidates:
        return None
    _, _, path = max(candidates, key=lambda item: (item[0], item[1]))
    max_bytes = 96 * 1024
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size > max_bytes:
            stream.seek(-max_bytes, os.SEEK_END)
        content = stream.read().decode("utf-8", errors="replace")
    if size > max_bytes:
        content = "[Showing the end of the log; earlier output omitted.]\n" + content
    return {"path": path.relative_to(log_dir.parent).as_posix(), "text": content}


def artifact_file(ticket, run_number, name):
    status = analyze(ticket, run_number)
    art = (status.get("artifacts") or {}).get(name)
    if not art or art.get("per_repo") or "/" in name or "\\" in name:
        raise ValueError("Unsupported artifact.")
    ticket_dir = (ROOT / "tickets" / ticket).resolve()
    run_dir = (ROOT / status["path"]).resolve()
    try:
        run_dir.relative_to(ticket_dir)
    except ValueError as exc:
        raise ValueError("Artifact path is outside the ticket run.") from exc
    path = wf_status.artifact_path(run_dir, name)
    if path is None:
        return None
    resolved = path.resolve()
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError("Artifact path is outside the ticket run.") from exc
    return resolved if resolved.is_file() else None


def command_for(action):
    ticket = valid_ticket(action.get("ticket"))
    if action.get("action") == "create":
        if (ROOT / "tickets" / "_archive" / ticket).exists():
            raise ValueError(f"{ticket} is archived. Restore it before creating another work item with the same ID.")
        ensure_no_active_jobs(ticket)
        kind = action.get("kind", "story")
        if kind not in kind_names():
            raise ValueError("Unsupported work item kind.")
        return [sys.executable, str(ROOT / "scripts" / "new_ticket.py"), "--ticket", ticket, "--kind", kind]

    res = analyze(ticket, action.get("run"))
    base = [sys.executable, str(ROOT / "scripts" / "wf_run.py")]
    verb = action.get("action")
    args = ["--ticket", ticket, "--run", str(res["run"])]
    if verb == "recheck":
        return [
            sys.executable, str(ROOT / "scripts" / "wf_status.py"),
            ticket, "--run", str(res["run"]),
        ]
    if verb == "ready":
        return base + ["ready"] + args
    if verb == "start":
        context_status = (res.get("artifacts") or {}).get("ticket-context.md", {}).get("status")
        if context_status not in ("ready", "approved"):
            raise ValueError("Fill ticket-context.md and mark it ready before starting the workflow.")
        return base + ["start"] + args
    if verb == "worker":
        agent = action.get("agent")
        if not isinstance(agent, str) or agent not in res["agents"] or res["agents"][agent]["state"] == "disabled":
            raise ValueError("That agent is not enabled for this work item.")
        return base + ["worker"] + args + ["--agent", agent]
    if verb == "approve":
        gate = action.get("gate")
        if (not isinstance(gate, str)
                or (res.get("gates") or {}).get(gate, {}).get("state") != "awaiting_approval"):
            raise ValueError("Unsupported approval gate.")
        return base + ["approve"] + args + ["--gate", gate]
    if verb == "reject":
        gate, reason = action.get("gate"), action.get("reason")
        if (not isinstance(gate, str)
                or (res.get("gates") or {}).get(gate, {}).get("state") != "awaiting_approval"):
            raise ValueError("That gate is not waiting for a human decision.")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 1000:
            raise ValueError("A rejection reason is required (up to 1,000 characters).")
        command = base + ["reject"] + args + ["--gate", gate, "--reason", reason.strip()]
        if (wf_config.load().agent(res["kind"], gate)["gate"] or {}).get("feedback_to") and not is_workflow_running(ticket):
            command.append("--restart")
        return command
    if verb == "answer":
        agent, raw_text = action.get("agent"), action.get("text", "")
        text = raw_text.strip() if isinstance(raw_text, str) else None
        if (not isinstance(agent, str) or agent not in res["agents"] or not isinstance(text, str)
                or not text or len(text) > 5000):
            raise ValueError("Choose a valid agent and enter an answer (up to 5,000 characters).")
        return base + ["answer"] + args + ["--agent", agent, "--text", text]
    if verb == "deployed":
        repo = action.get("repo")
        repos = res.get("repos") or []
        if (res["status"] != "Deploying" or not isinstance(repo, str) or repo not in repos
                or (res.get("release") or {}).get("repos", {}).get(repo) == "deployed"):
            raise ValueError("That repository is not part of this run.")
        release = res.get("release") or {}
        if release.get("deploy_order") == "ordered":
            sequence = release.get("sequence") or list(repos)
            next_repo = next(
                (name for name in sequence if release.get("repos", {}).get(name) != "deployed"),
                None,
            )
            if repo != next_repo:
                raise ValueError(f"Deployment order requires confirming {next_repo or 'the remaining release'} next.")
        return base + ["deployed"] + args + ["--repo", repo]
    if verb == "accept":
        if res["status"] != "Awaiting acceptance":
            raise ValueError("This work item is not ready for acceptance.")
        return base + ["accept"] + args
    raise ValueError("Unsupported workflow action.")


def ensure_no_active_jobs(ticket):
    with jobs_lock:
        if any(
            job.get("ticket", "").casefold() == ticket.casefold()
            and job["status"] in ("running", "cancelling")
            for job in jobs.values()
        ):
            raise ValueError(f"{ticket} has a workflow command running. Wait for it to finish before removing or restoring it.")


def archived_work_items():
    archive = ROOT / "tickets" / "_archive"
    if not archive.is_dir():
        return []
    return sorted(
        (folder.name for folder in archive.iterdir()
         if folder.is_dir() and TICKET_RE.fullmatch(folder.name)),
        key=str.lower,
    )


def is_job_running(ticket):
    with jobs_lock:
        return any(
            job.get("ticket", "").casefold() == ticket.casefold()
            and job["status"] in ("running", "cancelling")
            for job in jobs.values()
        )


def is_workflow_running(ticket):
    with jobs_lock:
        return any(
            job.get("ticket", "").casefold() == ticket.casefold()
            and (job.get("action") == "start" or job.get("restarts_workflow"))
            and job["status"] in ("running", "cancelling")
            for job in jobs.values()
        )


def current_user():
    """Who runs the playbook: WF_USER, else the operating system user. The Jira board shows their tickets."""
    return os.environ.get("WF_USER") or os.environ.get("USERNAME") or os.environ.get("USER") or ""


def backlog_items():
    result = []
    me = current_user().casefold()
    for item in wf_api.backlog():
        ticket = valid_ticket(item.get("id"))
        status = None
        try:
            status = analyze(ticket, None)
            state, detail = status["status"], status["detail"]
        except wf_status.StatusError:
            state, detail = "Not started", ""
        result.append({
            "ticket": ticket,
            "title": str(item.get("title") or ticket),
            "description": str(item.get("description") or ""),
            "repos": [str(repo) for repo in item.get("repos", [])],
            "assignee": str(item.get("assignee") or ""),
            "mine": bool(me) and str(item.get("assignee") or "").casefold() == me,
            "kind": "story" if item.get("prd") else "chore",
            "state": state,
            "detail": detail,
            "has_run": status is not None,
            "archived": (ROOT / "tickets" / "_archive" / ticket).is_dir(),
            "running": is_job_running(ticket),
        })
    return result


def start_backlog_workflow(ticket):
    ticket = valid_ticket(ticket)
    if not any(item.get("id") == ticket for item in wf_api.backlog()):
        raise ValueError(f"{ticket} is not in config/backlog.yaml.")
    if (ROOT / "tickets" / "_archive" / ticket).exists():
        raise ValueError(f"{ticket} is archived. Restore it before starting.")
    ensure_no_active_jobs(ticket)
    intake = wf_api.intake(ticket)
    command = [
        sys.executable, str(ROOT / "scripts" / "wf_run.py"),
        "start", "--ticket", ticket, "--run", str(intake["run"]), "--verbose",
    ]
    return start_job(command, f"Start workflow · {ticket}", ticket, action="start")


def move_work_item(ticket, restore=False):
    ticket = valid_ticket(ticket)
    with work_item_lock:
        ensure_no_active_jobs(ticket)
        tickets = ROOT / "tickets"
        archive = tickets / "_archive"
        source = archive / ticket if restore else tickets / ticket
        destination = tickets / ticket if restore else archive / ticket
        if not source.is_dir():
            state = "archived" if restore else "active"
            raise ValueError(f"Work item {ticket} is not {state}.")
        if destination.exists():
            state = "active" if restore else "archived"
            raise ValueError(f"A {state} work item with ID {ticket} already exists.")
        if not restore:
            archive.mkdir(exist_ok=True)
        source.rename(destination)
    return ticket


def confirm_human_action(action):
    ticket = valid_ticket(action.get("ticket"))
    run_number = action.get("run")
    if not isinstance(run_number, int) or isinstance(run_number, bool) or run_number < 1:
        raise ValueError("Run must be a positive integer.")
    text = action.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 5000:
        raise ValueError("A valid human recommendation is required.")
    recommendation = text.strip()
    result = analyze(ticket, run_number)
    if not any(item["who"].lower().startswith("human") and item["text"] == recommendation
               for item in result["next"]):
        raise ValueError("That human action is no longer recommended. Refresh the workflow status.")
    run = wf_state.Run(ticket, run_number)
    run.event("human", "human_action_confirmed", {"action": recommendation})
    run.decide(f"Human confirmed completion: {recommendation}")
    return {"confirmed": True, "ticket": ticket, "run": run_number}


def start_job(command, label, ticket, action="", restarts_workflow=False):
    process = subprocess.Popen(
        command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
        **({"start_new_session": True} if os.name != "nt" else {}),
    )
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id, "label": label, "ticket": ticket, "action": action,
        "restarts_workflow": restarts_workflow,
        "status": "running", "output": "",
        "returncode": None, "process": process, "cancel_requested": False,
    }
    with jobs_lock:
        jobs[job_id] = job

    def collect():
        try:
            for line in process.stdout:
                with jobs_lock:
                    job["output"] = (job["output"] + line)[-24000:]
            code = process.wait()
            with jobs_lock:
                job["returncode"] = code
                if job["cancel_requested"]:
                    job["status"] = "cancelled"
                else:
                    job["status"] = "succeeded" if code == 0 else "failed"
        except OSError as exc:
            with jobs_lock:
                job["status"] = "failed"
                job["output"] += f"\nUnable to collect command output: {exc}"

    threading.Thread(target=collect, daemon=True).start()
    return {"job": job_id, "label": label, "restarts_workflow": restarts_workflow}


# ---------------------------------------------------------------- configuration: agents and artifacts

AGENT_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
AUDITOR_FIELDS = ("instructions", "model", "poll_seconds", "audit_when", "auto_start")
AUDIT_STATUSES = ("Done", "Escalated")
AUDITOR_PATH = ROOT / "config" / "auditor.yaml"


def auditor_problems(a):
    out = []
    if not isinstance(a, dict):
        return ["auditor settings must be an object"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", str(a.get("instructions") or "")):
        out.append("auditor: choose an agent")
    elif not (ROOT / ".github" / "agents" / f"{a['instructions']}.agent.md").is_file():
        out.append(f"auditor: instructions file .github/agents/{a['instructions']}.agent.md not found")
    if a.get("model") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", str(a["model"])):
        out.append("auditor: the model name has characters that are not allowed")
    try:
        if int(a.get("poll_seconds", 30)) < 5:
            out.append("auditor: check at most every 5 seconds")
    except (TypeError, ValueError):
        out.append("auditor: the check interval must be a number of seconds")
    when = a.get("audit_when") or []
    if not isinstance(when, list) or not when or any(s not in AUDIT_STATUSES for s in when):
        out.append(f"auditor: audit tickets that are one of {', '.join(AUDIT_STATUSES)}")
    if not isinstance(a.get("auto_start", False), bool):
        out.append("auditor: auto start must be true or false")
    return out


def dump_yaml(data):
    """YAML with multi-line text (prompts) kept as readable | blocks."""
    import yaml

    class Dumper(yaml.SafeDumper):
        pass

    def text(dumper, value):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)

    Dumper.add_representer(str, text)
    return yaml.dump(data, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=120)


def save_auditor(a):
    import yaml
    text = AUDITOR_PATH.read_text(encoding="utf-8") if AUDITOR_PATH.exists() else ""
    raw = (yaml.safe_load(text) or {}) if text else {}
    raw.pop("prompt", None)   # the task message is fixed in scripts/wf_audit.py
    raw.pop("agent", None)
    new = {**raw, "instructions": str(a["instructions"]), "model": str(a.get("model") or ""), "poll_seconds": int(a.get("poll_seconds", 30)),
           "audit_when": list(a["audit_when"]), "auto_start": bool(a.get("auto_start"))}
    if new == raw:
        return False
    lines = text.splitlines()
    head = "".join(l + "\n" for l in lines[:next((i for i, l in enumerate(lines) if not l.startswith("#")), len(lines))])   # keep the top comment
    tmp = AUDITOR_PATH.with_suffix(".yaml.tmp")
    tmp.write_text(head + dump_yaml(new), encoding="utf-8", newline="\n")
    os.replace(tmp, AUDITOR_PATH)
    return True
RESERVED_AGENTS = {"ci", "auto-human", "auditor"}
EDITABLE = ("agents", "kinds", "artifact_inputs", "events")


def config_view():
    path = wf_config.PATH
    raw = wf_config.load().raw
    cfg = wf_config.Config(raw)
    available = sorted(p.name[:-len(".agent.md")] for p in (ROOT / ".github" / "agents").glob("*.agent.md")) \
        if (ROOT / ".github" / "agents").is_dir() else []
    return {key: raw.get(key) or {} for key in EDITABLE} | {
        "summary": cfg.summary(), "available_instructions": available, "path": path.relative_to(ROOT).as_posix(),
        "auditor": {k: wf_audit.config()[k] for k in AUDITOR_FIELDS}, "audit_statuses": list(AUDIT_STATUSES),
        "system_artifacts": cfg.system_artifacts()}


def candidate_config(body):
    if not isinstance(body, dict):
        raise ValueError("The configuration must be a JSON object.")
    raw = dict(wf_config.load().raw)
    extra = auditor_problems(body["auditor"]) if "auditor" in body else []
    for key in EDITABLE:
        if key in body:
            if not isinstance(body[key], dict):
                raise ValueError(f"{key} must be an object.")
            raw[key] = body[key]
    problems = []
    for name in (raw.get("agents") or {}):
        if not AGENT_RE.fullmatch(str(name)):
            problems.append(f"agent name {name!r}: use lowercase letters, digits and hyphens (start with a letter)")
        if name in RESERVED_AGENTS:
            problems.append(f"agent name {name!r} is reserved")
        entry = (raw.get("agents") or {}).get(name)
        instr = (entry.get("instructions") or entry.get("agent")) if isinstance(entry, dict) else None
        if instr and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", str(instr)):
            problems.append(f"{name}: instructions {instr!r} is not a valid agent file name")
    return raw, problems + extra + wf_config.Config(raw).problems()


AGENT_TEMPLATE = """---
name: {instructions}
description: {title} agent. {purpose}
tools: [read, search, edit]
---

You are one agent of an AI-native delivery workflow. This file is your role: the team edits it to change how the agent works.

Start:
- The task message gives `root` (the workflow folder) and `run_dir` (this work item's run folder). `skills/` and `config/` are under `root`. Artifacts are under `run_dir/artifacts/`, and `events.log` is in `run_dir`.
- Read `skills/_common.md` first (artifact header, events). Then follow this file.
- If you are blocked and need something from a person, ask ONE short message that lists everything you need, append `question_raised` to `events.log`, and stop. The person's answer arrives in this same session; then continue.
- Stop when your artifacts are written.
- If the task message says it is a chat with a person, follow "Ad-hoc chat" in `skills/_common.md` instead of the steps below. What you need from the person is `ask_for` for your agent in `config/agents.yaml`.

**Placeholder agent.** Today this agent does NOT do the real task. It reads its inputs, writes its output artifacts so the
next agent can start, and says in one line what the real task would be. Use no shell, change no repo, run no tests.

---

# {title} agent

Requires {requires}. Produces {produces}.{fan_out}

## Placeholder steps
1. Read {reads}.
2. Write {produces}, each with the artifact header from `skills/_common.md`, and one line saying what the real work would be.

## TEAM: replace this with the real task

Real task: (describe it here). Keep the output artifacts, because the next agents and the orchestrator read them.
"""


def ensure_agent_files(raw):
    """A placeholder agent file for each agent that has none, so a new agent can run straight away."""
    created = []
    cfg = wf_config.Config(raw)
    for name in cfg.agent_names():
        agent = cfg.agent("", name)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", agent["instructions"]):
            continue
        path = ROOT / ".github" / "agents" / f"{agent['instructions']}.agent.md"
        if path.exists():
            continue
        fmt = lambda items: ", ".join(f"`{x}`" for x in items) or "nothing"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(AGENT_TEMPLATE.format(
            instructions=agent["instructions"], title=agent["title"], purpose=agent["purpose"] or "",
            requires=fmt([f"{k}@{lv}" if lv else k for k, lv in agent["requires"]]), reads=fmt([k for k, _ in agent["requires"]]),
            produces=fmt([f"{k}/<repo>/" if k in agent["per_repo"] else k for k in agent["produces"]]),
            fan_out=" One instance per repo; the task message names yours." if agent["fan_out"] else ""), encoding="utf-8", newline="\n")
        created.append(path.relative_to(ROOT).as_posix())
    return created


def save_config(body):
    raw, problems = candidate_config(body)
    if problems:
        return {"saved": False, "problems": problems}
    auditor_saved = save_auditor(body["auditor"]) if "auditor" in body else False
    path = wf_config.PATH
    src = wf_config.config_path()
    text = src.read_text(encoding="utf-8") if src.exists() else ""
    head = text[:text.index("version:")] if "version:" in text else ""
    if text:
        (path.parent / "agents.yaml.bak").write_text(text, encoding="utf-8", newline="\n")
    import yaml
    body_text = dump_yaml(raw)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(head + body_text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return {"saved": True, "problems": [], "created_files": ensure_agent_files(raw), "backup": "config/agents.yaml.bak",
            "auditor_saved": auditor_saved}


# ---------------------------------------------------------------- the Auditor: one watcher for all tickets

auditor = {"job": None}


def auditor_running():
    with jobs_lock:
        job = jobs.get(auditor["job"])
        return bool(job and job["status"] in ("running", "cancelling"))


def start_auditor():
    if auditor_running():
        raise ValueError("The Auditor is already watching.")
    result = start_job([sys.executable, str(ROOT / "scripts" / "wf_audit.py"), "watch", "--verbose"], "Auditor", "", action="auditor")
    auditor["job"] = result["job"]
    return result


def auditor_view():
    with jobs_lock:
        job = jobs.get(auditor["job"])
        output = job["output"][-6000:] if job else ""
    items = wf_audit.status()
    live = next((item for item in items if item["state"] == "auditing"), None)
    live_text = ""
    if live and live.get("log"):
        try:
            live_text = (ROOT / live["log"]).read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
    cfg = wf_audit.config()
    return {"running": auditor_running(), "job": auditor["job"], "output": output,
            "auto_start": bool(cfg.get("auto_start")), "audit_when": cfg["audit_when"], "instructions": cfg["instructions"],
            "auditing": live["ticket"] if live else "", "live": live_text,
            "tickets": [item for item in items if item["state"] != "not_done"],
            "not_done": sum(1 for item in items if item["state"] == "not_done")}


def audit_report(ticket, run_number):
    ticket = valid_ticket(ticket)
    if not isinstance(run_number, int) or run_number < 1:
        raise ValueError("Run must be a positive integer.")
    path = wf_audit.report_path(ticket, run_number).resolve()
    if wf_audit.AUDITS.resolve() not in path.parents or not path.is_file():
        return None
    return path


def public_job(job):
    return {key: value for key, value in job.items() if key not in ("process", "cancel_requested")}


def cancel_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise KeyError("Job not found.")
        if job["status"] != "running" or job["process"].poll() is not None:
            raise ValueError("This command has already finished.")
        job["status"] = "cancelling"
        job["cancel_requested"] = True
        process = job["process"]

    try:
        if os.name == "nt":
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            if not taskkill.is_file():
                raise OSError(f"Could not find process-tree terminator: {taskkill}")
            result = subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if result.returncode and process.poll() is None:
                raise OSError(result.stderr.strip() or result.stdout.strip() or "taskkill failed")
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        return public_job(job)
    except (OSError, subprocess.SubprocessError) as exc:
        with jobs_lock:
            if process.poll() is None:
                job["status"] = "running"
                job["cancel_requested"] = False
        raise RuntimeError(f"Could not cancel command: {exc}") from exc


class PlaybookHandler(BaseHTTPRequestHandler):
    server_version = "WorkflowPlaybook/1.0"

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            try:
                body = PLAYBOOK.read_bytes()
            except OSError as exc:
                self.send_json({"error": f"Could not read the playbook page: {exc}"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/tickets":
            result = []
            base = ROOT / "tickets"
            for folder in sorted(base.iterdir(), key=lambda p: p.name.lower()) if base.is_dir() else []:
                if not folder.is_dir() or folder.name.startswith("_"):
                    continue
                try:
                    result.append(analyze(folder.name, None))
                except (wf_status.StatusError, OSError, ValueError) as exc:
                    result.append({"ticket": folder.name, "status": "Error", "detail": str(exc), "warnings": []})
            self.send_json({"tickets": result})
            return
        if parsed.path == "/config":
            try:
                body = CONFIG_PAGE.read_bytes()
            except OSError as exc:
                self.send_json({"error": f"Could not read the configuration page: {exc}"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/config":
            try:
                self.send_json(config_view())
            except (OSError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/api/auditor":
            try:
                self.send_json(auditor_view())
            except (OSError, ValueError, wf_status.StatusError) as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/api/audit":
            query = parse_qs(parsed.query)
            try:
                path = audit_report(query.get("ticket", [""])[0], int(query.get("run", ["0"])[0] or 0))
                if path is None:
                    self.send_json({"error": "No audit report for this run yet."}, 404)
                    return
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, OSError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/backlog":
            self.send_json({"tickets": backlog_items(), "user": current_user()})
            return
        if parsed.path == "/api/archived":
            self.send_json({"tickets": archived_work_items()})
            return
        if parsed.path == "/api/status":
            query = parse_qs(parsed.query)
            try:
                ticket = valid_ticket(query.get("ticket", [""])[0])
                raw_run = query.get("run", [None])[0]
                run = int(raw_run) if raw_run else None
                self.send_json({"status": analyze(ticket, run)})
            except (ValueError, wf_status.StatusError, OSError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/log":
            query = parse_qs(parsed.query)
            try:
                ticket = valid_ticket(query.get("ticket", [""])[0])
                raw_run = query.get("run", [""])[0]
                run_number = int(raw_run) if raw_run else None
                agent = query.get("agent", [""])[0]
                result = latest_agent_log(ticket, run_number, agent)
                if result is None:
                    self.send_json({"error": "No log is available for this agent yet."}, 404)
                else:
                    self.send_json(result)
            except (ValueError, wf_status.StatusError, OSError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/runlog":
            query = parse_qs(parsed.query)
            try:
                ticket = valid_ticket(query.get("ticket", [""])[0])
                raw_run = query.get("run", [""])[0]
                result = run_log(ticket, int(raw_run) if raw_run else None)
                if result is None:
                    self.send_json({"path": "", "text": "No output yet."})
                else:
                    self.send_json(result)
            except (ValueError, wf_status.StatusError, OSError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/artifact":
            query = parse_qs(parsed.query)
            try:
                ticket = valid_ticket(query.get("ticket", [""])[0])
                raw_run = query.get("run", [""])[0]
                run_number = int(raw_run) if raw_run else None
                name = query.get("name", [""])[0]
                path = artifact_file(ticket, run_number, name)
                if path is None:
                    self.send_json({"error": f"{name} is not available in this run."}, 404)
                    return
                body = path.read_bytes()
                if len(body) > 5 * 1024 * 1024:
                    self.send_json({"error": "Artifact is too large to preview."}, 413)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", f'inline; filename="{name}"')
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, wf_status.StatusError, OSError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path[len("/api/jobs/"):]
            if not re.fullmatch(r"[a-f0-9]{32}", job_id):
                self.send_json({"error": "Job not found."}, 404)
                return
            with jobs_lock:
                job = jobs.get(job_id)
                self.send_json({"job": public_job(job)} if job else {"error": "Job not found."}, 200 if job else 404)
            return
        self.send_json({"error": "Not found."}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/action", "/api/archive", "/api/restore", "/api/config", "/api/config/check") and not re.fullmatch(r"/api/jobs/[a-f0-9]{32}/cancel", path):
            self.send_json({"error": "Not found."}, 404)
            return
        try:
            origin = self.headers.get("Origin")
            if origin:
                origin_host = urlparse(origin).hostname
                if origin_host not in ("127.0.0.1", "localhost"):
                    raise ValueError("Actions are accepted only from the local playbook.")
            if path not in ("/api/action", "/api/archive", "/api/restore", "/api/config", "/api/config/check"):
                job_id = path[len("/api/jobs/"):-len("/cancel")]
                try:
                    result = cancel_job(job_id)
                except KeyError as exc:
                    self.send_json({"error": str(exc)}, 404)
                    return
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, 409)
                    return
                except RuntimeError as exc:
                    self.send_json({"error": str(exc)}, 500)
                    return
                self.send_json({"job": result})
                return
            length = int(self.headers.get("Content-Length", "0"))
            limit = 200000 if path.startswith("/api/config") else 16000
            if length < 1 or length > limit:
                raise ValueError(f"Request body must be between 1 and {limit:,} bytes.")
            action = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(action, dict):
                raise ValueError("Action must be a JSON object.")
            if path == "/api/config/check":
                _, problems = candidate_config(action)
                self.send_json({"problems": problems, "summary": wf_config.Config(candidate_config(action)[0]).summary()})
                return
            if path == "/api/config":
                with work_item_lock:
                    result = save_config(action)
                self.send_json(result, 200 if result["saved"] else 422)
                return
            if path in ("/api/archive", "/api/restore"):
                ticket = move_work_item(action.get("ticket"), restore=path == "/api/restore")
                self.send_json({"ticket": ticket})
                return
            if action.get("action") == "start-from-backlog":
                with work_item_lock:
                    result = start_backlog_workflow(action.get("ticket"))
                self.send_json(result, 202)
                return
            if action.get("action") == "stop-workflow":
                ticket = valid_ticket(action.get("ticket"))
                with jobs_lock:
                    running = [j["id"] for j in jobs.values() if j.get("ticket", "").casefold() == ticket.casefold()
                               and (j.get("action") == "start" or j.get("restarts_workflow")) and j["status"] == "running"]
                if not running:
                    raise ValueError(f"No workflow is running for {ticket}.")
                for job_id in running:
                    cancel_job(job_id)
                self.send_json({"stopped": len(running)}, 202)
                return
            if action.get("action") == "auditor-start":
                self.send_json(start_auditor(), 202)
                return
            if action.get("action") == "auditor-stop":
                if not auditor_running():
                    raise ValueError("The Auditor is not running.")
                self.send_json({"job": cancel_job(auditor["job"])["id"]})
                return
            if action.get("action") == "auditor-audit":
                ticket = valid_ticket(action.get("ticket"))
                run_number = action.get("run")
                if not isinstance(run_number, int) or isinstance(run_number, bool) or run_number < 1:
                    raise ValueError("Run must be a positive integer.")
                command = [sys.executable, str(ROOT / "scripts" / "wf_audit.py"), "audit", "--ticket", ticket, "--run", str(run_number)]
                self.send_json(start_job(command, f"Audit · {ticket}", ticket, action="audit"), 202)
                return
            if action.get("action") == "confirm-human":
                with work_item_lock:
                    result = confirm_human_action(action)
                self.send_json(result)
                return
            with work_item_lock:
                command = command_for(action)
                restarts_workflow = "--restart" in command
                label = "Reject plan · restart workflow" if restarts_workflow else str(action.get("action", "workflow action"))
                job_action = "start" if restarts_workflow or action.get("action") == "start" else str(action.get("action", ""))
                result = start_job(
                    command, label, valid_ticket(action.get("ticket")),
                    action=job_action, restarts_workflow=restarts_workflow,
                )
            self.send_json(result, 202)
        except (ValueError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError, wf_status.StatusError, OSError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def log_message(self, fmt, *args):
        print(f"[playbook] {self.address_string()} {fmt % args}", flush=True)


def main():
    port = 8765
    args = [a for a in sys.argv[1:] if a != "--no-auditor"]
    if args:
        try:
            port = int(args[0])
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            sys.exit("usage: python playbook/server.py [port 1-65535] [--no-auditor]")
    try:
        server = PlaybookHTTPServer(("127.0.0.1", port), PlaybookHandler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        sys.exit(f"error: port {port} is already in use; stop the existing server or choose another port")
    if wf_audit.config().get("auto_start") and "--no-auditor" not in sys.argv:   # off unless config/auditor.yaml says auto_start
        start_auditor()
    print(f"Workflow playbook ready at http://127.0.0.1:{port} (Ctrl+C to stop; Auditor {'on' if auditor_running() else 'off'})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping workflow playbook.", flush=True)
    finally:
        server.server_close()
        if auditor_running():
            try:
                cancel_job(auditor["job"])
            except (RuntimeError, ValueError, KeyError):
                pass


if __name__ == "__main__":
    main()
