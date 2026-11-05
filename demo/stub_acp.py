#!/usr/bin/env python3
"""Fake ACP server for tests: speaks just enough of the protocol and does the stub agent's placeholder work.
Environment: DEMO_ASK=<lane> makes that lane ask a question on its first prompt and work after the answer.
Run it like `copilot --acp --stdio`: it reads JSON-RPC lines on stdin."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stub_agent as A  # noqa: E402  (reads WF_LANE, WF_TICKET ... from the environment)

prompts = 0


def reply(i, result):
    print(json.dumps({"jsonrpc": "2.0", "id": i, "result": result}), flush=True)


def chunk(sid, text):
    print(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}}), flush=True)


for line in sys.stdin:
    m = json.loads(line)
    if m["method"] == "initialize":
        reply(m["id"], {"protocolVersion": 1, "agentCapabilities": {}})
    elif m["method"] == "session/new":
        reply(m["id"], {"sessionId": "stub-session"})
    elif m["method"] == "session/prompt":
        prompts += 1
        said = " ".join(b.get("text", "") for b in (m["params"].get("prompt") or []))
        time.sleep(float(os.environ.get("DEMO_PACE", "1")))
        if A.TASK_DIR:     # chat with a person outside a ticket
            time.sleep(0.3)
            chunk("stub-session", f"[stub {A.LANE}] I got: " + said.split("The person says:")[-1].strip()[:200])
        elif os.environ.get("DEMO_ASK") == A.LANE and prompts == 1:
            chunk("stub-session", "Which environment should I target?")
        else:
            ({"planner": A.planner, "qe": A.qe, "dev": A.dev, "reviewer": A.reviewer, "inspect-adapt": A.inspect_adapt}[A.LANE])()
            chunk("stub-session", "Done.")
        reply(m["id"], {"stopReason": "end_turn"})
