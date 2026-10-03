#!/usr/bin/env python3
"""Compare the secrets proxy's wire record with Prax's own traces.

Idea credit: NVIDIA's Open Agent Safety Platform — check the agent's account
against the model path, which the agent doesn't control.

The proxy's wire record (prax-secrets-proxy ``PROXY_WIRE_RECORD``) lists, per
model response, the tool calls the model told Prax to make — names and
argument hashes, written outside Prax. Prax's traces
(``$WORKSPACE_DIR/.prax/graphs/graphs-*.jsonl``) list the tool calls Prax says
it made. A call on the wire with no matching tool span in the traces, shortly
after, is what this reports: activity Prax didn't account for.

    uv run python scripts/check_wire_record.py WIRE.jsonl [--caller prax-prod]
        [--graphs DIR] [--hours 24] [--slack 300]

Exit 0: chain intact and every wire call accounted for. Exit 1: a broken
chain or unaccounted calls (listed). Read-only; changes nothing.

Honest limits: matching is by tool name and time window, not argument hash —
traces don't record the model's raw arguments — so it catches a hidden or
dropped call, not a call whose arguments were silently swapped. A call the
model asked for that Prax legitimately refused (a floor, a budget) has a span
too, so it matches; one Prax never attempted shows up, which is also worth
knowing.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import time
from datetime import datetime

GENESIS = "0" * 64


def verify_chain(entries: list[dict]) -> str | None:
    """Same rule as secrets_proxy.wire_record.verify (keep the two in step)."""
    prev = GENESIS
    for n, entry in enumerate(entries, 1):
        if entry.get("prev") != prev:
            return f"line {n}: chain broken (a line before it was removed, edited or reordered)"
        body = {k: v for k, v in entry.items() if k not in ("prev", "hash")}
        digest = hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":")))
                                .encode()).hexdigest()
        if digest != entry.get("hash"):
            return f"line {n}: content does not match its hash (edited)"
        prev = entry["hash"]
    return None


def load_wire(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_tool_spans(graphs_dir: str, since: float) -> list[tuple[float, str, str]]:
    """(start time, tool name, trace id) for every tool span since *since*."""
    spans = []
    for path in sorted(glob.glob(os.path.join(graphs_dir, "graphs-*.jsonl"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    graph = json.loads(line)
                except ValueError:
                    continue
                for node in graph.get("nodes") or []:
                    if node.get("spoke_or_category") != "tool":
                        continue
                    try:
                        started = datetime.fromisoformat(node["started_at"]).timestamp()
                    except (KeyError, TypeError, ValueError):
                        continue
                    if started >= since:
                        spans.append((started, str(node.get("name")), str(graph.get("trace_id"))))
    return sorted(spans)


def unaccounted(wire: list[dict], spans: list[tuple[float, str, str]], *, slack: float,
                caller: str | None, since: float) -> list[dict]:
    """Wire tool calls with no unused matching span within *slack* seconds after."""
    used: set[int] = set()
    missing = []
    for entry in wire:
        if entry.get("ts", 0) < since or (caller and entry.get("caller") != caller):
            continue
        for call in entry.get("tool_calls") or []:
            match = next((i for i, (t, name, _) in enumerate(spans)
                          if i not in used and name == call.get("name")
                          and entry["ts"] - 5 <= t <= entry["ts"] + slack), None)
            if match is None:
                missing.append({"ts": entry["ts"], "tool": call.get("name"),
                                "model": entry.get("model"), "caller": entry.get("caller")})
            else:
                used.add(match)
    return missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("wire")
    ap.add_argument("--graphs", default=os.path.join(
        os.environ.get("WORKSPACE_DIR", "../workspaces"), ".prax", "graphs"))
    ap.add_argument("--caller", default=None, help="only this proxy caller (e.g. prax-prod)")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--slack", type=float, default=300.0,
                    help="seconds after a model response to look for the tool span")
    args = ap.parse_args(argv)

    wire = load_wire(args.wire)
    broken = verify_chain(wire)
    if broken:
        print(f"BROKEN CHAIN: {broken}")
        return 1
    since = time.time() - args.hours * 3600
    spans = load_tool_spans(args.graphs, since - args.slack)
    missing = unaccounted(wire, spans, slack=args.slack, caller=args.caller, since=since)
    calls = sum(len(e.get("tool_calls") or []) for e in wire
                if e.get("ts", 0) >= since and (not args.caller or e.get("caller") == args.caller))
    print(f"chain intact ({len(wire)} lines); last {args.hours:g} h: {calls} tool call(s) on the "
          f"wire, {len(spans)} tool span(s) in traces, {len(missing)} unaccounted")
    for m in missing:
        when = datetime.fromtimestamp(m["ts"]).isoformat(timespec="seconds")
        print(f"  UNACCOUNTED {when} {m['tool']} (model {m['model']}, caller {m['caller']})")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
