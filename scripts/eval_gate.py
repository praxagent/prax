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


def main() -> int:
    from prax.eval.runner import run_eval_suite

    min_pass = float(os.environ.get("PRAX_EVAL_MIN_PASS_RATE", "0.0"))
    max_cases = int(os.environ.get("PRAX_EVAL_MAX_CASES", "20"))

    report = run_eval_suite(max_cases=max_cases)
    summary = {k: report.get(k) for k in ("total", "passed", "failed", "score", "pass_rate", "axes")}
    print(json.dumps(summary, indent=2))

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
