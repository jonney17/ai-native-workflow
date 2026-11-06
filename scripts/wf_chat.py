#!/usr/bin/env python3
"""Chat daemon for the UI: one long-lived ACP session per lane, for talking to a lane outside any ticket.

JSON lines on stdin:  {"op": "send", "lane": "dev", "text": "..."}  or  {"op": "reset", "lane": "dev"}
JSON lines on stdout: {"lane", "type": "chunk"|"tool"|"turn_end"|"error", "text"}.
"""
import json
import sys
import threading
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wf_run as R  # noqa: E402
import wf_acp as A  # noqa: E402

ROOT = R.ROOT
sessions, locks = {}, {}
out_lock = threading.Lock()


def emit(**o):
    with out_lock:
        sys.stdout.write(json.dumps(o) + "\n")
        sys.stdout.flush()


def preamble(lane, task_dir):
    runner = R.load_cfg("runner.yaml")
    return str(runner.get("adhoc_prompt", "")).replace("{root}", str(ROOT)).replace("{lane}", lane).replace("{task_dir}", task_dir)


def send(lane, text):
    lock = locks.setdefault(lane, threading.Lock())
    with lock:                                   # one turn at a time per lane
        try:
            sess = sessions.get(lane)
            first = sess is None or not sess.alive()
            task_dir = ROOT / "tasks" / f"chat-{lane}"
            if first:
                task_dir.mkdir(parents=True, exist_ok=True)
                runner = R.load_cfg("runner.yaml")
                fmt = {"lane": lane, "root": str(ROOT), "repo_path": "", "repo": "", "ticket": "", "run": 0}
                sess = sessions[lane] = A.Session(R.acp_cmd(runner, fmt, ""), ROOT, {"WF_LANE": lane, "WF_TASK_DIR": str(task_dir)})
                text = preamble(lane, str(task_dir)) + "\n\nThe person says:\n" + text
            sess.on_chunk = lambda t: emit(lane=lane, type="chunk", text=t)
            sess.echo = lambda line: emit(lane=lane, type="tool", text=line) if line.startswith(("[tool]", "[permission]")) else None
            sess.prompt(text)
            emit(lane=lane, type="turn_end", text="")
        except Exception as e:
            sessions.pop(lane, None)
            emit(lane=lane, type="error", text=str(e))


def main():
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        lane = str(m.get("lane", ""))
        if m.get("op") == "send" and lane in R.S.load_yaml(ROOT / "config" / "lanes.yaml").get("lanes", {}):
            threading.Thread(target=send, args=(lane, str(m.get("text", ""))), daemon=True).start()
        elif m.get("op") == "reset":
            s = sessions.pop(lane, None)
            if s:
                s.close()
            emit(lane=lane, type="turn_end", text="")


if __name__ == "__main__":
    main()
