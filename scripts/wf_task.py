#!/usr/bin/env python3
"""One-off requests to a single lane, outside any ticket (used by the UI).

A task lives in tasks/<id>/: request.md, answers.md, and what the agent writes: question.md (it needs more
information from the person) or result.md. Status is derived from those files and from the running process.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wf_run as R  # noqa: E402

ROOT = R.ROOT
TASKS = ROOT / "tasks"
procs = {}   # task id -> Popen


class _Task:     # what wf_run.agent_command needs from a "run"
    def __init__(self, tid, d):
        self.ticket, self.run, self.dir = tid, 0, d

    def events(self):
        return []


def _dir(tid):
    return TASKS / tid


def create(lane, request):
    TASKS.mkdir(exist_ok=True)
    n = 1 + max([int(p.name.split("-")[1]) for p in TASKS.glob("T-*") if p.name.split("-")[1].isdigit()] + [0])
    tid = f"T-{n:03d}"
    d = _dir(tid)
    d.mkdir()
    (d / "lane").write_text(lane, encoding="utf-8")
    (d / "request.md").write_text(request.strip() + "\n", encoding="utf-8")
    launch(tid)
    return tid


def answer(tid, text):
    d = _dir(tid)
    with open(d / "answers.md", "a", encoding="utf-8") as f:
        f.write(f"- {text.strip()}\n")
    (d / "question.md").unlink(missing_ok=True)
    launch(tid, text.strip())


sessions = {}   # task id -> open ACP session (kept while the lane waits for the person)


def _prompt(tid, lane, d):
    runner = R.load_cfg("runner.yaml")
    runner = dict(runner, prompt=str(runner.get("adhoc_prompt", "")).replace("{task_dir}", str(d)))
    return runner, R.prompt_parts(runner, _Task(tid, d), lane, "", 1)


def _turn_acp(tid, lane, d, text):
    import wf_acp as A
    runner, (prompt, fmt) = _prompt(tid, lane, d)
    n = len(list(d.glob("run-*.log"))) + 1
    with open(d / f"run-{n}.log", "w", encoding="utf-8", errors="replace") as lf:
        try:
            sess = sessions.get(tid)
            if sess is not None and sess.alive() and text:
                sess.log = lf
            else:
                sess = sessions[tid] = A.Session(R.acp_cmd(runner, fmt, ""), ROOT, {"WF_LANE": lane, "WF_TASK_DIR": str(d)}, lf)
                text = prompt
            _, msg = sess.prompt(text)
            sess.log = None
            if msg and not (d / "result.md").exists() and not (d / "question.md").exists():
                (d / "question.md").write_text(msg + "\n", encoding="utf-8")   # it stopped and said something: that is its question
            if (d / "result.md").exists() and not (d / "question.md").exists():
                sessions.pop(tid, None)
                sess.close()
        except Exception as e:  # keep the reason where the page can show it
            lf.write(f"\nERROR: {e}\n")
            sessions.pop(tid, None)


def launch(tid, answer_text=""):
    d = _dir(tid)
    lane = (d / "lane").read_text(encoding="utf-8").strip()
    runner = R.load_cfg("runner.yaml")
    if R.use_acp(runner):
        th = threading.Thread(target=_turn_acp, args=(tid, lane, d, ("Answer from a person: " + answer_text + "\nContinue.") if answer_text else ""), daemon=True)
        th.start()
        procs[tid] = th
        return
    runner, (prompt, fmt) = _prompt(tid, lane, d)
    cmd = R.agent_command(runner, _Task(tid, d), lane, "", 1)
    n = len(list(d.glob("run-*.log"))) + 1
    log = open(d / f"run-{n}.log", "w", encoding="utf-8", errors="replace")
    env = {**os.environ, "WF_LANE": lane, "WF_TASK_DIR": str(d), "PYTHONIOENCODING": "utf-8"}
    procs[tid] = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env)


def status(tid):
    d = _dir(tid)
    p = procs.get(tid)
    if p is not None and (p.is_alive() if hasattr(p, "is_alive") else p.poll() is None):
        return "working"
    q, r = d / "question.md", d / "result.md"
    if q.exists() and (not r.exists() or q.stat().st_mtime >= r.stat().st_mtime):
        return "needs info"
    if r.exists():
        return "done"
    return "failed" if p else "unknown"


def read(tid):
    d = _dir(tid)
    rd = lambda n: (d / n).read_text(encoding="utf-8", errors="replace") if (d / n).exists() else ""
    logs = sorted(d.glob("run-*.log"))
    return {"id": tid, "lane": rd("lane").strip(), "status": status(tid), "request": rd("request.md"), "answers": rd("answers.md"),
            "question": rd("question.md"), "result": rd("result.md"), "log": (logs[-1].read_text(encoding="utf-8", errors="replace")[-4000:] if logs else "")}


def all_tasks():
    return [read(p.name) for p in sorted(TASKS.glob("T-*"), reverse=True)] if TASKS.is_dir() else []
