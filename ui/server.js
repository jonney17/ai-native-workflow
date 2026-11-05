#!/usr/bin/env node
// Web UI server for the team workflow. Plain Node (18+), no packages.
//   node ui/server.js [--port 8765] [--no-browser]
// Static files come from ui/. Everything about runs and lanes comes from scripts/wf_api.py (JSON).
// Talking to a lane outside a ticket goes through scripts/wf_chat.py (one long-lived agent session per lane).
// Listens on 127.0.0.1 only.
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn, spawnSync, execFile } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const argv = process.argv.slice(2);
const PORT = Number(argv[argv.indexOf('--port') + 1]) || 8765;
const PY = process.env.WF_PYTHON || (spawnSync('python3', ['--version']).status === 0 ? 'python3' : 'python');
const NAME = /^[A-Za-z0-9._-]+$/;
const LANES = ['planner', 'dev', 'qe', 'reviewer', 'inspect-adapt'];
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };

// ---- python helper: one JSON document per call
function api(args, stdin) {
  return new Promise((resolve) => {
    const p = execFile(PY, [path.join(ROOT, 'scripts', 'wf_api.py'), ...args],
      { cwd: ROOT, maxBuffer: 16 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } },
      (err, out, errout) => {
        try { resolve(JSON.parse(out)); }
        catch { resolve({ error: (errout || (err && err.message) || 'no output').slice(-600) }); }
      });
    if (stdin !== undefined) { p.stdin.write(stdin); }
    p.stdin.end();
  });
}

// ---- ticket workers (wf_run.py start), one per ticket
const workers = new Map();
const running = (t) => workers.has(t) && workers.get(t).exitCode === null;
function startWorkers(ticket, runPath, auto) {
  if (running(ticket)) return `${ticket}: workers are already running`;
  const logdir = path.join(ROOT, runPath, 'logs');
  fs.mkdirSync(logdir, { recursive: true });
  const out = fs.openSync(path.join(logdir, 'workers.log'), 'a');
  const p = spawn(PY, [path.join(ROOT, 'scripts', 'wf_run.py'), 'start', '--ticket', ticket, '--verbose', ...(auto ? ['--auto-approve'] : [])],
    { cwd: ROOT, stdio: ['ignore', out, out], env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  workers.set(ticket, p);
  return `${ticket}: the team is on it`;
}

// ---- chat with a lane: history kept here, the agent sessions live in wf_chat.py
const chats = Object.fromEntries(LANES.map((l) => [l, { messages: [], busy: false }]));
let daemon = null;
function chatDaemon() {
  if (daemon && daemon.exitCode === null) return daemon;
  daemon = spawn(PY, [path.join(ROOT, 'scripts', 'wf_chat.py')], { cwd: ROOT, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  let buf = '';
  daemon.stdout.setEncoding('utf8');
  daemon.stdout.on('data', (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (line.trim()) onChat(line); }
  });
  daemon.stderr.on('data', () => {});
  daemon.on('exit', () => { for (const c of Object.values(chats)) if (c.busy) { c.busy = false; c.messages.push({ role: 'error', text: 'the chat helper stopped' }); } });
  return daemon;
}
function onChat(line) {
  let m; try { m = JSON.parse(line); } catch { return; }
  const c = chats[m.lane]; if (!c) return;
  const last = c.messages[c.messages.length - 1];
  if (m.type === 'chunk') {
    if (last && last.role === 'agent' && c.busy) last.text += m.text; else c.messages.push({ role: 'agent', text: m.text });
  } else if (m.type === 'tool') { c.messages.push({ role: 'tool', text: m.text }); }
  else if (m.type === 'turn_end') { c.busy = false; }
  else if (m.type === 'error') { c.busy = false; c.messages.push({ role: 'error', text: m.text }); }
}
function chatSend(lane, text) {
  const c = chats[lane];
  if (!c) return 'bad lane';
  if (c.busy) return `${lane} is still answering`;
  c.messages.push({ role: 'user', text }); c.busy = true;
  chatDaemon().stdin.write(JSON.stringify({ op: 'send', lane, text }) + '\n');
  return '';
}

// ---- http
function send(res, body, type = 'application/json', code = 200) {
  const data = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(code, { 'Content-Type': type + '; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(data);
}
function readBody(req) {
  return new Promise((resolve) => { let b = ''; req.on('data', (d) => (b += d)); req.on('end', () => { try { resolve(JSON.parse(b || '{}')); } catch { resolve({}); } }); });
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://x');
  const q = Object.fromEntries(u.searchParams);
  try {
    if (req.method === 'GET') {
      if (u.pathname === '/' || u.pathname === '/index.html' || u.pathname === '/app.js') {
        const f = u.pathname === '/app.js' ? 'app.js' : 'index.html';
        return send(res, fs.readFileSync(path.join(__dirname, f)), MIME[path.extname(f)]);
      }
      if (u.pathname === '/api/board') return send(res, await api(['board']));
      if (u.pathname === '/api/chats') return send(res, chats);
      if (u.pathname === '/api/state' && NAME.test(q.ticket || '')) {
        const s = await api(['state', q.ticket]); if (!s.error) s.workers_running = running(q.ticket);
        return send(res, s);
      }
      if (u.pathname === '/api/file' && NAME.test(q.ticket || '')) { const r = await api(['file', q.ticket, q.path || '']); return send(res, r.text ?? r.error ?? '', 'text/plain'); }
      return send(res, 'not found', 'text/plain', 404);
    }
    const b = await readBody(req);
    const ticket = String(b.ticket || '');
    if (u.pathname === '/api/action') return send(res, await api(['act'], JSON.stringify(b)));
    if (u.pathname === '/api/start') {
      if (!NAME.test(ticket)) return send(res, { message: 'bad ticket' });
      const r = await api(['intake', ticket]);
      if (r.error) return send(res, { message: 'error: ' + r.error });
      return send(res, { message: startWorkers(ticket, r.path, !!b.auto) });
    }
    if (u.pathname === '/api/stop') {
      const p = workers.get(ticket);
      if (p && p.exitCode === null) { p.kill(); return send(res, { message: 'workers stopped' }); }
      return send(res, { message: 'no workers started from this page' });
    }
    if (u.pathname === '/api/chat') {
      const text = String(b.text || '').trim();
      if (!LANES.includes(b.lane) || !text) return send(res, { message: 'write something' });
      return send(res, { message: chatSend(b.lane, text) });
    }
    if (u.pathname === '/api/chat-reset' && LANES.includes(b.lane)) {
      chats[b.lane] = { messages: [], busy: false };
      if (daemon && daemon.exitCode === null) daemon.stdin.write(JSON.stringify({ op: 'reset', lane: b.lane }) + '\n');
      return send(res, { message: 'chat cleared' });
    }
    send(res, 'not found', 'text/plain', 404);
  } catch (e) { send(res, { message: 'error: ' + e.message }, 'application/json', 500); }
});

server.listen(PORT, '127.0.0.1', () => {
  const url = `http://127.0.0.1:${PORT}`;
  console.log(`Team workflow UI on ${url}  (Ctrl+C to stop; python: ${PY})`);
  if (!argv.includes('--no-browser')) {
    const cmd = process.platform === 'win32' ? ['cmd', ['/c', 'start', '', url]] : process.platform === 'darwin' ? ['open', [url]] : ['xdg-open', [url]];
    try { spawn(cmd[0], cmd[1], { stdio: 'ignore', detached: true }).unref(); } catch { /* the URL is printed above */ }
  }
});
const stop = () => { for (const p of workers.values()) if (p.exitCode === null) p.kill(); if (daemon) daemon.kill(); process.exit(0); };
process.on('SIGINT', stop); process.on('SIGTERM', stop);
