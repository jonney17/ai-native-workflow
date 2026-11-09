#!/usr/bin/env python3
"""Show where a work item stands. READ-ONLY: it never changes any file.

It reads tickets/<ID>/run-<N>/manifest.yaml and config/agents.yaml, then derives:
  - the effective status of every artifact, including staleness (from recorded hashes),
  - the state of every agent: waiting, ready, working, done, blocked, stale, failed,
  - the ticket status, and the next actions (who or what moves the ticket forward).

Usage:
  python scripts/wf_status.py PROJ-123              # latest run
  python scripts/wf_status.py PROJ-123 --run 2
  python scripts/wf_status.py --all                 # one line per work item
  python scripts/wf_status.py PROJ-123 --json
  python scripts/wf_status.py --hash tickets/PROJ-123/run-1/artifacts/design.md

--hash prints the short hash to record in the manifest (`hash:` of an artifact, or its `inputs:`).
Quote it in YAML, for example hash: "0123abcd", so it is never read as a number.

Until the orchestrator exists, the manifest is edited by hand: this script reads it, it does not write it.
Needs PyYAML: pip install -r requirements.txt (the other scripts use only the standard library).
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

try:
    import yaml
except ImportError:
    sys.exit("error: wf_status.py needs PyYAML. Install it with: python -m pip install -r requirements.txt\n(the other scripts use only the standard library)")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wf_config as C  # noqa: E402
OK = ("ready", "approved")
ART_STATUSES = ("missing", "draft", "ready", "approved", "rejected", "stale")


class StatusError(Exception):
    pass


# ---------------------------------------------------------------- reading

def load_yaml(path):
    # A worker may be replacing the manifest right now (it writes atomically, but Windows can briefly refuse
    # a read), so retry a few times before reporting a problem.
    for attempt in range(6):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            break
        except (OSError, yaml.YAMLError) as e:
            if attempt == 5:
                raise StatusError(f"cannot read {path.name}: {e}")
            time.sleep(0.1)
    return data if isinstance(data, dict) else {}


def latest_run(ticket_dir):
    runs = [int(m.group(1)) for p in ticket_dir.glob("run-*")
            if p.is_dir() and (m := re.fullmatch(r"run-(\d+)", p.name))]
    return max(runs) if runs else None


def sha256_path(p):
    h = hashlib.sha256()
    if p.is_dir():
        for f in sorted(x for x in p.rglob("*") if x.is_file()):
            h.update(f.relative_to(p).as_posix().encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    else:
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def same_hash(a, b):
    a, b = str(a).lower(), str(b).lower()
    n = min(len(a), len(b))
    return n > 0 and a[:n] == b[:n]


def artifact_path(run_dir, name):
    for cand in (run_dir / "artifacts" / name, run_dir / name):
        if cand.exists():
            return cand
    return None


def migrate_manifest(m):
    """Manifests written before the rename keep the agents under `lanes:`."""
    if isinstance(m, dict) and "lanes" in m and "agents" not in m:
        m["agents"] = m.pop("lanes")
    return m


def read_events(path):
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t", 3)
        if len(parts) < 3:
            continue
        detail = {}
        if len(parts) == 4:
            try:
                detail = json.loads(parts[3])
            except json.JSONDecodeError:
                detail = {"raw": parts[3]}
        ev = parts[2]
        if ev.startswith("lane_"):   # events logged before lanes were renamed to agents
            ev = "agent_" + ev[len("lane_"):]
        if isinstance(detail, dict) and "lane" in detail and "agent" not in detail:
            detail["agent"] = detail["lane"]
        events.append({"time": parts[0], "actor": parts[1], "event": ev, "detail": detail})
    return events


def proposal_id(e):
    """Stable id of a proposed event, used by the orchestrator side to record that it applied it."""
    return hashlib.sha1((e["time"] + e["actor"] + e["event"] + json.dumps(e["detail"], sort_keys=True)).encode()).hexdigest()[:10]


def sessions(run_dir, events):
    out = {}
    for e in events:
        if e["event"] != "agent_started":
            continue
        d = e["detail"]
        agent, repo, att = d.get("agent"), d.get("repo") or "", d.get("attempt") or 1
        out.setdefault(agent, []).append({"repo": repo, "attempt": att})
    return out


# ---------------------------------------------------------------- contracts (from config/agents.yaml, see wf_config.py)

def config():
    return C.load()


def proposal_names(cfg=None):
    """Events an agent may propose: the built-in ones and the ones under `events` in config/agents.yaml."""
    return tuple(C.BUILTIN_EVENTS) + tuple(n for n in (cfg or config()).events() if n not in C.BUILTIN_EVENTS)


PROPOSALS = ("question_raised", "design_gap", "verify_failed", "review_rejected", "escalate")   # default set; see proposal_names()


def agent_contract(cfg, kind, agent, known=None):
    """(produces, requires [(key, level)], gate, agent) of one agent for this kind."""
    l = cfg.agent(kind, agent)
    return l["produces"], l["requires"], l["gate"], l


# ---------------------------------------------------------------- artifacts

def stale_reason(run_dir, arts, entry, status, own_name):
    if status not in OK:
        return None
    for inp, rec in (entry.get("inputs") or {}).items():
        cur = None
        p = artifact_path(run_dir, str(inp))
        if p is not None:
            cur = sha256_path(p)
        else:
            other = arts.get(inp)
            if isinstance(other, dict) and other.get("hash"):
                cur = str(other["hash"])
        if cur and not same_hash(cur, rec):
            return f"input {inp} changed (recorded {str(rec)[:8]}, now {cur[:8]})"
    own = entry.get("hash")
    if own:
        p = artifact_path(run_dir, own_name)
        if p is not None and not same_hash(sha256_path(p), own):
            return f"{own_name} was edited after its hash was recorded"
    return None


def aggregate_repos(v):
    sts = [r["status"] for r in v["repos"].values()]
    if not sts:
        return
    if any(s == "stale" for s in sts):
        v["status"] = "stale"
    elif any(s == "rejected" for s in sts):
        v["status"] = "rejected"
    elif all(s == "approved" for s in sts):
        v["status"] = "approved"
    elif all(s in OK for s in sts):
        v["status"] = "ready"
    elif any(s == "draft" or s in OK for s in sts):
        v["status"] = "draft"
    else:
        v["status"] = "missing"


def build_view(run_dir, manifest, cfg, kind, agent_names=None, warnings=None):
    """Every artifact of this kind (from the config) with its effective status; per-repo artifacts carry `repos`."""
    warnings = [] if warnings is None else warnings
    arts = manifest.get("artifacts") or {}
    known = cfg.artifacts(kind)
    view = {}
    for name in list(known) + [n for n in arts if n not in known]:
        a = arts.get(name) if isinstance(arts.get(name), dict) else {}
        st = str(a.get("status", "missing"))
        if st not in ART_STATUSES:
            warnings.append(f"artifact {name}: unknown status {st!r} (treated as missing)")
            st = "missing"
        per = (known.get(name) or {}).get("per_repo") or "per_repo" in a
        if name not in known and st == "missing":
            continue     # left over in an old manifest and never written: not part of this config
        view[name] = {"status": st, "recorded": st, "reason": None, "entry": a, "repos": {}, "per_repo": bool(per),
                      "owner": (known.get(name) or {}).get("owner") or a.get("owner") or "?", "extra": name not in known}
        if per:
            for repo, e in (a.get("per_repo") or {}).items():
                e = e if isinstance(e, dict) else {"status": e}
                rs = str(e.get("status", "missing"))
                if rs not in ART_STATUSES:
                    warnings.append(f"{name}/{repo}: unknown status {rs!r} (treated as missing)")
                    rs = "missing"
                view[name]["repos"][str(repo)] = {"status": rs, "recorded": rs, "reason": None, "entry": e}

    for name, v in view.items():     # staleness from recorded hashes
        if v["per_repo"]:
            for repo, r in v["repos"].items():
                reason = stale_reason(run_dir, arts, r["entry"], r["status"], f"{name}/{repo}")
                if reason:
                    r["status"], r["reason"] = "stale", reason
            aggregate_repos(v)
            continue
        reason = stale_reason(run_dir, arts, v["entry"], v["status"], name)
        if reason:
            v["status"], v["reason"] = "stale", reason

    deps = {k: set(cfg.inputs(kind, k)) for k in view}     # staleness propagates along what each artifact is built from
    for _ in range(len(view) + 1):
        changed = False
        for p, ds in deps.items():
            v = view.get(p)
            src = next((d for d in sorted(ds) if d in view and d != p and view[d]["status"] == "stale"), None)
            if v is None or src is None:
                continue
            why = f"built on {src}, which is stale"
            if v["per_repo"] and v["repos"]:
                for r in v["repos"].values():
                    if r["status"] in OK:
                        r["status"], r["reason"] = "stale", why
                        changed = True
                aggregate_repos(v)
            elif v["status"] in OK:
                v["status"], v["reason"] = "stale", why
                changed = True
        if not changed:
            break

    for name, v in view.items():     # sanity checks on what a human wrote
        if v["per_repo"]:
            for repo, r in v["repos"].items():
                if r["recorded"] in OK and artifact_path(run_dir, f"{name}/{repo}") is None:
                    warnings.append(f"{name}/{repo} is marked {r['recorded']} but no folder was found under artifacts/")
            continue
        if v["recorded"] in OK and artifact_path(run_dir, name) is None:
            warnings.append(f"{name} is marked {v['recorded']} but no file was found under artifacts/")
        if v["recorded"] == "approved" and not v["entry"].get("approved_by"):
            warnings.append(f"{name} is approved but has no approved_by")
    return view


def ticket_repos(manifest, view):
    """The repos of this run: `repos` in the manifest (set at the gate with sets_repos_from), plus any found per repo."""
    out = [str(r) for r in (manifest.get("repos") or [])]
    for name, ent in (manifest.get("agents") or {}).items():     # older runs kept them under agents.<fan-out agent>.repos
        if isinstance(ent, dict) and isinstance(ent.get("repos"), dict):
            out += [str(r) for r in ent["repos"] if str(r) not in out]
    for v in view.values():
        if v["per_repo"]:
            out += [r for r in v["repos"] if r not in out]
    return out


# ---------------------------------------------------------------- agents and gates

def unmet_requirements(view, req, repos):
    unmet = []
    for key, level in req:
        v = view.get(key)
        if v is None:
            unmet.append(f"{key} (not an artifact of this run)")
            continue
        need = ("approved",) if level == "approved" else OK
        if v["per_repo"]:
            if not repos:
                unmet.append(f"{key} (no repos listed for this run yet)")
                continue
            bad = [r for r in repos if (v["repos"].get(r) or {}).get("status") not in need]
            if bad:
                unmet.append(f"{key} from " + ", ".join(bad))
            continue
        if v["status"] not in need:
            label = f"{key}@approved" if level == "approved" else key
            unmet.append(f"{label} (is {v['status']})")
    return unmet


def gate_decision(manifest, agent):
    """The risk decision for a `when: risk_flagged` gate (copied from the agent's report): True, False or None."""
    g = ((manifest.get("gates") or {}).get(agent) or {}).get("human_required")
    if g is None and agent == "reviewer":
        g = (manifest.get("review") or {}).get("human_required")    # older runs
    return g


def gate_state(agent, gate, view, manifest):
    if not gate:
        return None
    keys = [k for k in gate.get("artifacts") or [] if k in view]
    if not keys:
        return None
    sts = [view[k]["status"] for k in keys]
    if all(s == "approved" for s in sts):
        return {"state": "approved", "artifacts": keys}
    if any(s == "rejected" for s in sts):
        return {"state": "rejected", "artifacts": keys}
    if all(s in OK for s in sts):
        if gate.get("when") == "risk_flagged":
            human = gate_decision(manifest, agent)
            if human is False:
                return {"state": "not_required", "artifacts": keys}
            if human is None:
                return {"state": "needs_risk_decision", "artifacts": keys}
        return {"state": "awaiting_approval", "artifacts": keys}
    return {"state": "open", "artifacts": keys}


def is_accepted(kind, view, manifest, cfg=None, gates=None):
    acc = manifest.get("acceptance") or {}
    if acc.get("ticket_accepted") is True or acc.get("accepted") is True:
        return True
    return False


def compute_agents(cfg, kind, agent_names, view, manifest, repos, tele, warnings):
    magents = manifest.get("agents") or {}
    agents = {}
    for agent in agent_names:
        l = cfg.agent(kind, agent)
        pk, req = l["produces"], l["requires"]
        ment = magents.get(agent) if isinstance(magents.get(agent), dict) else {}
        mstat = str(ment.get("status", ""))
        unmet = unmet_requirements(view, req, repos)
        rec = {"state": "waiting", "note": "", "unmet": unmet, "rework": False, "repos": {}, "launch": [],
               "title": l["title"], "stage": l["stage"], "fan_out": l["fan_out"]}
        if ment.get("enabled") is False:
            rec.update(state="disabled", note="disabled in the manifest (set enabled: true to run it)")
        elif l["fan_out"]:
            compute_fan_out(rec, view, ment, unmet, repos, pk)
        else:
            sts = [view[k]["status"] for k in pk if k in view]
            rework = any(s == "rejected" for s in sts)
            if mstat == "failed":
                rec["state"] = "failed"
            elif mstat == "blocked":
                rec["state"] = "blocked"
            elif rework:
                rec["state"], rec["rework"] = ("waiting" if unmet else "ready"), True
            elif sts and any(s == "stale" for s in sts):
                rec["state"] = "stale"
            elif sts and all(s in OK for s in sts):
                rec["state"] = "done"
            elif mstat == "working" or any(s == "draft" or s in OK for s in sts):
                rec["state"] = "working"
            else:
                rec["state"] = "waiting" if unmet else "ready"
            why = [view[k]["reason"] for k in pk if k in view and view[k]["reason"]]
            rec["note"] = agent_note(rec, unmet, why, rework)
            if mstat == "done" and rec["state"] != "done":
                warnings.append(f"manifest says agent {agent} is done, but its artifacts say {rec['state']}")
        agents[agent] = rec
    return agents


def agent_note(rec, unmet, why, rework):
    s = rec["state"]
    if s == "waiting":
        return "needs: " + "; ".join(unmet)
    if s == "ready":
        return "rework: an artifact was rejected" if rework else "can start"
    if s == "stale":
        return "; ".join(why) if why else "an input changed"
    if s == "working":
        return "has draft or partial artifacts"
    if s == "blocked":
        return "blocked (per manifest), waiting for an answer or a dependency"
    if s == "failed":
        return "failed (per manifest), escalate to a human"
    return ""


def compute_fan_out(rec, view, ment, unmet, repos, pk):
    """An agent with one instance per repo: each repo's state comes from its folder of the agent's per-repo artifacts."""
    mrepos = ment.get("repos") if isinstance(ment.get("repos"), dict) else {}
    if not repos:
        rec["state"] = "waiting" if unmet else "ready"
        rec["note"] = ("needs: " + "; ".join(unmet)) if unmet else "ready, but this run has no repos yet (they are copied at the gate with sets_repos_from)"
        return
    for r in repos:
        rvs = [(view[k]["repos"].get(r) or {"status": "missing", "reason": None}) for k in pk if k in view]
        sts = [x["status"] for x in rvs]
        ms = str(mrepos.get(r, ""))
        rework = False
        if ms in ("failed", "blocked"):
            st = ms
        elif any(s == "rejected" for s in sts):
            st, rework = ("waiting" if unmet else "ready"), True
        elif any(s == "stale" for s in sts):
            st = "stale"
        elif sts and all(s in OK for s in sts):
            st = "done"
        elif any(s == "draft" for s in sts) or ms == "working":
            st = "working"
        else:
            st = "waiting" if unmet else "ready"
        reason = next((x.get("reason") for x in rvs if x.get("reason")), None)
        note = {"waiting": "needs: " + "; ".join(unmet), "ready": "rework: its output was rejected" if rework else "can start",
                "stale": reason or "an input changed", "failed": "failed (per manifest)",
                "blocked": "blocked (per manifest)"}.get(st, "")
        rec["repos"][r] = {"state": st, "note": note, "rework": rework}
    states = [x["state"] for x in rec["repos"].values()]
    for s in ("failed", "blocked", "stale"):
        if s in states:
            rec["state"] = s
            break
    else:
        if all(s == "done" for s in states):
            rec["state"] = "done"
        elif any(s in ("working", "done") for s in states):
            rec["state"] = "working"
        elif "ready" in states:
            rec["state"] = "ready"
        else:
            rec["state"] = "waiting"
    rec["note"] = ", ".join(f"{r}: {x['state']}" for r, x in rec["repos"].items())
    if rec["state"] == "waiting" and unmet:
        rec["note"] = "needs: " + "; ".join(unmet)


# ---------------------------------------------------------------- ticket status

def derive_ticket(cfg, kind, agents, gates, view, manifest, repos, relrepos):
    """Generic: escalated, needs input, done, a gate waiting for a person, the first unfinished agent's stage,
    then deploy and acceptance."""
    failed = [l for l, v in agents.items() if v["state"] == "failed"]
    rfail = [r for r, v in relrepos.items() if v == "failed"]
    if failed or rfail:
        bits = []
        if failed:
            bits.append("agent failed: " + ", ".join(failed))
        if rfail:
            bits.append("release failed in " + ", ".join(rfail) + " (partial release: stop, record what is merged or deployed)")
        return "Escalated", "; ".join(bits)
    blocked = [l for l, v in agents.items() if v["state"] == "blocked"]
    if blocked:
        return "Needs input", ", ".join(blocked) + " blocked on a question or dependency"
    if is_accepted(kind, view, manifest, cfg, gates):
        return "Done", "accepted"
    done = cfg.done_when(kind)

    def title(l):
        return agents[l]["title"]

    for l, g in gates.items():
        if g["state"] == "awaiting_approval" and agents.get(l, {}).get("state") == "done":
            return "Awaiting approval", f"{title(l)}: {', '.join(g['artifacts'])} ready for a person to approve or reject"
        if g["state"] == "needs_risk_decision":
            return agents[l]["stage"], f"{title(l)}: the risk decision is missing (human_required in {g['artifacts'][0]})"
    open_agents = [l for l, v in agents.items() if v["state"] not in ("done", "disabled")]
    if open_agents:
        first = open_agents[0]
        parts = [f"{title(l)} {agents[l]['note'] or agents[l]['state']}" if agents[l]["fan_out"] else f"{title(l)} {agents[l]['state']}"
                 for l in open_agents if agents[l]["state"] != "waiting" or l == first]
        g = gates.get(first) or {}
        if g.get("state") == "rejected" or agents[first].get("rework"):
            parts.insert(0, "rework: work was rejected")
        return agents[first]["stage"], "; ".join(parts)
    pend = [l for l, g in gates.items() if g["state"] not in ("approved", "not_required")]
    if pend:
        return agents[pend[0]]["stage"], f"{title(pend[0])} gate is {gates[pend[0]]['state']}"
    need = unmet_requirements(view, done["artifacts"], repos)
    if need:
        return "Finishing", "done when: " + "; ".join(need)
    if done["deploy"] and repos and any(relrepos.get(r) != "deployed" for r in repos):
        ok = [r for r in repos if relrepos.get(r) == "deployed"]
        return "Deploying", f"every agent is done; deployed {len(ok)} of {len(repos)} repos"
    if done["person_accepts"]:
        return "Awaiting acceptance", "a person accepts the ticket"
    return "Done", "every agent done, every gate passed" + (" and deployed" if done["deploy"] and repos else "")


# ---------------------------------------------------------------- next actions

def next_actions(ticket, run, kind, agents, gates, view, manifest, repos, relrepos, tele, status, tasks_hint, cfg):
    acts = []
    for a in cfg.system_artifacts():
        if a in view and view[a]["status"] not in OK:
            acts.append(("human", f"Fill artifacts/{a}, then: python scripts/wf_run.py ready --ticket {ticket} --artifact {a} (it is {view[a]['status']})"))
    for agent, rec in agents.items():
        s = rec["state"]
        if rec["fan_out"] and rec["repos"]:
            for r, x in rec["repos"].items():
                if x["state"] in ("ready", "stale") and not rec["unmet"]:
                    acts.append(("agent", ("Re-run " if x["state"] == "stale" else "Run ") + f"{agent} for {r} (the {agent} worker starts it)"))
        elif rec["fan_out"] and s == "ready":
            hint = f" (it mentions: {', '.join(tasks_hint)})" if tasks_hint else ""
            acts.append(("human", f"{agent} needs the run's repos: approve the gate that copies them, or list them under repos: in the manifest" + hint))
        elif s in ("ready", "stale") and not rec["unmet"]:
            acts.append(("agent", f"{'Re-run' if s == 'stale' else 'Run'} {agent} (its worker starts it)"))
    for agent, g in gates.items():
        if g["state"] == "awaiting_approval":
            acts.append(("human", f"Review {', '.join(g['artifacts'])}, then: python scripts/wf_run.py approve --ticket {ticket} --gate {agent}   (or reject --gate {agent} --reason ...)"))
        if g["state"] == "needs_risk_decision":
            acts.append(("human", f"Set human_required: true or false in {g['artifacts'][0]} (see config/risk-rules.yaml)"))
    for agent, rec in agents.items():
        if rec["state"] == "blocked":
            acts.append(("human", f"{agent} is blocked on a question (see question_raised in events.log). Answer it, then: python scripts/wf_run.py answer --ticket {ticket} --agent {agent} --text \"...\""))
    if status == "Deploying":
        order = [str(x) for x in ((manifest.get("release") or {}).get("sequence") or [])]
        mode = (manifest.get("release") or {}).get("deploy_order", "independent")
        todo = [r for r in (order if mode == "ordered" and order else repos) if relrepos.get(r) != "deployed"]
        if todo:
            tail = f" (ordered: {' -> '.join(order)})" if mode == "ordered" and order else ""
            nxt = todo[0] if mode == "ordered" else ", ".join(todo)
            acts.append(("ci/cd", f"Merge and deploy {nxt}{tail}; record it: python scripts/wf_run.py deployed --ticket {ticket} --repo <name>"))
    if status == "Awaiting acceptance":
        acts.append(("human", f"Decide acceptance (policy is TBD), then: python scripts/wf_run.py accept --ticket {ticket}"))
    if not acts and status != "Done":
        acts.append(("system", "Nothing can start yet. The Agents table says what each waiting agent needs"))
    return acts


# ---------------------------------------------------------------- analysis

def body_of(path):
    """File text without the artifact header (the --- block at the top)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2]
    return text


def shape_warnings(run_dir, manifest, view, registry, warnings):
    """Warn when a machine-read part of an artifact is missing. Read-only; templates are in skills/<name>/templates/."""
    def got(name):
        v = view.get(name)
        if not v or v["recorded"] == "missing":
            return None
        p = artifact_path(run_dir, name)
        return body_of(p) if p is not None and p.is_file() else None

    ctx = got("ticket-context.md")
    ac_ids = sorted(set(re.findall(r"^\|\s*([JC]-\d+)\s*\|", ctx, re.M)), key=lambda a: int(a[2:])) if ctx is not None else []
    tasks = got("tasks.md")
    if tasks is not None:
        uncovered = [a for a in ac_ids if not re.search(re.escape(a) + r"(?!\d)", tasks)]
        if uncovered:
            warnings.append("tasks.md: no task covers " + ", ".join(uncovered) + " (list the ticket's criterion ids in the Covers column)")
        m = re.search(r"^deploy_order:\s*(\w+)", tasks, re.M)
        if not m or m.group(1) not in ("independent", "ordered"):
            warnings.append("tasks.md: the Release section needs a line 'deploy_order: independent' or 'deploy_order: ordered'")
        elif m.group(1) == "ordered":
            sq = re.search(r"^sequence:\s*(.+)$", tasks, re.M)
            seq = [x.strip() for x in sq.group(1).split(",") if x.strip()] if sq else []
            if not seq:
                warnings.append("tasks.md: deploy_order is ordered but there is no 'sequence:' line")
            elif registry:
                unknown = [r for r in seq if r not in registry]
                if unknown:
                    warnings.append("tasks.md: sequence names repos that are not in config/repos.yaml: " + ", ".join(unknown))
    plan = got("test-plan.md")
    if plan is not None and ac_ids:
        gaps = [a for a in ac_ids if not re.search(re.escape(a) + r"(?!\d)", plan)]
        if gaps:
            warnings.append("test-plan.md: no row for " + ", ".join(gaps))
    ver = got("verify-report.md")
    if ver is not None and not re.search(r"^Result:\s*\**\s*(PASS|FAIL)", ver, re.M | re.I):
        warnings.append("verify-report.md: no 'Result: PASS' or 'Result: FAIL' line")
    rev = got("review-report.md")
    if rev is not None:
        head = [l.strip() for l in rev.splitlines() if l.strip()][:4]
        hm = next((re.fullmatch(r"human_required:\s*(true|false)", l) for l in head if l.startswith("human_required")), None)
        if not hm:
            warnings.append("review-report.md: the first lines must include 'human_required: true' or 'human_required: false'")
        else:
            decided = (manifest.get("review") or {}).get("human_required")
            if isinstance(decided, bool) and decided != (hm.group(1) == "true"):
                warnings.append(f"review.human_required in the manifest ({str(decided).lower()}) differs from review-report.md ({hm.group(1)})")
        if not re.search(r"^Verdict:\s*(pass|rework|design_rejected)\b", rev, re.M | re.I):
            warnings.append("review-report.md: no 'Verdict: pass|rework|design_rejected' line")


def analyze(ticket, run=None):
    tdir = ROOT / "tickets" / ticket
    if not tdir.is_dir():
        raise StatusError(f"no such work item: tickets/{ticket} (create it with scripts/new_ticket.py)")
    if run is None:
        run = latest_run(tdir)
        if run is None:
            raise StatusError(f"{ticket} has no run-N folder")
    run_dir = tdir / f"run-{run}"
    if not (run_dir / "manifest.yaml").exists():
        raise StatusError(f"tickets/{ticket}/run-{run}/manifest.yaml not found")
    manifest = migrate_manifest(load_yaml(run_dir / "manifest.yaml"))
    if not C.config_path().exists():
        raise StatusError("config/agents.yaml not found")
    cfg = config()

    kind = str(manifest.get("kind") or "story")
    if kind not in cfg.kinds():
        raise StatusError(f"unknown kind {kind!r} (config/agents.yaml has: {', '.join(cfg.kinds())})")
    agent_names = cfg.kind_agents(kind)
    warnings = []
    view = build_view(run_dir, manifest, cfg, kind, agent_names, warnings)
    repos = ticket_repos(manifest, view)
    for v in view.values():
        if v["per_repo"]:
            for r in repos:
                v["repos"].setdefault(r, {"status": "missing", "recorded": "missing", "reason": None, "entry": {}})
            aggregate_repos(v)

    events = read_events(run_dir / "events.log")
    tele = sessions(run_dir, events)
    agents = compute_agents(cfg, kind, agent_names, view, manifest, repos, tele, warnings)
    gates = {}
    for agent in agent_names:
        g = gate_state(agent, cfg.agent(kind, agent)["gate"], view, manifest)
        if g:
            gates[agent] = g

    rel = manifest.get("release") if isinstance(manifest.get("release"), dict) else {}
    relrepos = {str(k): str(v) for k, v in (rel.get("repos") or {}).items()}
    status, detail = derive_ticket(cfg, kind, agents, gates, view, manifest, repos, relrepos)

    registry = []
    rp = ROOT / "config" / "repos.yaml"
    if rp.exists():
        registry = list((load_yaml(rp).get("repos") or {}))
    for r in repos:
        if registry and r not in registry:
            warnings.append(f"repo {r} is not in config/repos.yaml")
    shape_warnings(run_dir, manifest, view, registry, warnings)
    tasks_hint = []
    src = next((l["gate"]["sets_repos_from"] for l in cfg.agents(kind) if l["gate"] and l["gate"].get("sets_repos_from")), None)
    tp = artifact_path(run_dir, src) if src else None
    if tp is not None and registry and tp.is_file():
        text = tp.read_text(encoding="utf-8", errors="replace")
        tasks_hint = [n for n in registry if re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", text)]

    actions = next_actions(ticket, run, kind, agents, gates, view, manifest, repos, relrepos, tele, status, tasks_hint, cfg)
    applied = {e["detail"].get("id") for e in events if e["event"] == "proposal_applied"}
    names = proposal_names(cfg)
    proposals = [e for e in events if e["event"] in names and proposal_id(e) not in applied][-5:]

    arts_out = {}
    for name, v in view.items():
        item = {"status": v["status"], "recorded": v["recorded"], "reason": v["reason"], "owner": v["owner"], "per_repo": v["per_repo"]}
        if v["per_repo"]:
            item["repos"] = {r: {"status": x["status"], "recorded": x["recorded"], "reason": x["reason"]} for r, x in v["repos"].items()}
        arts_out[name] = item
    return {"ticket": ticket, "run": run, "kind": kind, "path": run_dir.relative_to(ROOT).as_posix(),
            "status": status, "detail": detail, "agents": agents, "gates": gates, "artifacts": arts_out, "repos": repos,
            "release": rel, "next": [{"who": w, "text": t} for w, t in actions],
            "proposals": [{"time": e["time"], "actor": e["actor"], "event": e["event"], "detail": e["detail"]} for e in proposals],
            "warnings": warnings}


# ---------------------------------------------------------------- output

def render(res):
    L = [f"{res['ticket']}  run {res['run']}  {res['kind']}   ({res['path']})",
         f"Status: {res['status'].upper()}  -  {res['detail']}", "",
         "Agents  (derived from artifacts; the manifest's agent status only counts for working, blocked and failed)"]
    for name, ln in res["agents"].items():
        L.append(f"  {name:<14} {ln['state']:<9} {ln['note']}".rstrip())
        for repo, x in ln["repos"].items():
            L.append(f"    {repo:<12} {x['state']:<9} {x['note']}".rstrip())
    L += ["", "Artifacts"]
    for name, a in res["artifacts"].items():
        if a.get("per_repo") and a.get("repos"):
            for repo, x in a["repos"].items():
                L.append(f"  {name + '/' + repo:<22} {_art(x)}")
        else:
            L.append(f"  {name:<22} {_art(a)}")
    L += ["", "Next"]
    for a in res["next"]:
        L.append(f"  [{a['who']}] {a['text']}")
    if res["proposals"]:
        L += ["", "Proposed events (nothing applies them yet: act on them by hand, then record the outcome in decisions.md)"]
        for p in res["proposals"]:
            d = json.dumps(p["detail"], sort_keys=True)
            L.append(f"  {p['time']}  {p['actor']}  {p['event']}  {d[:100]}")
    if res["warnings"]:
        L += ["", "Warnings"]
        L += [f"  - {w}" for w in res["warnings"]]
    return "\n".join(L)


def _art(a):
    s = a["status"]
    if a["recorded"] != s:
        s += f"  (manifest says {a['recorded']})"
    if a["reason"]:
        s += f"  - {a['reason']}"
    return s


def list_all():
    base = ROOT / "tickets"
    rows = []
    for d in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")) if base.is_dir() else []:
        try:
            r = analyze(d.name)
            rows.append((r["ticket"], r["kind"], r["run"], r["status"], r["detail"], len(r["warnings"])))
        except StatusError as e:
            rows.append((d.name, "?", "?", "Error", str(e), 0))
    if not rows:
        print("no work items yet (create one with scripts/new_ticket.py)")
        return
    for t, k, n, s, d, w in rows:
        print(f"{t:<14} {k:<6} run {n!s:<3} {s:<24} {d}" + (f"  [{w} warning(s)]" if w else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticket", nargs="?", help="work item id, for example PROJ-123")
    ap.add_argument("--ticket", dest="ticket_opt", help="same as the positional argument")
    ap.add_argument("--run", type=int, default=None, help="default: the latest run")
    ap.add_argument("--all", action="store_true", help="one line per work item")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--hash", metavar="PATH", help="print the short hash of a file or folder")
    args = ap.parse_args()

    if args.hash:
        p = Path(args.hash)
        if not p.exists():
            sys.exit(f"error: {p} not found")
        print(sha256_path(p)[:8])
        print("quote it in YAML, for example hash: \"" + sha256_path(p)[:8] + "\"", file=sys.stderr)
        return
    if args.all:
        list_all()
        return
    ticket = args.ticket or args.ticket_opt
    if not ticket:
        ap.error("give a work item id, or use --all")
    try:
        res = analyze(ticket, args.run)
    except StatusError as e:
        sys.exit(f"error: {e}")
    print(json.dumps(res, indent=2, default=str) if args.json else render(res))


if __name__ == "__main__":
    main()
