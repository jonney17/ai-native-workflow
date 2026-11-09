#!/usr/bin/env python3
"""Chat daemon for the UI: one long-lived ACP session per agent, for talking to an agent outside any ticket.

JSON lines on stdin:  {"op": "send", "agent": "dev", "text": "..."}  or  {"op": "reset", "agent": "dev"}
JSON lines on stdout: {"agent", "type": "chunk"|"tool"|"turn_end"|"error", "text"}.
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


def preamble(agent, task_dir):
    runner = R.load_cfg("runner.yaml")
    return R.chat_message(agent, task_dir)


def send(agent, text):
    lock = locks.setdefault(agent, threading.Lock())
    with lock:                                   # one turn at a time per agent
        try:
            sess = sessions.get(agent)
            first = sess is None or not sess.alive()
            task_dir = ROOT / "tasks" / f"chat-{agent}"
            if first:
                task_dir.mkdir(parents=True, exist_ok=True)
                runner = R.load_cfg("runner.yaml")
                fmt = {"agent": agent, "root": str(ROOT), "repo_path": "", "repo": "", "ticket": "", "run": 0}
                sess = sessions[agent] = A.Session(R.acp_cmd(runner, fmt, ""), ROOT, {"WF_AGENT": agent, "WF_TASK_DIR": str(task_dir)})
                text = preamble(agent, str(task_dir)) + "\n\nThe person says:\n" + text
            sess.on_chunk = lambda t: emit(agent=agent, type="chunk", text=t)
            sess.echo = lambda line: emit(agent=agent, type="tool", text=line) if line.startswith(("[tool]", "[permission]")) else None
            sess.prompt(text)
            emit(agent=agent, type="turn_end", text="")
        except Exception as e:
            sessions.pop(agent, None)
            emit(agent=agent, type="error", text=str(e))


def main():
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        agent = str(m.get("agent", ""))
        if m.get("op") == "send" and agent in R.C.load().agents_raw:
            threading.Thread(target=send, args=(agent, str(m.get("text", ""))), daemon=True).start()
        elif m.get("op") == "reset":
            s = sessions.pop(agent, None)
            if s:
                s.close()
            emit(agent=agent, type="turn_end", text="")


if __name__ == "__main__":
    main()
