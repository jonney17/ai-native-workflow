#!/usr/bin/env python3
"""A small local web UI for a run: see every lane, read its artifacts and logs, answer its questions,
approve or reject at the gates. It calls the same commands as scripts/wf_run.py; nothing new is stored.

  python scripts/wf_ui.py [--port 8765]      then open http://127.0.0.1:8765 (opens by itself)

Only listens on 127.0.0.1. Standard library only.
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wf_status as S  # noqa: E402
import wf_state as W  # noqa: E402

ROOT = S.ROOT
PY = sys.executable
NAME = re.compile(r"^[A-Za-z0-9._-]+$")
LANES = ("planner", "qe", "dev", "reviewer", "inspect-adapt")
COMMANDS = ("ready", "approve", "reject", "answer", "retry", "accept", "deployed")
workers = {}   # ticket -> Popen of `wf_run.py start`


def tickets():
    d = ROOT / "tickets"
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("_")) if d.is_dir() else []


def question_of(run, lane, repo=""):
    """The last question a lane (or one Dev repo) raised: text for the person to read."""
    unit = f"{lane}-{repo}" if repo else lane
    for e in reversed(run.events()):
        d = e["detail"]
        if e["event"] == "question_raised" and (e["actor"] == unit or (not repo and d.get("lane") == lane) or (repo and d.get("repo") == repo)):
            return " ".join(str(d[k]) for k in ("question", "reason", "needed", "text", "blocker") if d.get(k))
    return ""


def last_log(run_dir, lane, repo=""):
    unit = f"{lane}-{repo}" if repo else lane
    logs = sorted((run_dir / "logs").glob(f"{unit}-a*.log"), key=lambda p: p.stat().st_mtime) if (run_dir / "logs").is_dir() else []
    return logs[-1].relative_to(run_dir).as_posix() if logs else ""


def state(ticket):
    res = S.analyze(ticket)
    run = W.Run(ticket, res["run"])
    for lane, rec in res["lanes"].items():
        rec["question"] = question_of(run, lane) if rec["state"] == "blocked" else ""
        rec["log"] = last_log(run.dir, lane)
        for repo, r in (rec.get("repos") or {}).items():
            r["log"] = last_log(run.dir, lane, repo)
            r["question"] = question_of(run, lane, repo) if r["state"] == "blocked" else ""
    arts = []
    adir = run.dir / "artifacts"
    if adir.is_dir():
        arts = sorted(p.relative_to(run.dir).as_posix() for p in adir.rglob("*") if p.is_file() and p.name != ".gitkeep")
    res["files"] = arts
    p = workers.get(ticket)
    res["workers_running"] = bool(p and p.poll() is None)
    return res


def read_file(ticket, rel):
    res = S.analyze(ticket)
    base = (ROOT / res["path"]).resolve()
    p = (base / rel).resolve()
    if base not in p.parents or not p.is_file():
        return "(not found)"
    text = p.read_text(encoding="utf-8", errors="replace")
    return text[-20000:] if len(text) > 20000 else text


def act(body):
    t, cmd = str(body.get("ticket", "")), str(body.get("cmd", ""))
    if not NAME.match(t) or cmd not in COMMANDS:
        return "refused: bad ticket or command"
    args = [PY, str(HERE / "wf_run.py"), cmd, "--ticket", t, "--by", "ui"]
    lane, repo, text, gate = (str(body.get(k) or "") for k in ("lane", "repo", "text", "gate"))
    if lane:
        if lane not in LANES:
            return "refused: bad lane"
        args += ["--lane", lane]
    if repo:
        if not NAME.match(repo):
            return "refused: bad repo"
        args += ["--repo", repo]
    if gate in ("planner", "reviewer"):
        args += ["--gate", gate]
    if cmd == "answer":
        args += ["--text", text]
    if cmd == "reject":
        args += ["--reason", text]
    r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout + r.stderr).strip() or "done"


def start_workers(ticket):
    if not NAME.match(ticket):
        return "refused"
    p = workers.get(ticket)
    if p and p.poll() is None:
        return "workers are already running"
    res = S.analyze(ticket)
    logdir = ROOT / res["path"] / "logs"
    logdir.mkdir(exist_ok=True)
    f = open(logdir / "workers.log", "a", encoding="utf-8")
    workers[ticket] = subprocess.Popen([PY, str(HERE / "wf_run.py"), "start", "--ticket", ticket, "--verbose"], cwd=str(ROOT),
                                       stdout=f, stderr=subprocess.STDOUT)
    return "workers started"


def stop_workers(ticket):
    p = workers.get(ticket)
    if p and p.poll() is None:
        p.terminate()
        return "workers stopped"
    return "no workers started from this page"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self.send(PAGE, "text/html")
            if u.path == "/api/tickets":
                return self.send(json.dumps(tickets()))
            t = q.get("ticket", "")
            if not NAME.match(t):
                return self.send(json.dumps({"error": "bad ticket"}), code=400)
            if u.path == "/api/state":
                return self.send(json.dumps(state(t), default=str))
            if u.path == "/api/file":
                return self.send(read_file(t, q.get("path", "")), "text/plain")
            if u.path == "/api/workers-log":
                return self.send(read_file(t, "logs/workers.log"), "text/plain")
        except Exception as e:  # show the problem in the page instead of a dead connection
            return self.send(json.dumps({"error": str(e)}), code=500)
        self.send("not found", "text/plain", 404)

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/action":
                return self.send(json.dumps({"message": act(body)}))
            if self.path == "/api/start":
                return self.send(json.dumps({"message": start_workers(str(body.get("ticket", "")))}))
            if self.path == "/api/stop":
                return self.send(json.dumps({"message": stop_workers(str(body.get("ticket", "")))}))
        except Exception as e:
            return self.send(json.dumps({"message": f"error: {e}"}), code=500)
        self.send("not found", "text/plain", 404)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Workflow</title><style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#dcdcd4;--acc:#1f5fbf;--ok:#1b7f3b;--warn:#b26a00;--bad:#b3261e}
@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#ececea;--mut:#9a9a94;--card:#202020;--line:#383836;--acc:#6ea8ff;--ok:#5fcf80;--warn:#f0b050;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:16px}header{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
h1{font-size:18px;margin:0 8px 0 0}select,button,textarea{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
button{cursor:pointer}button.p{background:var(--acc);color:#fff;border-color:var(--acc)}button.d{color:var(--bad)}
.box{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0}
.lane{display:grid;grid-template-columns:130px 1fr;gap:6px 12px}.lane+.lane{border-top:1px solid var(--line);margin-top:10px;padding-top:10px}
.st{font-weight:600}.done,.ready,.approved{color:var(--ok)}.working,.blocked,.waiting,.stale{color:var(--warn)}.failed,.rejected{color:var(--bad)}
.mut{color:var(--mut)}.q{border-left:3px solid var(--warn);padding:4px 10px;margin:6px 0;white-space:pre-wrap}
textarea{width:100%;min-height:54px;margin:4px 0}pre{white-space:pre-wrap;word-break:break-word;max-height:380px;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;font-size:13px}
a{color:var(--acc);cursor:pointer}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px 0}.files a{margin-right:12px}
@media(max-width:560px){.lane{grid-template-columns:1fr}}</style></head><body><main>
<header><h1>Workflow</h1><select id="t"></select><button class="p" id="go">Start workers</button><button id="stop">Stop</button><span id="msg" class="mut"></span></header>
<div class="box"><div id="status"></div><div id="next" class="mut"></div></div>
<div id="lanes" class="box"></div>
<div class="box"><b>Files</b> <span class="mut">(click to read)</span><div id="files" class="files"></div><pre id="view" hidden></pre></div>
<div class="box"><a id="wl">Worker output</a><pre id="wlog" hidden></pre></div>
</main><script>
const $=id=>document.getElementById(id);let T='',busy=false;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(p,b){const r=await fetch(p,b?{method:'POST',body:JSON.stringify(b)}:{});const ct=r.headers.get('content-type')||'';return ct.includes('json')?r.json():r.text()}
async function act(o){const r=await api('/api/action',{ticket:T,...o});$('msg').textContent=r.message;load(true)}
async function show(path){const v=$('view');v.hidden=false;v.textContent='…';v.textContent=await api('/api/file?ticket='+T+'&path='+encodeURIComponent(path))}
window.A=act;window.S=show;
function qbox(lane,repo,q){const id='a-'+lane+(repo?'-'+repo:'');return `<div class="q">${esc(q)}</div><textarea id="${id}" placeholder="Your answer. It goes into the lane's next task message."></textarea>
<button class="p" onclick="A({cmd:'answer',lane:'${lane}',repo:'${repo}',text:$('${id}').value})">Answer and unblock</button>`}
function render(s){
 $('status').innerHTML=`<b>${esc(s.ticket)}</b> run ${s.run} (${esc(s.kind)}) &nbsp; <span class="st">${esc(s.status)}</span> <span class="mut">${esc(s.detail)}</span> &nbsp; <span class="mut">workers ${s.workers_running?'running':'not started here'}</span>`;
 $('next').innerHTML=s.next.map(n=>'• '+esc(n.who)+': '+esc(n.text)).join('<br>');
 let h='';
 for(const [lane,r] of Object.entries(s.lanes)){
  if(r.state==='disabled')continue;
  h+=`<div class="lane"><div><b>${lane}</b><br><span class="st ${r.state}">${r.state}</span></div><div><span class="mut">${esc(r.note)}</span>`;
  if(r.log)h+=` <a onclick="S('${r.log}')">last log</a>`;
  if(r.question)h+=qbox(lane,'',r.question);
  if(r.state==='failed')h+=`<div class="row"><button onclick="A({cmd:'retry',lane:'${lane}'})">Retry with a fresh budget</button></div>`;
  for(const [repo,x] of Object.entries(r.repos||{})){
   h+=`<div class="row"><span>${esc(repo)}: <span class="st ${x.state}">${x.state}</span></span>`+(x.log?` <a onclick="S('${x.log}')">last log</a>`:'')+
      (x.state==='failed'?` <button onclick="A({cmd:'retry',lane:'dev',repo:'${repo}'})">Retry</button>`:'')+`</div>`;
   if(x.state==='blocked')h+=qbox(lane,repo,x.question||'(blocked; see its last log)');
  }
  h+='</div></div>';
 }
 // gates and human steps
 const g=s.gates||{};let hb='';
 if(s.status==='Awaiting intake'||(s.artifacts['ticket-context.md']||{}).status==='draft'||(s.artifacts['ticket-context.md']||{}).status==='missing')hb+=`<button class="p" onclick="A({cmd:'ready'})">Mark ticket context ready</button> `;
 for(const [name,x] of Object.entries(g)){
  if(x.state==='awaiting_approval')hb+=`<div class="row"><b>${name} gate</b> ${x.artifacts.map(a=>`<a onclick="S('artifacts/${a}')">${a}</a>`).join(', ')}
   <button class="p" onclick="A({cmd:'approve',gate:'${name}'})">Approve</button>
   <input id="rj-${name}" placeholder="reason to send back" size="26"><button class="d" onclick="A({cmd:'reject',gate:'${name}',text:$('rj-${name}').value})">Reject</button></div>`;
 }
 if(s.status==='Awaiting acceptance')hb+=`<button class="p" onclick="A({cmd:'accept'})">Accept the ticket</button> `;
 h+=hb?`<div class="lane"><div><b>You</b></div><div>${hb}</div></div>`:'';
 $('lanes').innerHTML=h;
 $('files').innerHTML=s.files.map(f=>`<a onclick="S('${f}')">${esc(f.replace('artifacts/',''))}</a>`).join('')||'<span class="mut">none yet</span>';
 if(s.warnings&&s.warnings.length)$('next').innerHTML+='<br>⚠ '+s.warnings.map(esc).join('<br>⚠ ');
}
async function load(force){
 if(!T)return;const a=document.activeElement;
 if(!force&&a&&(a.tagName==='TEXTAREA'||a.tagName==='INPUT'))return;
 const s=await api('/api/state?ticket='+T);if(s.error){$('status').textContent=s.error;return}render(s)}
$('t').onchange=()=>{T=$('t').value;load(true)};
$('go').onclick=async()=>{$('msg').textContent=(await api('/api/start',{ticket:T})).message;load(true)};
$('stop').onclick=async()=>{$('msg').textContent=(await api('/api/stop',{ticket:T})).message;load(true)};
$('wl').onclick=async()=>{const p=$('wlog');p.hidden=!p.hidden;if(!p.hidden)p.textContent=await api('/api/workers-log?ticket='+T)};
(async()=>{const l=await api('/api/tickets');$('t').innerHTML=l.map(x=>`<option>${esc(x)}</option>`).join('');T=$('t').value;load(true);setInterval(load,3000)})();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}"
    print(f"Workflow UI on {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    for p in workers.values():
        if p.poll() is None:
            p.terminate()


if __name__ == "__main__":
    main()
