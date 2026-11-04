#!/usr/bin/env python3
"""Check the telemetry of one lane session and write a metrics summary.

Reads the lane_started event written by wf_launch.py, opens that session's JSONL dump,
and verifies that the workflow.* attributes, a session id and token usage are present.
The summary is written to tickets/<ticket>/run-<n>/metrics/<lane>[-<repo>]-a<attempt>.json
and Inspect & Adapt reads it. Missing or incomplete telemetry is recorded as such, so no
conclusions are drawn from partial data.

Exit code: 0 ok, 1 incomplete, 2 missing.

Usage:
  python scripts/otel_check.py --ticket PROJ-123 --run 1 --lane dev --repo repo-a [--attempt 1]

Format (confirmed on a real Copilot CLI 1.0.x dump): one JSON object per line, either
  {"type": "span",   "name", "attributes": {...}, "resource": {"attributes": {...}}, ...}
  {"type": "metric", ...}                       (cumulative; ignored here to avoid double counting)
Spans of interest: "chat <model>" (one per model call, carries tokens, cost and nano_aiu),
"execute_tool <tool>" (one per tool call) and "invoke_agent" (a total of the whole session).
The workflow.* tags are resource attributes. The session id is gen_ai.conversation.id.
Tokens are summed over the chat spans only: the invoke_agent span repeats the same totals,
and input_tokens already includes the cache read and write tokens (they are subsets).
OTLP JSON ({"resourceSpans": ...}) is also understood.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSION_KEYS = ("session.id", "gen_ai.conversation.id")


def norm(key):
    return str(key).replace(".", "_").lower()


def unwrap(v):
    if isinstance(v, dict):
        for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if k in v:
                return v[k]
    return v


def attrs_of(x):
    """Attributes as a plain dict. Accepts {'k': v} or the OTLP list [{'key': k, 'value': {...}}]."""
    if isinstance(x, dict):
        return {k: unwrap(v) for k, v in x.items()}
    if isinstance(x, list):
        return {a["key"]: unwrap(a.get("value")) for a in x if isinstance(a, dict) and "key" in a}
    return {}


def read_dump(path):
    spans, metrics, lines, bad = [], 0, 0, 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        lines += 1
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            bad += 1
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "metric":
            metrics += 1
        elif "resourceSpans" in obj:
            for rs in obj.get("resourceSpans") or []:
                res = attrs_of((rs.get("resource") or {}).get("attributes"))
                for ss in rs.get("scopeSpans") or []:
                    for sp in ss.get("spans") or []:
                        spans.append({"name": str(sp.get("name", "")), "attrs": attrs_of(sp.get("attributes")), "resource": res})
        elif obj.get("type") == "span" or "spanId" in obj or "traceId" in obj:
            spans.append({"name": str(obj.get("name", "")), "attrs": attrs_of(obj.get("attributes")),
                          "resource": attrs_of((obj.get("resource") or {}).get("attributes"))})
    return spans, metrics, lines, bad


def num(d, key):
    try:
        return float(d.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def read_kind(manifest):
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.startswith("kind:"):
                return line.split(":", 1)[1].split("#")[0].strip() or "story"
    return "story"


def find_launch(events_path, lane, repo, attempt):
    found = None
    if not events_path.exists():
        return None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4 or parts[2] != "lane_started":
            continue
        try:
            d = json.loads(parts[3])
        except json.JSONDecodeError:
            continue
        if d.get("lane") != lane or (repo and d.get("repo") != repo):
            continue
        if attempt is not None and d.get("attempt") != attempt:
            continue
        found = d  # keep the latest match
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--lane", required=True)
    ap.add_argument("--repo", default="")
    ap.add_argument("--attempt", type=int, default=None, help="default: the latest attempt")
    args = ap.parse_args()

    run_dir = ROOT / "tickets" / args.ticket / f"run-{args.run}"
    launch = find_launch(run_dir / "events.log", args.lane, args.repo, args.attempt)
    kind = read_kind(run_dir / "manifest.yaml")
    result = {"ticket": args.ticket, "run": args.run, "kind": kind, "lane": args.lane, "repo": args.repo}

    if launch is None:
        result.update(otel_status="missing", problems=["no lane_started event found in events.log"])
        return finish(run_dir, result, args)
    result.update(attempt=launch.get("attempt"), launch_id=launch.get("launch_id"), overlay=launch.get("overlay", []))

    dump = ROOT / launch["dump"] if launch.get("dump") else None
    if dump is None:
        result.update(otel_status="missing", problems=["session used the otlp exporter; no local dump to check"])
        return finish(run_dir, result, args)
    if not dump.exists() or dump.stat().st_size == 0:
        result.update(otel_status="missing", problems=[f"no telemetry in {launch['dump']}"])
        return finish(run_dir, result, args)

    spans, metrics, lines, bad = read_dump(dump)

    expected = {
        "workflow_ticket": args.ticket,
        "workflow_kind": kind,
        "workflow_run": str(args.run),
        "workflow_lane": args.lane,
        "workflow_launch_id": launch["launch_id"],
        "workflow_attempt": str(launch.get("attempt")),
    }
    if launch.get("repo"):
        expected["workflow_repo"] = launch["repo"]
    seen, sessions, credit_keys, models = set(), set(), set(), set()
    for sp in spans:
        merged = {norm(k): v for k, v in {**sp["resource"], **sp["attrs"]}.items()}
        for k, want in expected.items():
            if str(merged.get(k)) == want:
                seen.add(k)
        for k in SESSION_KEYS:
            if sp["attrs"].get(k):
                sessions.add(str(sp["attrs"][k]))
        for k in sp["attrs"]:
            nk = norm(k)
            if "credit" in nk or "aiu" in nk or nk.endswith("_cost"):
                credit_keys.add(k)
        m = sp["attrs"].get("gen_ai.response.model") or sp["attrs"].get("gen_ai.request.model")
        if m:
            models.add(str(m))

    chat = [s for s in spans if s["attrs"].get("gen_ai.operation.name") == "chat" or s["name"].startswith("chat")]
    if not chat:  # fall back to the session total
        chat = [s for s in spans if s["name"] == "invoke_agent"]
    tools = Counter(str(s["attrs"].get("gen_ai.tool.name") or s["name"]) for s in spans
                    if s["name"].startswith("execute_tool") or s["attrs"].get("gen_ai.tool.name"))

    def total(key):
        return sum(num(s["attrs"], key) for s in chat)

    tokens_in, tokens_out = total("gen_ai.usage.input_tokens"), total("gen_ai.usage.output_tokens")
    nano = total("github.copilot.nano_aiu")

    problems = [f"attribute not found: {k}" for k in sorted(expected) if k not in seen]
    if tokens_in + tokens_out == 0:
        problems.append("no gen_ai.usage.* token counts found")
    if not sessions:
        problems.append("no session id found (looked for " + ", ".join(SESSION_KEYS) + ")")

    result.update(
        otel_status="ok" if not problems else "incomplete",
        problems=problems,
        dump=launch["dump"],
        lines=lines, unparsable_lines=bad, spans=len(spans), metric_records=metrics,
        chat_calls=len(chat), tool_calls=sum(tools.values()), tools=dict(sorted(tools.items())),
        tokens_in=int(tokens_in), tokens_out=int(tokens_out),
        tokens_cache_read=int(total("gen_ai.usage.cache_read.input_tokens")),
        tokens_cache_write=int(total("gen_ai.usage.cache_write.input_tokens")),
        tokens_reasoning=int(total("gen_ai.usage.reasoning.output_tokens")),
        copilot_cost=total("github.copilot.cost"), nano_aiu=int(nano), aiu=round(nano / 1e9, 4),
        models=sorted(models), conversation_ids=sorted(sessions),
        credit_attributes_found=sorted(credit_keys),
    )
    return finish(run_dir, result, args)


def finish(run_dir, result, args):
    name = "-".join(p for p in (args.lane, args.repo) if p) + f"-a{result.get('attempt') or args.attempt or 1}.json"
    out = run_dir / "metrics" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"otel_status: {result['otel_status']}")
    for p in result.get("problems", []):
        print(f"  - {p}")
    if result["otel_status"] != "missing" and "tokens_in" in result:
        print(f"  tokens in/out: {result['tokens_in']}/{result['tokens_out']}, tool calls: {result['tool_calls']}, spans: {result['spans']}")
        print(f"  cache read/write: {result['tokens_cache_read']}/{result['tokens_cache_write']}, reasoning: {result['tokens_reasoning']}, "
              f"model calls: {result['chat_calls']}")
        print(f"  copilot cost: {result['copilot_cost']:g}, nano_aiu: {result['nano_aiu']} (= {result['aiu']} AIU)")
        print(f"  credit-like attributes: {', '.join(result['credit_attributes_found']) or 'none found'}")
    print(f"wrote {out.relative_to(ROOT).as_posix()}")
    sys.exit({"ok": 0, "incomplete": 1, "missing": 2}[result["otel_status"]])


if __name__ == "__main__":
    main()
