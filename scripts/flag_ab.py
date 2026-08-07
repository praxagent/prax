#!/usr/bin/env python3
"""Run a flag A/B campaign: baseline plus one arm per flag configuration.

Reproduces the 2026-07-08 method (``docs/research/flag-eval-campaign-2026-07-08.md``):
each arm is a separate subprocess whose environment differs from baseline by
exactly the flags under test, all arms run the same suite on the same model, and
results land under ``$PRAX_EVAL_DIR/<campaign>/<arm>/``.

Why a script rather than a Makefile target: an arm is *an environment*, and the
useful unit is "run every arm, then diff them" — which needs a loop, a
per-arm result path, and a comparison at the end.

Usage
-----
    uv run python scripts/flag_ab.py --campaign flags-20260807 \\
        --suite capability --arms arms.json

``arms.json`` maps arm name → {ENV: value}. The ``baseline`` arm is implicit
(empty overrides) unless you define it.

Honesty rules baked in
----------------------
* An arm that CRASHES is reported as crashed, never as a score of zero — a
  broken arm is not evidence about the flag.
* The comparison prints the per-case grid, not only totals, because "5/6 vs 5/6"
  hides two different cases failing.
* With n this small the script refuses to declare a winner; it reports the
  numbers and names what would be needed to call it.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _eval_dir() -> Path:
    env = os.environ.get("PRAX_EVAL_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "prax-evals"


def run_arm(name: str, overrides: dict, *, suite: str, tier: str,
            campaign: str, cheap: bool, timeout_s: int) -> dict:
    """Run one arm in a subprocess. Never raises — a crash is a result."""
    env = os.environ.copy()
    env.setdefault("FLASK_SECRET_KEY", "ci-test-key")
    out_dir = _eval_dir() / campaign / name
    out_dir.mkdir(parents=True, exist_ok=True)
    env["PRAX_EVAL_DIR"] = str(out_dir)

    if cheap:
        model = os.environ.get("OPENROUTER_EVAL_MODEL", "deepseek/deepseek-v4-flash")
        env.update({
            "LLM_PROVIDER": "openrouter",
            "BASE_MODEL": model, "LOW_MODEL": model, "MEDIUM_MODEL": model,
            "HIGH_MODEL": model, "PRO_MODEL": model,
            "EMBEDDING_PROVIDER": "ollama", "EMBEDDING_MODEL": "nomic-embed-text",
        })
    env.update({k: str(v) for k, v in overrides.items()})

    cmd = [sys.executable, "scripts/eval_suite.py", suite, "--tier", tier]
    started = time.time()
    print(f"\n=== arm {name}: {overrides or '(baseline)'}", flush=True)
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=timeout_s,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
        tail = (proc.stdout or "")[-4000:]
        (out_dir / "stdout.log").write_text(proc.stdout or "", encoding="utf-8")
        (out_dir / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    except subprocess.TimeoutExpired:
        status, tail = "TIMEOUT", ""
    except Exception as exc:  # noqa: BLE001 - a crashed arm is data, not a stop
        status, tail = f"CRASH {type(exc).__name__}: {exc}", ""

    return {
        "arm": name, "overrides": overrides, "status": status,
        "seconds": round(time.time() - started, 1),
        "out_dir": str(out_dir), "tail": tail,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--suite", default="capability")
    ap.add_argument("--tier", default="low")
    ap.add_argument("--arms", required=True, help="path to arms JSON")
    ap.add_argument("--cheap", action="store_true")
    ap.add_argument("--timeout-s", type=int, default=3600)
    args = ap.parse_args()

    arms: dict = json.loads(Path(args.arms).read_text(encoding="utf-8"))
    arms.setdefault("baseline", {})
    # baseline first, so a broken harness is obvious before spending on arms
    order = ["baseline"] + [k for k in arms if k != "baseline"]

    results = []
    for name in order:
        results.append(run_arm(
            name, arms[name], suite=args.suite, tier=args.tier,
            campaign=args.campaign, cheap=args.cheap, timeout_s=args.timeout_s))
        r = results[-1]
        print(f"--- {r['arm']}: {r['status']} in {r['seconds']}s", flush=True)
        if name == "baseline" and r["status"] != "ok":
            print("BASELINE FAILED — stopping. Arms measured against a broken "
                  "baseline are not evidence.", flush=True)
            break

    summary = _eval_dir() / args.campaign / "summary.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    for r in results:
        print(f"{r['arm']:32} {r['status']:12} {r['seconds']:>7}s")
    print("=" * 60)
    print(f"raw: {summary.parent}")
    print("\nNOTE: read the per-case grids in each arm's stdout.log before "
          "concluding anything. The capability suite is 7 cases — a one-case "
          "difference is noise, not a verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
