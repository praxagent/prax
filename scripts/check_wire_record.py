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

Arguments: tool spans record ``requested_args_sha256`` — the model's raw
arguments, hashed exactly as the wire record hashes them — and, when it is
true, ``args_changed``. Where a span has the hash, a call matches only if the
hashes agree, and two more findings are reported:

- ARGS DIFFER — the trace says the model asked for different arguments than the
  wire shows (the trace misreports the model);
- CHANGED BEFORE RUNNING — the span's ``args_changed`` flag: some layer of the
  call (a governance or context wrapper, the tool itself) ran without an
  argument the model asked for, or with a different value for one.

``args_sha256`` on a span is NOT compared with the requested hash: it hashes
what the innermost layer received after LangChain validation (defaults filled
in, values coerced, ``expected_observation`` removed), so it differs from the
request on ordinary calls.

Honest limits of CHANGED BEFORE RUNNING. Prax compares each layer's inputs with
the model's arguments in memory, while the call runs (the arguments are never
stored); this script only reads the resulting flag.

- Extra keys are not detected: a layer that ADDS an argument the model did not
  send looks the same as a schema default being filled in, so it is ignored.
- Values are compared across the conversions pydantic validation makes, and
  no others (``prax.agent.trace._loosely_equal``): ``"5"``, ``5`` and ``5.0``
  are equal; integers compare exactly, digit for digit, with no float round
  trip (``"9007199254740993"`` and ``9007199254740992`` differ); a boolean
  equals what pydantic's lax bool parsing reads from the other side (``1``,
  ``"yes"``, ``"on"``, ``"true"`` and ``True``; ``0``, ``"no"``, ``"off"``,
  ``"false"`` and ``False``), and a value that parsing rejects (``2``,
  ``"maybe"``) differs; a dict and the model built from it are equal. So a
  wrapper that changes only a value's type, within those conversions, is not
  reported.
- A requested key the tool's schema does not define is dropped by validation
  and reads as changed: the tool really did not receive it.
- Traces written before ``args_changed`` existed never report it.

Traces written before the hashes existed fall back to name and time window,
which catches a hidden or dropped call but not swapped arguments. A call the
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


def load_tool_spans(graphs_dir: str, since: float) -> list[tuple]:
    """(start time, tool name, trace id, requested args hash, args changed) for
    every tool span since *since*; the hash is "" and the flag False in traces
    that predate them."""
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
                        spans.append((started, str(node.get("name")), str(graph.get("trace_id")),
                                      str(node.get("requested_args_sha256") or ""),
                                      node.get("args_changed") is True))
    return sorted(spans)


def unaccounted(wire: list[dict], spans: list[tuple], *, slack: float,
                caller: str | None, since: float) -> list[dict]:
    """Wire tool calls with no unused matching span within *slack* seconds after.

    A span carrying ``requested_args_sha256`` matches only a call with the same
    argument hash; a same-name span in the window with a different hash is
    reported as ``ARGS DIFFER`` rather than as a match.

    Matching runs in passes over every call in the window, so a mismatch can
    never take a span another call matches exactly — with parallel same-name
    calls, the first-come span may be a sibling's true match:

    1. exact: same name and the same requested hash;
    2. legacy: same name, a span from before the hashes (name and time only);
    3. the rest are reported, ARGS DIFFER when a same-name span is still free
       in the window (it is used up, so one misreporting span cannot explain
       two calls), otherwise UNACCOUNTED.

    Each pass takes the earliest free span in a call's window, calls in wire
    order.
    """
    calls = [(entry, call) for entry in wire
             if entry.get("ts", 0) >= since and not (caller and entry.get("caller") != caller)
             for call in entry.get("tool_calls") or []]
    used: set[int] = set()
    matched: set[int] = set()

    def free_in_window(n: int) -> list[int]:
        entry, call = calls[n]
        return [i for i, s in enumerate(spans)
                if i not in used and s[1] == call.get("name")
                and entry["ts"] - 5 <= s[0] <= entry["ts"] + slack]

    def assign(fits) -> None:
        for n, (_entry, call) in enumerate(calls):
            if n in matched:
                continue
            want = call.get("args_sha256") or ""
            for i in free_in_window(n):
                if fits(spans[i][3], want):
                    used.add(i)
                    matched.add(n)
                    break

    assign(lambda have, want: bool(have) and have == want)  # exact
    assign(lambda have, want: not have)                     # legacy (hashless)

    missing = []
    for n, (entry, call) in enumerate(calls):
        if n in matched:
            continue
        rest = free_in_window(n)
        if rest:
            used.add(rest[0])
        missing.append({"ts": entry["ts"], "tool": call.get("name"),
                        "kind": "ARGS DIFFER" if rest else "UNACCOUNTED",
                        "model": entry.get("model"), "caller": entry.get("caller")})
    return missing


def changed_before_running(spans: list[tuple]) -> list[tuple]:
    """Spans whose tool ran with arguments other than the ones the model asked
    for — Prax's own ``args_changed`` flag, never a hash comparison (see the
    module docstring for why the two hashes are not comparable)."""
    return [s for s in spans if s[4]]


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
    changed = changed_before_running(spans)
    print(f"chain intact ({len(wire)} lines); last {args.hours:g} h: {calls} tool call(s) on the "
          f"wire, {len(spans)} tool span(s) in traces, {len(missing)} unaccounted, "
          f"{len(changed)} changed before running")
    for m in missing:
        when = datetime.fromtimestamp(m["ts"]).isoformat(timespec="seconds")
        print(f"  {m['kind']} {when} {m['tool']} (model {m['model']}, caller {m['caller']})")
    for s in changed:
        when = datetime.fromtimestamp(s[0]).isoformat(timespec="seconds")
        print(f"  CHANGED BEFORE RUNNING {when} {s[1]} (trace {s[2]})")
    return 1 if (missing or changed) else 0


if __name__ == "__main__":
    sys.exit(main())
