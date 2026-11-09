#!/usr/bin/env python3
"""One long-lived agent session over ACP (Agent Client Protocol), e.g. `copilot --acp --stdio`.

An agent keeps its session open between turns: it can stop to ask a question, a person answers, and the answer is
sent to the SAME session, so the agent continues with everything it already read. Newline-delimited JSON-RPC on stdio.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time


class AcpError(RuntimeError):
    pass


class Session:
    def __init__(self, cmd, cwd, env=None, log=None, echo=None):
        """cmd: argv of the ACP server. log: open text file for the transcript. echo: function(line) for live output."""
        exe = shutil.which(cmd[0])
        if exe is None:
            raise AcpError(f"command not found: {cmd[0]}")
        self.cwd, self.log, self.echo = str(cwd), log, echo
        self.idle_seconds = 0    # a turn with no output at all for this long is treated as hung (0 = wait forever)
        self._last = time.time()
        self.on_chunk = None     # function(text) called for each piece of the agent's message as it streams
        self._err = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
        self.cmdline = " ".join([exe] + list(cmd[1:]))
        self.proc = subprocess.Popen([exe] + list(cmd[1:]), cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._err, text=True, encoding="utf-8", errors="replace",
                                     env={**os.environ, **(env or {})})
        self._id, self._lock, self._wait = 0, threading.Lock(), {}
        self._chunks = []
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self._call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            r = self._call("session/new", {"cwd": self.cwd, "mcpServers": []})
        except (OSError, AcpError) as e:
            raise AcpError(f"{e}\ncommand: {self.cmdline}\nstderr: {self.stderr_tail()}") from None
        self.sid = (r.get("result") or {}).get("sessionId")
        if not self.sid:
            raise AcpError(f"no session: {r}\ncommand: {self.cmdline}\nstderr: {self.stderr_tail()}")

    def stderr_tail(self):
        try:
            self._err.flush()
            self._err.seek(0)
            return self._err.read()[-1500:].strip() or "(empty)"
        except (OSError, ValueError):
            return "(unreadable)"

    # ---- plumbing
    def _send(self, obj):
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except OSError:
            raise AcpError("the agent process ended") from None

    def _call(self, method, params):
        with self._lock:
            self._id += 1
            i = self._id
        ev = threading.Event()
        self._wait[i] = [ev, None]
        self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        while not ev.wait(1.0):
            if self.proc.poll() is not None and not ev.is_set():
                raise AcpError("the agent process ended")
            if method == "session/prompt" and self.idle_seconds and time.time() - self._last > self.idle_seconds:
                self.proc.kill()
                raise AcpError(f"no output from the agent for {self.idle_seconds} s; stopped it so the agent can be retried")
        return self._wait.pop(i)[1] or {}

    def _raw(self, text):
        if self.log:
            try:
                self.log.write(text)
                self.log.flush()
            except ValueError:   # the log was closed between turns
                pass

    def _say(self, text):
        if self.log:
            self.log.write(text)
            self.log.flush()
        if self.echo:
            for line in text.splitlines():
                self.echo(line)

    def _read(self):
        for line in self.proc.stdout:
            self._last = time.time()
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if "method" in m and "id" in m:                      # a request from the agent
                self._send(self._answer_request(m))
            elif "method" in m:                                  # streamed update
                u = (m.get("params") or {}).get("update") or {}
                kind, c = u.get("sessionUpdate", ""), u.get("content")
                if kind == "agent_message_chunk" and isinstance(c, dict):
                    text = c.get("text", "")
                    if text.startswith("Info: "):                    # a notice from the CLI, not the agent's words
                        self._raw("\n" + text.strip() + "\n")
                        continue
                    self._chunks.append(text)
                    if self.on_chunk:
                        self.on_chunk(text)
                    self._raw(text)                                  # live: the log fills as the agent speaks
                elif kind == "tool_call":
                    self._say(f"[tool] {u.get('title', '')}\n")
            elif m.get("id") in self._wait:
                self._wait[m["id"]][1] = m
                self._wait[m["id"]][0].set()
        for w in list(self._wait.values()):                      # process ended: release anyone waiting
            w[0].set()

    def _answer_request(self, m):
        """Permission requests: allow, except `git push` (the same rule as --deny-tool "shell(git push)")."""
        if m["method"] != "session/request_permission":
            return {"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "not supported"}}
        opts = (m.get("params") or {}).get("options") or []
        bad = "git push" in json.dumps(m.get("params") or {}).lower()
        want = "reject" if bad else "allow"
        pick = next((o.get("optionId") for o in opts if want in str(o.get("kind", "")).lower()), None)
        self._say(f"[permission] {'denied' if bad else 'allowed'}\n")
        if pick is None:
            return {"jsonrpc": "2.0", "id": m["id"], "result": {"outcome": {"outcome": "cancelled"}}}
        return {"jsonrpc": "2.0", "id": m["id"], "result": {"outcome": {"outcome": "selected", "optionId": pick}}}

    # ---- use
    def alive(self):
        return self.proc.poll() is None

    def prompt(self, text):
        """Send one turn and wait for it to end. Returns (stop_reason, the agent's last message)."""
        self._chunks = []
        self._last = time.time()
        self._say(f"\n>>> {' '.join(text.split())[:220]}\n")
        r = self._call("session/prompt", {"sessionId": self.sid, "prompt": [{"type": "text", "text": text}]})
        msg = "".join(self._chunks).strip()
        self._raw("\n")
        if self.echo:
            for line in msg.splitlines():
                self.echo(line)
        if "error" in r or not r:
            raise AcpError(str((r or {}).get("error") or "the agent process ended"))
        return (r.get("result") or {}).get("stopReason", ""), msg

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
