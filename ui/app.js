'use strict';
const $ = (id) => document.getElementById(id);
const LANES = ['planner', 'dev', 'qe', 'reviewer', 'inspect-adapt'];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const say = (m) => { $('msg').textContent = m || ''; };
let cur = '', board = null, chats = {};
const mode = {};   // lane -> {ticket, repo} when the box answers a ticket question, else null

async function api(p, b) {
  const r = await fetch(p, b ? { method: 'POST', body: JSON.stringify(b) } : {});
  return (r.headers.get('content-type') || '').includes('json') ? r.json() : r.text();
}

// ---------- one row per lane (built once; only the parts inside are updated, so typing is never lost)
function buildRows() {
  $('team').innerHTML = LANES.map((l) => `<div class="lane" id="row-${l}">
    <div class="who"><b class="t"></b><span class="pill idle">idle</span><div class="lines"></div></div>
    <div class="work">
      <div class="live" hidden><div class="livehead"></div><pre></pre></div>
      <div class="chat"></div>
      <div class="q" hidden></div>
      <form class="send" data-lane="${l}"><input type="text" autocomplete="off"><button class="p">Send</button><button type="button" class="clear" title="Start a new conversation with this lane">Clear</button></form>
    </div></div>`).join('');
  for (const l of LANES) {
    const f = document.querySelector(`#row-${l} form`);
    f.onsubmit = (e) => { e.preventDefault(); submit(l, f); };
    f.querySelector('.clear').onclick = async () => { await api('/api/chat-reset', { lane: l }); loadChats(); };
  }
}

async function submit(lane, f) {
  const input = f.querySelector('input'), text = input.value.trim();
  if (!text) return;
  input.value = '';
  const m = mode[lane];
  const r = m ? await api('/api/action', { ticket: m.ticket, cmd: 'answer', lane, repo: m.repo, text })
              : await api('/api/chat', { lane, text });
  say(r.message || '');
  m ? loadBoard() : loadChats();
}

function paintBoard() {
  if (!board || board.error) { say(board && board.error); return; }
  for (const l of LANES) {
    const d = board.lanes[l], row = $('row-' + l);
    row.querySelector('.t').textContent = d.title;
    const pill = row.querySelector('.pill');
    pill.textContent = d.status; pill.className = 'pill ' + (d.status === 'waiting for you' ? 'wfy' : d.status);
    row.querySelector('.lines').innerHTML = d.lines.map((x) => `<div class="${x.status === 'waiting for you' ? 'wfy' : ''}">` +
      (x.ticket ? `<a data-open="${esc(x.ticket)}">${esc(x.text)}</a>` : esc(x.text)) + '</div>').join('') || '<div>nothing to do</div>';
    const live = row.querySelector('.live');
    if (d.live) {
      live.hidden = false; live.querySelector('.livehead').textContent = 'live: ' + d.live.unit;
      const pre = live.querySelector('pre'), atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
      pre.textContent = d.live.text; if (atEnd) pre.scrollTop = pre.scrollHeight;
    } else live.hidden = true;
    const q = d.questions[0], qe = row.querySelector('.q'), input = row.querySelector('input');
    mode[l] = q ? { ticket: q.ticket, repo: q.repo } : null;
    qe.hidden = !q;
    if (q) qe.innerHTML = `<b>${esc(q.ticket)}${q.repo ? ' · ' + esc(q.repo) : ''} asks:</b> ${esc(q.question || '(see its last log)')}` + (d.questions.length > 1 ? ` <span class="mut">(+${d.questions.length - 1} more)</span>` : '');
    input.placeholder = q ? 'Your answer goes to this lane and it continues' : `Chat with ${d.title}. It needs: ${d.ask_for}`;
  }
  $('tickets').innerHTML = board.tickets.map((t) => `<tr><td><b>${esc(t.id)}</b></td><td>${esc(t.title)}<br><span class="mut">${esc(t.repos.join(', '))}</span></td>
    <td>${esc(t.state)}<br><span class="mut">${esc(t.detail)}</span></td>
    <td>${t.state === 'Not started' ? `<button class="p" data-start="${esc(t.id)}">Start</button>` : `<button data-open="${esc(t.id)}">Open</button>`}</td></tr>`).join('');
}

function paintChats() {
  for (const l of LANES) {
    const c = chats[l]; if (!c) continue;
    const el = document.querySelector(`#row-${l} .chat`), atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    el.innerHTML = c.messages.map((m) => `<div class="m ${m.role}">${esc(m.text)}</div>`).join('') + (c.busy ? '<div class="m tool">working…</div>' : '');
    if (atEnd) el.scrollTop = el.scrollHeight;
    const f = document.querySelector(`#row-${l} form`);
    f.querySelector('.p').disabled = !!c.busy && !mode[l];
  }
}

async function loadBoard() { board = await api('/api/board'); paintBoard(); }
async function loadChats() { chats = await api('/api/chats'); paintChats(); }

// ---------- ticket detail (gates, questions per repo, files)
const typing = () => { const a = document.activeElement; return a && (a.tagName === 'TEXTAREA' || (a.tagName === 'INPUT' && a.type === 'text' && a.closest('#detail'))); };
function renderDetail(s) {
  let h = `<h2>${esc(s.ticket)} <span class="mut" style="font-weight:400">${esc(s.status)} - ${esc(s.detail)}</span></h2><div class="box">
   <div class="row2"><button data-stop="${esc(s.ticket)}">Stop workers</button><span class="mut">workers ${s.workers_running ? 'running' : 'not started from this page'}</span></div>
   ${s.next.map((n) => '<div class="mut">• ' + esc(n.who) + ': ' + esc(n.text) + '</div>').join('')}${(s.warnings || []).map((w) => '<div class="mut">⚠ ' + esc(w) + '</div>').join('')}`;
  for (const [lane, r] of Object.entries(s.lanes)) {
    if (r.state === 'disabled') continue;
    h += `<div class="dl"><div><b>${lane}</b><br><span class="pill ${r.state}" style="margin:0">${r.state}</span></div><div><span class="mut">${esc(r.note)}</span>${r.log ? ` <a data-file="${esc(r.log)}">last log</a>` : ''}`;
    if (r.state === 'failed') h += `<div class="row2"><button data-cmd="retry" data-lane="${lane}">Retry with a fresh budget</button></div>`;
    for (const [repo, x] of Object.entries(r.repos || {})) {
      h += `<div class="row2">${esc(repo)}: <span class="pill ${x.state}" style="margin:0">${x.state}</span>${x.log ? ` <a data-file="${esc(x.log)}">last log</a>` : ''}${x.state === 'failed' ? ` <button data-cmd="retry" data-lane="dev" data-repo="${esc(repo)}">Retry</button>` : ''}</div>`;
    }
    h += '</div></div>';
  }
  let hb = ''; const g = s.gates || {}, ctx = (s.artifacts['ticket-context.md'] || {}).status;
  if (ctx === 'draft' || ctx === 'missing') hb += '<button class="p" data-cmd="ready">Mark ticket context ready</button> ';
  for (const [name, x] of Object.entries(g)) if (x.state === 'awaiting_approval') hb += `<div class="row2"><b>${name} gate</b> ${x.artifacts.map((a) => `<a data-file="artifacts/${a}">${a}</a>`).join(', ')}
    <button class="p" data-cmd="approve" data-gate="${name}">Approve</button><input type="text" id="rj-${name}" placeholder="reason to send back" size="24"><button class="d" data-cmd="reject" data-gate="${name}" data-text="rj-${name}">Reject</button></div>`;
  if (s.status === 'Awaiting acceptance') hb += '<button class="p" data-cmd="accept">Accept the ticket</button>';
  if (hb) h += `<div class="dl"><div><b>You</b></div><div>${hb}</div></div>`;
  h += `<div class="files" style="margin-top:8px"><b>Files</b> ${s.files.map((f) => `<a data-file="${esc(f)}">${esc(f.replace('artifacts/', ''))}</a>`).join('') || '<span class="mut">none yet</span>'}</div><pre id="view" hidden></pre></div>`;
  $('detail').innerHTML = h;
}
async function loadDetail(force) {
  if (!cur || (!force && typing())) return;
  const s = await api('/api/state?ticket=' + encodeURIComponent(cur));
  if (!s.error) renderDetail(s);
}

document.addEventListener('click', async (e) => {
  const t = e.target.closest('[data-open],[data-start],[data-stop],[data-cmd],[data-file]'); if (!t) return;
  const d = t.dataset;
  if (d.open) { cur = d.open; loadDetail(true); $('detail').scrollIntoView({ block: 'nearest' }); }
  else if (d.start) { say((await api('/api/start', { ticket: d.start, auto: $('auto').checked })).message); cur = d.start; loadBoard(); loadDetail(true); }
  else if (d.stop) say((await api('/api/stop', { ticket: d.stop })).message);
  else if (d.cmd) {
    const text = d.text ? $(d.text).value : '';
    say((await api('/api/action', { ticket: cur, cmd: d.cmd, lane: d.lane, repo: d.repo, gate: d.gate, text })).message);
    loadBoard(); loadDetail(true);
  } else if (d.file) { const v = $('view'); v.hidden = false; v.textContent = await api('/api/file?ticket=' + cur + '&path=' + encodeURIComponent(d.file)); }
});

buildRows(); loadBoard(); loadChats();
setInterval(loadBoard, 2500); setInterval(loadChats, 1000); setInterval(() => loadDetail(false), 3000);
