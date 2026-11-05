#!/usr/bin/env python3
"""Local web UI for the team: the lanes as team members with their status, a ticket list, and one-off requests.

  python scripts/wf_ui.py [--port 8765]      then open http://127.0.0.1:8765 (opens by itself)

- The board shows each lane (idle, working, waiting, waiting for you, blocked) and what it is doing.
- Start a ticket from the list (config/backlog.yaml, fake for now; later the Jira board) and the lanes process it.
- Ask any lane for one task outside a ticket. If the lane lacks information it asks you; answer on the page.
Listens on 127.0.0.1 only.
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
import wf_task as TK  # noqa: E402
import yaml  # noqa: E402

ROOT = S.ROOT
PY = sys.executable
NAME = re.compile(r"^[A-Za-z0-9._-]+$")
LANES = ("planner", "dev", "qe", "reviewer", "inspect-adapt")
TITLES = {"planner": "Planner (architecture)", "dev": "Dev", "qe": "QE", "reviewer": "Reviewer", "inspect-adapt": "Inspect & Adapt"}
COMMANDS = ("ready", "approve", "reject", "answer", "retry", "accept", "deployed")
workers = {}   # ticket -> Popen of `wf_run.py start`


# ---------------------------------------------------------------- data

def backlog():
    p = ROOT / "config" / "backlog.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("tickets", []) if p.exists() else []


def ticket_dirs():
    d = ROOT / "tickets"
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("_") and any(p.glob("run-*"))) if d.is_dir() else []


def question_of(run, lane, repo=""):
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


def ticket_state(ticket):
    res = S.analyze(ticket)
    run = W.Run(ticket, res["run"])
    for lane, rec in res["lanes"].items():
        rec["question"] = question_of(run, lane) if rec["state"] == "blocked" else ""
        rec["log"] = last_log(run.dir, lane)
        for repo, r in (rec.get("repos") or {}).items():
            r["log"] = last_log(run.dir, lane, repo)
            r["question"] = question_of(run, lane, repo) if r["state"] == "blocked" else ""
    adir = run.dir / "artifacts"
    res["files"] = sorted(p.relative_to(run.dir).as_posix() for p in adir.rglob("*") if p.is_file() and p.name != ".gitkeep") if adir.is_dir() else []
    p = workers.get(ticket)
    res["workers_running"] = bool(p and p.poll() is None)
    return res


RANK = {"idle": 0, "waiting": 1, "starting": 2, "working": 3, "waiting for you": 4, "blocked": 5, "failed": 6}


def board():
    """Each lane across all tickets and one-off tasks: an overall status and the lines behind it."""
    lanes = {l: {"title": TITLES[l], "status": "idle", "lines": [], "ask_for": ""} for l in LANES}
    cfg = S.load_yaml(ROOT / "config" / "lanes.yaml")
    for l in LANES:
        lanes[l]["ask_for"] = str(((cfg.get("lanes") or {}).get(l) or {}).get("ask_for", ""))

    def add(lane, status, text, ticket="", task=""):
        lanes[lane]["lines"].append({"status": status, "text": text, "ticket": ticket, "task": task})
        if RANK[status] > RANK[lanes[lane]["status"]]:
            lanes[lane]["status"] = status

    summaries = {}
    for t in ticket_dirs():
        try:
            res = S.analyze(t)
        except Exception as e:  # a broken ticket must not hide the board
            summaries[t] = {"status": f"error: {e}", "detail": ""}
            continue
        summaries[t] = {"status": res["status"], "detail": res["detail"]}
        if res["status"] in ("Done", "Escalated"):
            continue
        for lane, rec in res["lanes"].items():
            st = rec["state"]
            if lane not in lanes or st in ("done", "disabled"):
                continue
            who = "blocked" if st == "blocked" else "failed" if st == "failed" else "working" if st == "working" else "starting" if st in ("ready", "stale") else "waiting"
            add(lane, who, f"{t}: {rec['note'] or st}", ticket=t)
        g = res.get("gates") or {}
        if (g.get("planner") or {}).get("state") == "awaiting_approval":
            add("planner", "waiting for you", f"{t}: approve the plan", ticket=t)
        if (g.get("reviewer") or {}).get("state") == "awaiting_approval":
            add("reviewer", "waiting for you", f"{t}: approve the review", ticket=t)
        if res["status"] == "Awaiting acceptance":
            add("inspect-adapt", "waiting for you", f"{t}: accept the ticket", ticket=t)
    for task in TK.all_tasks():
        if task["lane"] in lanes and task["status"] != "done":
            st = {"working": "working", "needs info": "waiting for you", "failed": "failed"}.get(task["status"], "idle")
            if st != "idle":
                add(task["lane"], st, f"{task['id']}: {task['status']}", task=task["id"])
    items = []
    for b in backlog():
        s = summaries.get(b["id"])
        items.append({"id": b["id"], "title": b["title"], "repos": b.get("repos", []), "state": s["status"] if s else "Not started", "detail": s["detail"] if s else ""})
    return {"lanes": lanes, "tickets": items, "tasks": TK.all_tasks()[:8]}


# ---------------------------------------------------------------- actions

def read_file(ticket, rel):
    res = S.analyze(ticket)
    base = (ROOT / res["path"]).resolve()
    p = (base / rel).resolve()
    if base not in p.parents or not p.is_file():
        return "(not found)"
    text = p.read_text(encoding="utf-8", errors="replace")
    return text[-20000:]


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


def run_cmd(*args):
    r = subprocess.run([PY, *map(str, args)], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError((r.stdout + r.stderr).strip())
    return r.stdout


def start_ticket(tid, auto=False):
    b = next((x for x in backlog() if x["id"] == tid), None)
    if b is None or not NAME.match(tid):
        return "unknown ticket"
    if not (ROOT / "tickets" / tid).exists():
        run_cmd(HERE / "new_ticket.py", "--ticket", tid, "--kind", "story" if b.get("prd") else "chore", *(["--prd", b["prd"]] if b.get("prd") else ["--origin", "tech-debt"]))
        crit = "\n".join(f"| J-{i} | {c} |" for i, c in enumerate(b.get("criteria", []), 1))
        ctx = ROOT / "tickets" / tid / "run-1" / "artifacts" / "ticket-context.md"
        ctx.write_text(f"# {tid}: {b['title']}\n\nPRD refs: {b.get('prd') or 'none'}.\n\n## Description\n{b.get('description', '')}\n\n"
                       f"## {'Jira criteria' if b.get('prd') else 'Done criteria'}\n| Id | Criterion |\n|----|-----------|\n{crit}\n\n"
                       f"## Notes\nRepos involved: {', '.join(b.get('repos', []))}.\n", encoding="utf-8", newline="\n")
        run_cmd(HERE / "wf_run.py", "ready", "--ticket", tid, "--by", "ui")
    return start_workers(tid, auto)


def start_workers(ticket, auto=False):
    if not NAME.match(ticket):
        return "refused"
    p = workers.get(ticket)
    if p and p.poll() is None:
        return "workers are already running"
    res = S.analyze(ticket)
    logdir = ROOT / res["path"] / "logs"
    logdir.mkdir(exist_ok=True)
    f = open(logdir / "workers.log", "a", encoding="utf-8")
    workers[ticket] = subprocess.Popen([PY, str(HERE / "wf_run.py"), "start", "--ticket", ticket, "--verbose"] + (["--auto-approve"] if auto else []),
                                       cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)
    return f"{ticket}: the team is on it"


def stop_workers(ticket):
    p = workers.get(ticket)
    if p and p.poll() is None:
        p.terminate()
        return "workers stopped"
    return "no workers started from this page"


# ---------------------------------------------------------------- http

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
            if u.path == "/api/board":
                return self.send(json.dumps(board(), default=str))
            t = q.get("ticket", "")
            if u.path.startswith("/api/") and not NAME.match(t):
                return self.send(json.dumps({"error": "bad ticket"}), code=400)
            if u.path == "/api/state":
                return self.send(json.dumps(ticket_state(t), default=str))
            if u.path == "/api/file":
                return self.send(read_file(t, q.get("path", "")), "text/plain")
        except Exception as e:
            return self.send(json.dumps({"error": str(e)}), code=500)
        self.send("not found", "text/plain", 404)

    def do_POST(self):
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            m = lambda s: self.send(json.dumps({"message": s}))
            if self.path == "/api/action":
                return m(act(b))
            if self.path == "/api/start":
                return m(start_ticket(str(b.get("ticket", "")), bool(b.get("auto"))))
            if self.path == "/api/stop":
                return m(stop_workers(str(b.get("ticket", ""))))
            if self.path == "/api/ask":
                lane, text = str(b.get("lane", "")), str(b.get("text", "")).strip()
                if lane not in LANES or not text:
                    return m("write what you want the lane to do")
                return m(f"{TITLES[lane]}: task {TK.create(lane, text)} started")
            if self.path == "/api/answer-task":
                tid = str(b.get("id", ""))
                if not NAME.match(tid) or not str(b.get("text", "")).strip():
                    return m("write your answer")
                TK.answer(tid, str(b["text"]))
                return m(f"{tid}: answered, the lane is working again")
        except Exception as e:
            return self.send(json.dumps({"message": f"error: {e}"}), code=500)
        self.send("not found", "text/plain", 404)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Team workflow</title><style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#dcdcd4;--acc:#1f5fbf;--ok:#1b7f3b;--warn:#b26a00;--bad:#b3261e}
@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#ececea;--mut:#9a9a94;--card:#202020;--line:#383836;--acc:#6ea8ff;--ok:#5fcf80;--warn:#f0b050;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:1040px;margin:0 auto;padding:16px}h1{font-size:18px;margin:0}h2{font-size:15px;margin:18px 0 8px}
select,button,textarea,input{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
button{cursor:pointer}button.p{background:var(--acc);color:#fff;border-color:var(--acc)}button.d{color:var(--bad)}
.team{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px;min-width:0}
.pill{display:inline-block;border-radius:10px;padding:1px 9px;font-size:12px;font-weight:600;border:1px solid currentColor}
.idle{color:var(--mut)}.waiting,.starting{color:var(--warn)}.working,.done,.ready,.approved{color:var(--ok)}.blocked,.failed,.rejected{color:var(--bad)}.wfy{color:var(--acc)}
.line{font-size:13px;margin-top:5px;color:var(--mut)}.line a{color:var(--acc);cursor:pointer}
table{width:100%;border-collapse:collapse}td{padding:7px 6px;border-top:1px solid var(--line);vertical-align:top}.mut{color:var(--mut)}
textarea{width:100%;min-height:60px;margin:4px 0}pre{white-space:pre-wrap;word-break:break-word;max-height:320px;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;font-size:13px}
.q{border-left:3px solid var(--warn);padding:4px 10px;margin:6px 0;white-space:pre-wrap}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px 0}
.box{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:10px 0}a{color:var(--acc);cursor:pointer}.files a{margin-right:12px}
.lane{display:grid;grid-template-columns:120px 1fr;gap:4px 12px}.lane+.lane{border-top:1px solid var(--line);margin-top:8px;padding-top:8px}
@media(max-width:560px){.lane{grid-template-columns:1fr}}</style></head><body><main>
<div class="row"><h1>Team workflow</h1><span id="msg" class="mut"></span></div>
<h2>Team</h2><div id="team" class="team"></div>
<div id="ask" class="box" hidden></div>
<h2>Tickets <span class="mut" style="font-weight:400">(fake list; later the Jira board)</span></h2>
<div class="box"><table id="tickets"></table><label class="mut"><input type="checkbox" id="auto"> auto-approve the gates (testing only)</label></div>
<div id="detail"></div><div id="tasks"></div>
</main><script>
const $=id=>document.getElementById(id);let cur='',askLane='',busy=false;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(p,b){const r=await fetch(p,b?{method:'POST',body:JSON.stringify(b)}:{});return (r.headers.get('content-type')||'').includes('json')?r.json():r.text()}
const say=m=>$('msg').textContent=m;
const typing=()=>{const a=document.activeElement;return a&&(a.tagName==='TEXTAREA'||a.tagName==='INPUT'&&a.type==='text')};
const dos={
 async start(d){say((await api('/api/start',{ticket:d.id,auto:$('auto').checked})).message);cur=d.id;refresh(true)},
 open(d){cur=d.id;refresh(true)},
 async stop(d){say((await api('/api/stop',{ticket:d.id})).message)},
 ask(d){askLane=d.lane;renderAsk();$('ask').scrollIntoView({block:'nearest'})},
 async send(d){say((await api('/api/ask',{lane:askLane,text:$('askt').value})).message);askLane='';renderAsk();refresh(true)},
 async ans(d){say((await api('/api/answer-task',{id:d.id,text:$('ans-'+d.id).value})).message);refresh(true)},
 async cmd(d){const t=d.text?$(d.text).value:'';say((await api('/api/action',{ticket:cur,cmd:d.cmd,lane:d.lane,repo:d.repo,gate:d.gate,text:t})).message);refresh(true)},
 async file(d){const v=$('view');v.hidden=false;v.textContent=await api('/api/file?ticket='+cur+'&path='+encodeURIComponent(d.path))},
 cancel(){askLane='';renderAsk()}
};
document.addEventListener('click',e=>{const el=e.target.closest('[data-do]');if(el&&dos[el.dataset.do])dos[el.dataset.do](el.dataset)});
let board=null;
function renderAsk(){const el=$('ask');if(!askLane||!board){el.hidden=true;return}const l=board.lanes[askLane];el.hidden=false;
 el.innerHTML=`<b>Ask ${esc(l.title)}</b> <span class="mut">for one task. Give it: ${esc(l.ask_for)}. If something is missing it will ask you.</span>
 <textarea id="askt" placeholder="What should ${esc(l.title)} do?"></textarea><div class="row"><button class="p" data-do="send">Send</button><button data-do="cancel">Cancel</button></div>`;$('askt').focus()}
function renderTeam(){
 $('team').innerHTML=Object.entries(board.lanes).map(([k,l])=>`<div class="card"><b>${esc(l.title)}</b><br>
  <span class="pill ${l.status==='waiting for you'?'wfy':l.status}">${l.status}</span>
  ${l.lines.map(x=>`<div class="line ${x.status==='waiting for you'?'wfy':''}">${x.ticket?`<a data-do="open" data-id="${esc(x.ticket)}">${esc(x.text)}</a>`:esc(x.text)}</div>`).join('')}
  <div class="row"><button data-do="ask" data-lane="${k}">Ask</button></div></div>`).join('')}
function renderTickets(){
 $('tickets').innerHTML=board.tickets.map(t=>`<tr><td><b>${esc(t.id)}</b></td><td>${esc(t.title)}<br><span class="mut">${esc(t.repos.join(', '))}</span></td>
  <td>${esc(t.state)}<br><span class="mut">${esc(t.detail)}</span></td>
  <td>${t.state==='Not started'?`<button class="p" data-do="start" data-id="${esc(t.id)}">Start</button>`:`<button data-do="open" data-id="${esc(t.id)}">Open</button>`}</td></tr>`).join('')}
function renderTasks(){
 const ts=board.tasks;if(!ts.length){$('tasks').innerHTML='';return}
 $('tasks').innerHTML='<h2>One-off requests</h2>'+ts.map(t=>`<div class="box"><b>${esc(t.id)}</b> ${esc(t.lane)} <span class="pill ${t.status==='working'?'working':t.status==='done'?'done':t.status==='failed'?'failed':'waiting'}">${esc(t.status)}</span>
  <div class="mut" style="white-space:pre-wrap">${esc(t.request)}${t.answers?'\n'+esc(t.answers):''}</div>
  ${t.question?`<div class="q">${esc(t.question)}</div><textarea id="ans-${t.id}" placeholder="Your answer"></textarea><button class="p" data-do="ans" data-id="${t.id}">Answer</button>`:''}
  ${t.result?`<pre>${esc(t.result)}</pre>`:''}${t.status==='failed'?`<pre>${esc(t.log)}</pre>`:''}</div>`).join('')}
function renderDetail(s){
 let h=`<h2>${esc(s.ticket)} <span class="mut" style="font-weight:400">${esc(s.status)} - ${esc(s.detail)}</span></h2><div class="box">
  <div class="row"><button data-do="stop" data-id="${esc(s.ticket)}">Stop workers</button><span class="mut">workers ${s.workers_running?'running':'not started from this page'}</span></div>
  ${s.next.map(n=>'<div class="mut">• '+esc(n.who)+': '+esc(n.text)+'</div>').join('')}${(s.warnings||[]).map(w=>'<div class="mut">⚠ '+esc(w)+'</div>').join('')}`;
 for(const [lane,r] of Object.entries(s.lanes)){
  if(r.state==='disabled')continue;
  h+=`<div class="lane"><div><b>${lane}</b><br><span class="pill ${r.state}">${r.state}</span></div><div><span class="mut">${esc(r.note)}</span>${r.log?` <a data-do="file" data-path="${esc(r.log)}">last log</a>`:''}`;
  if(r.question)h+=`<div class="q">${esc(r.question)}</div><textarea id="a-${lane}" placeholder="Your answer. It goes into the lane's next task message."></textarea><button class="p" data-do="cmd" data-cmd="answer" data-lane="${lane}" data-text="a-${lane}">Answer and unblock</button>`;
  if(r.state==='failed')h+=`<div class="row"><button data-do="cmd" data-cmd="retry" data-lane="${lane}">Retry with a fresh budget</button></div>`;
  for(const [repo,x] of Object.entries(r.repos||{})){
   h+=`<div class="row">${esc(repo)}: <span class="pill ${x.state}">${x.state}</span>${x.log?` <a data-do="file" data-path="${esc(x.log)}">last log</a>`:''}${x.state==='failed'?` <button data-do="cmd" data-cmd="retry" data-lane="dev" data-repo="${esc(repo)}">Retry</button>`:''}</div>`;
   if(x.state==='blocked')h+=`<div class="q">${esc(x.question||'(blocked; see its last log)')}</div><textarea id="a-${lane}-${repo}"></textarea><button class="p" data-do="cmd" data-cmd="answer" data-lane="${lane}" data-repo="${esc(repo)}" data-text="a-${lane}-${repo}">Answer and unblock</button>`}
  h+='</div></div>'}
 let hb='';const g=s.gates||{};const ctx=(s.artifacts['ticket-context.md']||{}).status;
 if(ctx==='draft'||ctx==='missing')hb+=`<button class="p" data-do="cmd" data-cmd="ready">Mark ticket context ready</button> `;
 for(const [name,x] of Object.entries(g))if(x.state==='awaiting_approval')hb+=`<div class="row"><b>${name} gate</b> ${x.artifacts.map(a=>`<a data-do="file" data-path="artifacts/${a}">${a}</a>`).join(', ')}
   <button class="p" data-do="cmd" data-cmd="approve" data-gate="${name}">Approve</button><input type="text" id="rj-${name}" placeholder="reason to send back" size="24"><button class="d" data-do="cmd" data-cmd="reject" data-gate="${name}" data-text="rj-${name}">Reject</button></div>`;
 if(s.status==='Awaiting acceptance')hb+=`<button class="p" data-do="cmd" data-cmd="accept">Accept the ticket</button>`;
 if(hb)h+=`<div class="lane"><div><b>You</b></div><div>${hb}</div></div>`;
 h+=`<div class="files" style="margin-top:8px"><b>Files</b> ${s.files.map(f=>`<a data-do="file" data-path="${esc(f)}">${esc(f.replace('artifacts/',''))}</a>`).join('')||'<span class="mut">none yet</span>'}</div><pre id="view" hidden></pre></div>`;
 $('detail').innerHTML=h}
async function refresh(force){
 if(!force&&typing())return;
 board=await api('/api/board');renderTeam();renderTickets();renderTasks();if(!askLane)renderAsk();
 if(cur){const s=await api('/api/state?ticket='+cur);if(!s.error)renderDetail(s)}else $('detail').innerHTML=''}
refresh(true);setInterval(refresh,3000);
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}"
    print(f"Team workflow UI on {url}  (Ctrl+C to stop)")
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
