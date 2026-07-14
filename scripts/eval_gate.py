"""Regression gate — run the eval suite and fail if quality drops too low.

This replays recorded failure cases through the **live** agent and scores them
with an LLM judge, so it needs provider API keys and makes real calls.  It is a
*manual / dedicated-CI* gate, not part of the default ``make ci`` (which must
stay key-free and deterministic).  Run it before shipping a system-prompt or
model-config change to get a measured before/after:

    make eval                              # report only (no threshold)
    PRAX_EVAL_MIN_PASS_RATE=0.8 make eval  # fail if pass-rate < 0.8

Exit codes: 0 = passed (or nothing to evaluate), 1 = below threshold.
"""
from __future__ import annotations

import json
import os
import sys


def _report_goldens() -> None:
    """Surface curated 'golden' quality targets every run so roadmap items
    tracked as goldens stay visible (IDEAS_BACKLOG #13). Listing is key-free;
    set PRAX_EVAL_GOLDENS=1 to actually replay + score them (needs keys + cost).
    """
    from prax.eval.goldens import run_golden_suite

    score = os.environ.get("PRAX_EVAL_GOLDENS", "").lower() in ("1", "true", "yes")
    report = run_golden_suite(replay=score)
    if report["total"] == 0:
        return
    print("\n--- Golden quality targets ---")
    if not score:
        for r in report["results"]:
            vis = "" if r.get("visibility", "public") == "public" else " (private/held-out)"
            print(f"  • {r['id']} [{r.get('status') or 'tracked'}]{vis} — {r['title']}")
        print("  (set PRAX_EVAL_GOLDENS=1 to replay + score these)")
    else:
        for r in report["results"]:
            total = r.get("total")
            vis = "" if r.get("visibility", "public") == "public" else " (private)"
            print(f"  • {r['id']}{vis}: {total if total is not None else 'n/a'} — {r['title']}")
        print(f"  goldens avg: {report['avg']} ({report['scored']}/{report['total']} scored)")
        # Public/private split (AIDE² selection rule) — a self-improvement change is
        # judged on the PRIVATE (held-out) average, not the public one it was tuned on.
        pub, priv = report.get("avg_public"), report.get("avg_private")
        print(f"  split: public {pub} ({report.get('n_public', 0)}) | "
              f"private/held-out {priv} ({report.get('n_private', 0)})")


def main() -> int:
    from prax.eval.runner import run_eval_suite

    min_pass = float(os.environ.get("PRAX_EVAL_MIN_PASS_RATE", "0.0"))
    max_cases = int(os.environ.get("PRAX_EVAL_MAX_CASES", "20"))

    report = run_eval_suite(max_cases=max_cases)
    summary = {k: report.get(k) for k in ("total", "passed", "failed", "score", "pass_rate", "axes")}
    print(json.dumps(summary, indent=2))

    try:
        _report_goldens()
    except Exception as exc:  # never let golden reporting break the gate
        print(f"(golden reporting skipped: {exc})")

    if report.get("total", 0) == 0:
        print("No eval cases recorded — nothing to gate.")
        return 0

    pass_rate = report.get("pass_rate", 0.0)
    if pass_rate < min_pass:
        print(f"FAIL: pass_rate {pass_rate} < threshold {min_pass}")
        return 1
    print(f"Eval gate passed (pass_rate {pass_rate} >= {min_pass}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
