#!/usr/bin/env python3
"""Check the telemetry of one lane session and write a metrics summary.

Reads the lane_started event written by wf_launch.py, opens that session's JSONL dump,
and verifies that the workflow.* attributes and token usage are present. The summary is
written to tickets/<ticket>/run-<n>/metrics/<lane>[-<repo>]-a<attempt>.json and
Inspect & Adapt reads it. Missing or incomplete telemetry is recorded as such, so no
conclusions are drawn from partial data.

Exit code: 0 ok, 1 incomplete, 2 missing.

Usage:
  python scripts/otel_check.py --ticket PROJ-123 --run 1 --lane dev --repo repo-a [--attempt 1]

Field names are matched tolerantly (dots and underscores are treated alike) because the
exact JSONL layout depends on the Copilot version. Confirm them once against a real dump.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def norm(key):
    return str(key).replace(".", "_").lower()


def unwrap(v):
    if isinstance(v, dict):
        for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if k in v:
                return v[k]
    return v


def collect(node, out):
    """Flatten any JSON into (key, value) pairs, handling OTLP-style attribute lists."""
    if isinstance(node, dict):
        if isinstance(node.get("key"), str) and "value" in node:
            out.append((node["key"], unwrap(node["value"])))
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                collect(v, out)
            else:
                out.append((k, v))
    elif isinstance(node, list):
        for x in node:
            collect(x, out)


def number(v):
    try:
        return float(v)
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

    seen = set()
    lines = bad = spans = tool_calls = 0
    tokens_in = tokens_out = 0.0
    has_session_id = False
    credit_keys = set()
    span_like, all_lines = [], []

    for raw in dump.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        lines += 1
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            bad += 1
            continue
        pairs = []
        collect(obj, pairs)
        for k, v in pairs:
            nk = norm(k)
            if nk in expected and str(v) == expected[nk]:
                seen.add(nk)
            if nk == "session_id":
                has_session_id = True
            if "credit" in nk:
                credit_keys.add(nk)
        keys = {norm(k) for k, _ in pairs}
        (span_like if keys & {"spanid", "span_id", "traceid", "trace_id"} else all_lines).append(pairs)

    # Count usage from span records when the dump has them (avoids double counting metrics).
    records = span_like if span_like else all_lines
    spans = len(span_like)
    for pairs in records:
        for k, v in pairs:
            nk = norm(k)
            if nk.startswith("gen_ai_usage_") and nk.endswith("input_tokens"):
                tokens_in += number(v)
            elif nk.startswith("gen_ai_usage_") and nk.endswith("output_tokens"):
                tokens_out += number(v)
            elif nk == "gen_ai_tool_name":
                tool_calls += 1

    problems = [f"attribute not found: {k}" for k in sorted(expected) if k not in seen]
    if tokens_in + tokens_out == 0:
        problems.append("no gen_ai.usage.* token counts found")
    if not has_session_id:
        problems.append("session.id not found (needed to join VS Code sessions)")

    result.update(
        otel_status="ok" if not problems else "incomplete",
        problems=problems,
        dump=launch["dump"],
        lines=lines, unparsable_lines=bad, spans=spans, tool_calls=tool_calls,
        tokens_in=int(tokens_in), tokens_out=int(tokens_out),
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
        print(f"  credit attributes: {', '.join(result['credit_attributes_found']) or 'none found'}")
    print(f"wrote {out.relative_to(ROOT).as_posix()}")
    sys.exit({"ok": 0, "incomplete": 1, "missing": 2}[result["otel_status"]])


if __name__ == "__main__":
    main()
