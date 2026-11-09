#!/usr/bin/env python3
"""Experiment: drive ONE long-lived Copilot session over ACP (copilot --acp --stdio) like an agent would be.

  python scripts/acp_probe.py --agent wf-planner "Ask me which repo to use, wait for my answer, then say OK"

It prints every message the agent sends. After each turn it asks YOU for a follow-up (empty line quits) and sends it
to the SAME session, so you can see whether an agent could pause, ask, get an answer and continue with its context.
Permission requests are auto-approved (this is a throw-away experiment). ACP is a public preview and may change.
"""
import argparse
import json
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--agent", default="")
    a = ap.parse_args()
    cmd = ["copilot", "--acp", "--stdio"]
    if a.agent:
        cmd += ["--agent", a.agent]
    p = subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", shell=(sys.platform == "win32"))
    nid = [0]
    waiters = {}
    lock = threading.Lock()

    def send(obj):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()

    def call(method, params):
        with lock:
            nid[0] += 1
            i = nid[0]
        ev = threading.Event()
        waiters[i] = [ev, None]
        send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        ev.wait()
        return waiters[i][1]

    def reader():
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                print("RAW:", line)
                continue
            if "method" in m and "id" in m:                      # a request FROM the agent
                print("AGENT REQUEST:", json.dumps(m)[:600], flush=True)
                if m["method"] == "session/request_permission":
                    opts = m["params"].get("options", [])
                    pick = next((o["optionId"] for o in opts if "allow" in str(o.get("kind", "")) or "allow" in str(o.get("optionId", ""))), opts[0]["optionId"] if opts else "")
                    send({"jsonrpc": "2.0", "id": m["id"], "result": {"outcome": {"outcome": "selected", "optionId": pick}}})
                else:
                    send({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "not supported by this probe"}})
            elif "method" in m:                                  # a notification
                u = (m.get("params") or {}).get("update") or {}
                kind = u.get("sessionUpdate", m["method"])
                text = (u.get("content") or {}).get("text") if isinstance(u.get("content"), dict) else None
                print(f"[{kind}]", text if text else json.dumps(u)[:300], flush=True)
            elif "id" in m and m["id"] in waiters:               # a response to us
                waiters[m["id"]][1] = m
                waiters[m["id"]][0].set()
        for w in waiters.values():
            w[0].set()

    threading.Thread(target=reader, daemon=True).start()
    print(call("initialize", {"protocolVersion": 1, "clientCapabilities": {}}))
    s = call("session/new", {"cwd": str(ROOT), "mcpServers": []})
    sid = ((s or {}).get("result") or {}).get("sessionId")
    print("session:", sid)
    text = a.prompt
    while text:
        r = call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": text}]})
        print("\n--- turn finished:", json.dumps((r or {}).get("result") or r)[:200])
        text = input("follow-up (empty to quit)> ").strip()
    p.stdin.close()


if __name__ == "__main__":
    main()
