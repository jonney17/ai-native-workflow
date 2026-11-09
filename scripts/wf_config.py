#!/usr/bin/env python3
"""Read config/agents.yaml: the agents, the artifacts they need and produce, the gates and the kinds.

Every script and the playbook read agents and artifacts through this module, so adding, removing or changing a
agent is a change to config/agents.yaml (and an agent file), not to the code.

  python scripts/wf_config.py            # print the agents per kind and any problem in the config
  python scripts/wf_config.py --json
"""
import copy
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "config" / "agents.yaml"
LEGACY_PATH = ROOT / "config" / "lanes.yaml"   # the old file name; still read when agents.yaml does not exist yet


def config_path():
    return PATH if PATH.exists() or not LEGACY_PATH.exists() else LEGACY_PATH


def migrate(raw):
    """Older configs say `lanes:` where this one says `agents:` (top level, and the list inside each kind)."""
    if not isinstance(raw, dict):
        return raw
    if "lanes" in raw and "agents" not in raw:
        raw["agents"] = raw.pop("lanes")
    for k in (raw.get("kinds") or {}).values():
        if isinstance(k, dict) and "lanes" in k and "agents" not in k:
            k["agents"] = k.pop("lanes")
    return raw
BUILTIN_EVENTS = ("question_raised", "escalate")


def parse(token):
    """'tasks.md@approved' -> ('tasks.md', 'approved', False); 'changes/<repo>/' -> ('changes', None, True)."""
    name, _, level = str(token).partition("@")
    name = name.strip()
    per_repo = "<repo>" in name
    key = name.split("/")[0] if "/" in name.rstrip("/") or per_repo else name.rstrip("/")
    return key, (level.strip() or None), per_repo


class Config:
    def __init__(self, raw):
        self.raw = migrate(raw) if isinstance(raw, dict) else {}
        self.agents_raw = self.raw.get("agents") or {}
        self.kinds_raw = self.raw.get("kinds") or {}

    # ---- agents
    def agent_names(self):
        return list(self.agents_raw)

    def kinds(self):
        return list(self.kinds_raw)

    def kind(self, kind):
        k = self.kinds_raw.get(kind)
        if k is None:
            raise KeyError(f"unknown kind {kind!r} (config/agents.yaml has: {', '.join(self.kinds_raw)})")
        return k

    def kind_agents(self, kind):
        return [l for l in (self.kind(kind).get("agents") or []) if l in self.agents_raw]

    def _merged(self, kind, name):
        d = self.agents_raw.get(name)
        d = copy.deepcopy(d) if isinstance(d, dict) else {}
        over = ((self.kind(kind).get("overrides") or {}).get(name) or {}) if kind else {}
        for k, v in over.items():
            if k == "gate" and isinstance(v, dict) and isinstance(d.get("gate"), dict):
                d["gate"] = {**{x: y for x, y in d["gate"].items() if x in ("approver",)}, **v}
            else:
                d[k] = copy.deepcopy(v)
        return d

    def produced(self, kind):
        """Every artifact key that some agent of this kind produces, plus the system artifacts."""
        out = set(self.system_artifacts())
        names = self.kind_agents(kind) if kind else self.agent_names()
        for n in names:
            out.update(parse(t)[0] for t in self._merged(kind, n).get("produces") or [])
        return out

    def agent(self, kind, name):
        """The agent as it applies to this kind: its fields with the kind's overrides, normalized.
        A required artifact that no agent of this kind produces is skipped for this kind (listed in `skipped`),
        so an agent adapts when a kind leaves out the agent that would produce it."""
        d = self._merged(kind, name)
        made = self.produced(kind) if kind else None
        requires, skipped = [], []
        for t in d.get("requires") or []:
            key, level, _ = parse(t)
            if made is not None and key not in made:
                skipped.append(key)
                continue
            if (key, level) not in requires:
                requires.append((key, level))
        produces, per_repo = [], []
        for t in d.get("produces") or []:
            key, _, pr = parse(t)
            if key not in produces:
                produces.append(key)
            if pr:
                per_repo.append(key)
        gate = d.get("gate") if isinstance(d.get("gate"), dict) else None
        if gate:
            gate = dict(gate)
            gate["artifacts"] = [parse(t)[0] for t in gate.get("artifacts") or produces]
        return {"name": name, "title": str(d.get("title") or name), "stage": str(d.get("stage") or d.get("title") or name),
                "purpose": str(d.get("purpose") or ""), "ask_for": str(d.get("ask_for") or ""),
                "requires": requires, "skipped": skipped, "produces": produces, "per_repo": per_repo,
                "fan_out": d.get("fan_out") == "repo", "gate": gate, "color": d.get("color"),
                "instructions": str(d.get("instructions") or d.get("agent") or f"wf-{name}"), "model": str(d.get("model") or ""),
                "args": [str(x) for x in d.get("args") or []]}

    def agents(self, kind):
        return [self.agent(kind, n) for n in self.kind_agents(kind)]

    def fan_out_agents(self, kind):
        return [l["name"] for l in self.agents(kind) if l["fan_out"]]

    # ---- artifacts
    def system_artifacts(self):
        return [str(x) for x in self.raw.get("system_artifacts") or ["ticket-context.md"]]

    def artifacts(self, kind):
        """Ordered {key: {owner, per_repo}}: the system artifacts, then what each agent produces, in agent order."""
        out = {a: {"owner": "system", "per_repo": False} for a in self.system_artifacts()}
        for l in self.agents(kind):
            for key in l["produces"]:
                out.setdefault(key, {"owner": l["name"], "per_repo": key in l["per_repo"]})
        return out

    def per_repo_keys(self, kind):
        return {k for k, a in self.artifacts(kind).items() if a["per_repo"]}

    def producer(self, kind, key):
        a = self.artifacts(kind).get(key)
        return a["owner"] if a else None

    def inputs(self, kind, key):
        """What an artifact is built from: artifact_inputs[key], else the requires of the agent that produces it."""
        over = (self.raw.get("artifact_inputs") or {}).get(key)
        if over is not None:
            return [parse(t)[0] for t in over]
        owner = self.producer(kind, key)
        if owner in (None, "system"):
            return []
        return [k for k, _ in self.agent(kind, owner)["requires"]]

    # ---- kinds and events
    def done_when(self, kind):
        """When a ticket of this kind is Done. Always: every agent done and every gate passed. Then, from `done_when`:
        artifacts  more artifacts that must reach a status (`name@approved`, else ready)
        deploy     CI/CD deploys the run's repos first (needs repos, i.e. a gate with sets_repos_from)
        person_accepts  a person accepts the ticket at the end"""
        k = self.kind(kind)
        d = k.get("done_when") if isinstance(k.get("done_when"), dict) else {}
        legacy_accept = k.get("accept_when") != "gates_approved"
        legacy_deploy = k.get("deploy", True) is not False
        arts = []
        for t in d.get("artifacts") or []:
            key, level, _ = parse(t)
            arts.append((key, level))
        has_repos = any((l["gate"] or {}).get("sets_repos_from") for l in self.agents(kind))
        deploy = bool(d.get("deploy", legacy_deploy)) and has_repos
        return {"artifacts": arts, "deploy": deploy, "person_accepts": bool(d.get("person_accepts", legacy_accept))}

    def deploys(self, kind):
        return self.done_when(kind)["deploy"]

    def person_accepts(self, kind):
        return self.done_when(kind)["person_accepts"]

    def events(self):
        return {str(k): v for k, v in (self.raw.get("events") or {}).items() if isinstance(v, dict)}

    # ---- checks
    def problems(self):
        """Everything that would stop a run from working. Empty list = the config is fine."""
        out = []
        if not self.agents_raw:
            out.append("no agents defined")
        if not self.kinds_raw:
            out.append("no kinds defined")
        for kind in self.kinds_raw:
            try:
                names = self.kind(kind).get("agents") or []
            except KeyError:
                continue
            for n in names:
                if n not in self.agents_raw:
                    out.append(f"kind {kind}: agent {n!r} is not defined under agents")
            arts = self.artifacts(kind)
            seen = {}
            for l in self.agents(kind):
                if not l["produces"]:
                    out.append(f"{kind}/{l['name']}: produces nothing, so it can never be done")
                for key in l["produces"]:
                    if key in seen and seen[key] != l["name"]:
                        out.append(f"{kind}: {key} is produced by both {seen[key]} and {l['name']} (one writer per artifact)")
                    seen[key] = l["name"]
                    if key in self.system_artifacts():
                        out.append(f"{kind}/{l['name']}: {key} is a system artifact; an agent cannot produce it")
                for key, _ in l["requires"]:
                    if key in l["produces"]:
                        out.append(f"{kind}/{l['name']}: requires its own output {key}")
                if l["per_repo"] and not l["fan_out"]:
                    out.append(f"{kind}/{l['name']}: produces a per-repo artifact but has no fan_out: repo")
                if l["fan_out"] and any(k not in l["per_repo"] for k in l["produces"]):
                    out.append(f"{kind}/{l['name']}: a fan_out agent may only produce per-repo artifacts (name/<repo>/)")
                g = l["gate"]
                if g:
                    for key in g["artifacts"]:
                        if key not in l["produces"]:
                            out.append(f"{kind}/{l['name']}: gate artifact {key} is not produced by this agent")
                    for f in ("sets_repos_from", "feedback_to", "decision_in"):
                        if g.get(f) and parse(g[f])[0] not in arts:
                            out.append(f"{kind}/{l['name']}: gate {f} names {g[f]}, which is not an artifact of this kind")
                for key, level in l["requires"]:
                    if level == "approved":
                        owner = arts.get(key, {}).get("owner")
                        og = self.agent(kind, owner)["gate"] if owner and owner != "system" else None
                        if not og or key not in og["artifacts"]:
                            out.append(f"{kind}/{l['name']}: requires {key}@approved, but no gate approves {key}")
            # cycles: an agent cannot (indirectly) need its own output
            need = {l["name"]: {arts[k]["owner"] for k, _ in l["requires"] if k in arts} for l in self.agents(kind)}
            for start in need:
                stack, seen_l = list(need[start]), set()
                while stack:
                    x = stack.pop()
                    if x == start:
                        out.append(f"{kind}: agent {start} depends on its own output (a cycle)")
                        break
                    if x in seen_l or x not in need:
                        continue
                    seen_l.add(x)
                    stack.extend(need[x])
            if any(self.agent(kind, n)["fan_out"] for n in self.kind_agents(kind)) and not any(
                    (l["gate"] or {}).get("sets_repos_from") for l in self.agents(kind)):
                out.append(f"{kind}: a fan_out agent needs repos, but no gate has sets_repos_from")
        anywhere = set(self.system_artifacts())
        for k in self.kinds_raw:
            anywhere |= self.produced(k)
        anywhere |= self.produced("")
        for n in self.agent_names():
            for t in (self.agents_raw.get(n) or {}).get("requires") or [] if isinstance(self.agents_raw.get(n), dict) else []:
                if parse(t)[0] not in anywhere:
                    out.append(f"{n}: requires {parse(t)[0]}, which no agent produces")
        for k in self.kinds_raw:
            try:
                made = self.produced(k)
                d = self.kind(k).get("done_when") if isinstance(self.kind(k).get("done_when"), dict) else {}
            except KeyError:
                continue
            for t in d.get("artifacts") or []:
                if parse(t)[0] not in made:
                    out.append(f"{k}: done when {parse(t)[0]} ..., but no agent of this kind produces it")
            if d.get("deploy") and not any((l["gate"] or {}).get("sets_repos_from") for l in self.agents(k)):
                out.append(f"{k}: deploy needs the run's repos, but no gate copies them (sets_repos_from)")
        for ev, rule in self.events().items():
            for n in rule.get("from") or []:
                if n not in self.agents_raw:
                    out.append(f"events.{ev}: from names unknown agent {n!r}")
        return out

    def notes(self):
        """Not problems: what adapts per kind (a required artifact that a kind does not produce is skipped)."""
        out = []
        for k in self.kinds():
            for l in self.agents(k):
                if l["skipped"]:
                    out.append(f"{k}: {l['name']} does not wait for {', '.join(l['skipped'])} (no agent of this kind produces it)")
        return out

    def summary(self):
        """A plain view of the config for the UI."""
        return {"notes": self.notes(),"agents": {n: {k: v for k, v in self.agent("", n).items()} for n in self.agent_names()},
                "kinds": {k: {"agents": self.kind_agents(k), "done_when": self.done_when(k),
                              "artifacts": self.artifacts(k),
                              "inputs": {a: self.inputs(k, a) for a in self.artifacts(k)},
                              "agent_views": {n: self.agent(k, n) for n in self.kind_agents(k)}} for k in self.kinds()},
                "problems": self.problems()}


def load(path=None):
    p = Path(path) if path else config_path()
    return Config(yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {})


def main():
    c = load()
    if "--json" in sys.argv:
        print(json.dumps(c.summary(), indent=2))
        return
    for k in c.kinds():
        print(f"{k}: " + " -> ".join(c.kind_agents(k)))
        for l in c.agents(k):
            req = ", ".join(f"{a}@{lv}" if lv else a for a, lv in l["requires"])
            gate = f"  [gate: {', '.join(l['gate']['artifacts'])}{' when ' + l['gate']['when'] if l['gate'].get('when') else ''}]" if l["gate"] else ""
            print(f"  {l['name']:<12} needs {req or '-'}  ->  {', '.join(l['produces'])}{' (per repo)' if l['fan_out'] else ''}{gate}")
    probs = c.problems()
    print("\nProblems:\n  - " + "\n  - ".join(probs) if probs else "\nNo problems found.")
    sys.exit(1 if probs else 0)


if __name__ == "__main__":
    main()
