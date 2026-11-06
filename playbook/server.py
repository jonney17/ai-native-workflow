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

PLAYBOOK = ROOT / "playbook" / "index.html"
TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LANES = ("planner", "qe", "dev", "reviewer", "inspect-adapt")
KINDS = ("story", "chore", "spike")
ARTIFACT_FILES = frozenset((
    "ticket-context.md", "spec.md", "design.md", "tasks.md", "test-plan.md",
    "verify-report.md", "review-report.md", "inspection-report.md", "acceptance-report.md",
))
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


def latest_lane_log(ticket, run_number, lane):
    if lane not in LANES:
        raise ValueError("Unsupported workflow lane.")
    status = analyze(ticket, run_number)
    log_dir = (ROOT / status["path"] / "logs").resolve()
    if not log_dir.is_dir():
        return None
    pattern = f"{lane}*-a*.log"
    log_pattern = re.compile(rf"{re.escape(lane)}(?:-[A-Za-z0-9._-]+)?-a(\d+)\.log")
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
    if name not in ARTIFACT_FILES:
        raise ValueError("Unsupported artifact.")
    status = analyze(ticket, run_number)
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
        if kind not in KINDS:
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
        lane = action.get("lane")
        if not isinstance(lane, str) or lane not in res["lanes"] or res["lanes"][lane]["state"] == "disabled":
            raise ValueError("That lane is not enabled for this work item.")
        return base + ["worker"] + args + ["--lane", lane]
    if verb == "inspect-adapt":
        lane = "inspect-adapt"
        lane_info = res["lanes"].get(lane)
        if (
            not lane_info
            or (
                lane_info["state"] not in ("ready", "stale")
                and not lane_info.get("resumable")
            )
            or lane_info.get("unmet")
        ):
            raise ValueError("Inspect & Adapt is not ready to launch.")
        return base + ["worker"] + args + ["--lane", lane, "--verbose"]
    if verb == "approve":
        gate = action.get("gate")
        if (not isinstance(gate, str) or gate not in ("planner", "reviewer")
                or (res.get("gates") or {}).get(gate, {}).get("state") != "awaiting_approval"):
            raise ValueError("Unsupported approval gate.")
        return base + ["approve"] + args + ["--gate", gate]
    if verb == "reject":
        gate, reason = action.get("gate"), action.get("reason")
        if (not isinstance(gate, str) or gate not in ("planner", "reviewer")
                or (res.get("gates") or {}).get(gate, {}).get("state") != "awaiting_approval"):
            raise ValueError("That gate is not waiting for a human decision.")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 1000:
            raise ValueError("A rejection reason is required (up to 1,000 characters).")
        command = base + ["reject"] + args + ["--gate", gate, "--reason", reason.strip()]
        if gate == "planner" and res["kind"] != "spike" and not is_workflow_running(ticket):
            command.append("--restart")
        return command
    if verb == "answer":
        lane, raw_text = action.get("lane"), action.get("text", "")
        text = raw_text.strip() if isinstance(raw_text, str) else None
        if (not isinstance(lane, str) or lane not in res["lanes"] or not isinstance(text, str)
                or not text or len(text) > 5000):
            raise ValueError("Choose a valid lane and enter an answer (up to 5,000 characters).")
        return base + ["answer"] + args + ["--lane", lane, "--text", text]
    if verb == "deployed":
        repo = action.get("repo")
        repos = (res["lanes"].get("dev") or {}).get("repos") or {}
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


def backlog_items():
    result = []
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
        if parsed.path == "/api/backlog":
            self.send_json({"tickets": backlog_items()})
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
                lane = query.get("lane", [""])[0]
                result = latest_lane_log(ticket, run_number, lane)
                if result is None:
                    self.send_json({"error": "No log is available for this lane yet."}, 404)
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
        if path not in ("/api/action", "/api/archive", "/api/restore") and not re.fullmatch(r"/api/jobs/[a-f0-9]{32}/cancel", path):
            self.send_json({"error": "Not found."}, 404)
            return
        try:
            origin = self.headers.get("Origin")
            if origin:
                origin_host = urlparse(origin).hostname
                if origin_host not in ("127.0.0.1", "localhost"):
                    raise ValueError("Actions are accepted only from the local playbook.")
            if path not in ("/api/action", "/api/archive", "/api/restore"):
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
            if length < 1 or length > 16000:
                raise ValueError("Request body must be between 1 and 16,000 bytes.")
            action = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(action, dict):
                raise ValueError("Action must be a JSON object.")
            if path in ("/api/archive", "/api/restore"):
                ticket = move_work_item(action.get("ticket"), restore=path == "/api/restore")
                self.send_json({"ticket": ticket})
                return
            if action.get("action") == "start-from-backlog":
                with work_item_lock:
                    result = start_backlog_workflow(action.get("ticket"))
                self.send_json(result, 202)
                return
            if action.get("action") == "confirm-human":
                with work_item_lock:
                    result = confirm_human_action(action)
                self.send_json(result)
                return
            with work_item_lock:
                command = command_for(action)
                restarts_workflow = "--restart" in command
                label = "Reject plan · restart workflow" if restarts_workflow else (
                    "Inspect & Adapt"
                    if action.get("action") == "inspect-adapt"
                    else str(action.get("action", "workflow action"))
                )
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
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            sys.exit("usage: python playbook/server.py [port 1-65535]")
    try:
        server = PlaybookHTTPServer(("127.0.0.1", port), PlaybookHandler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        sys.exit(f"error: port {port} is already in use; stop the existing server or choose another port")
    print(f"Workflow playbook ready at http://127.0.0.1:{port} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping workflow playbook.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
