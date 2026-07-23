#!/usr/bin/env python3
"""Drive Prax's benchmark suites — built to run overnight on a slow local model.

All three suites are **resumable**: kill the process (or Ctrl-C) and re-run the
same command to pick up exactly where it stopped.  By default there is **no
per-task timeout** (``PRAX_EVAL_TASK_TIMEOUT_S=0``), so a model generating at a
few tokens/second can take as long as it needs.

Point Prax at a local OpenAI-compatible endpoint (ds4 / vLLM / Ollama / llama.cpp)
in ``.env`` first::

    LLM_PROVIDER=vllm
    VLLM_BASE_URL=http://<host>:<port>/v1
    LOW_MODEL=<model-id>
    MEDIUM_MODEL=<model-id>
    HIGH_MODEL=<model-id>

Usage::

    # Daily driver — capability checks through the full harness (deterministic)
    uv run python scripts/eval_suite.py capability --tier medium

    # The headline number — how much does the harness lift THIS model?
    uv run python scripts/eval_suite.py harness-lift --tier medium

    # External scoreboard — resumable GAIA batch (run it overnight)
    uv run python scripts/eval_suite.py gaia --level 1 --limit 20 --tier medium

Resume is automatic; pass ``--no-resume`` to start fresh, ``--retry-errors`` to
re-attempt only the tasks that errored last time.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _suite_dir(args):
    return Path(args.suite_dir) if getattr(args, "suite_dir", None) else None


def _print_summary(summary: dict) -> None:
    print(json.dumps(summary, indent=2, default=str))
    agg = summary.get("aggregate") or {}
    out = summary.get("out_dir") or summary.get("label")
    if agg:
        print("\n--- aggregate ---")
        for k, v in agg.items():
            print(f"  {k}: {v}")
    print(f"\nResults under: {out}")


def cmd_capability(args) -> int:
    from prax.eval.capability import run_capability_suite
    summary = run_capability_suite(
        tier=args.tier, model_override=args.model,
        resume=not args.no_resume, concurrency=args.concurrency,
        suite_dir=_suite_dir(args), skip=getattr(args, "skip", None),
    )
    _print_summary(summary)
    return 0


def cmd_harness_lift(args) -> int:
    from prax.eval.capability import run_harness_lift
    summary = run_harness_lift(
        tier=args.tier, model_override=args.model, resume=not args.no_resume,
        suite_dir=_suite_dir(args),
    )
    _print_summary(summary)
    agg = summary.get("aggregate") or {}
    lift = agg.get("avg_harness_lift")
    if lift is not None:
        print(f"\n>>> Harness lift (full − bare, content): {lift:+.3f}")
        print("    Positive = the scaffolding is doing its job on this model.")
    return 0


def _record_scorecard(report: dict) -> None:
    """Distil a matrix report into the committed aggregates-only scorecard record."""
    import datetime
    import os
    import subprocess

    from prax.eval.scorecard import (
        UnhealthyRunError,
        assert_run_healthy,
        write_dashboard,
        write_record,
    )
    # Refuse to publish a broken run (mass auth/infra failures scored as zeros).
    try:
        assert_run_healthy(report)
    except UnhealthyRunError as exc:
        print(f"\n>>> Scorecard NOT recorded: {exc}")
        return
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                         text=True).strip()
    except Exception:
        commit = "unknown"
    model = report.get("cost_model") or os.environ.get("LOW_MODEL") or "unknown"
    ts = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
    from prax.eval.scorecard import build_record
    record = build_record(
        report, git_commit=commit, model=model,
        provider=os.environ.get("LLM_PROVIDER", "unknown"),
        matrix_limit=int(os.environ.get("PRAX_EVAL_DATASET_LIMIT", "0")),
        timestamp=ts,
    )
    run_id = commit if commit != "unknown" else ts.replace(":", "").replace("-", "")[:12]
    path = write_record(record, run_id=run_id)
    write_dashboard()
    print(f"\n>>> Scorecard record written: {path}")
    print("    Dashboard updated: docs/eval-results/MATRIX.md (aggregates only, committable)")


def cmd_multiturn(args) -> int:
    from prax.eval.multiturn import run_multiturn_suite
    summary = run_multiturn_suite(tier=args.tier, model_override=args.model, k=args.k)
    _print_summary(summary)
    agg = summary.get("aggregate") or {}
    print(f"\n>>> Multi-turn pass^{agg.get('k')}: {agg.get('pass_hat_k')} "
          f"(avg per-trial pass rate {agg.get('avg_trial_pass_rate')})")
    print("    pass^k = fraction of cases that passed ALL k trials — reliability, not one lucky shot.")
    return 0


def cmd_self_regen(args) -> int:
    from prax.eval.self_regen import run_self_regen
    summary = run_self_regen(rounds=args.rounds, apply=args.apply, tier=args.tier)
    print(f"\n>>> Self-regeneration (#29): baseline {summary['baseline']} → "
          f"best {summary['best']} (+{summary['improvement']}) over {args.rounds} rounds; "
          f"kept={summary['variants_kept']}, applied={summary['applied']}")
    print(f"    Reviewable proposal + full lineage: {summary['out_dir']}/PROPOSAL.md")
    if not args.apply:
        print("    (propose-only; pass --apply AND set SELF_REGEN_ENABLED to auto-apply)")
    return 0


def cmd_gaia(args) -> int:
    from prax.eval.gaia_single import run_gaia_suite
    summary = run_gaia_suite(
        level=args.level, limit=args.limit, text_only=not args.with_files,
        tier=args.tier, model_override=args.model, cost_limit_usd=args.cost_limit,
        resume=not args.no_resume, retry_errors=args.retry_errors,
        concurrency=args.concurrency, suite_dir=_suite_dir(args),
    )
    _print_summary(summary)
    return 0


def cmd_benchmark(args) -> int:
    from prax.eval.pricing import format_cost
    if args.name == "all":
        from prax.eval.benchmarks import run_all_benchmarks
        report = run_all_benchmarks(tier=args.tier, model=args.model, resume=not args.no_resume)
        print(f"\n>>> Benchmark suite: {report['n_benchmarks']} adapters, "
              f"avg pass-rate {report['avg_pass_rate']}  (model: {report.get('cost_model') or 'n/a'})")
        for name, agg in report["benchmarks"].items():
            print(f"    {name:12} pass_rate={agg.get('pass_rate')} (graded {agg.get('graded')})"
                  f"  {agg.get('total_tokens', 0):>8} tok  {format_cost(agg.get('estimated_cost_usd'))}")
        print(f"    {'TOTAL':12} {'':>25} {report.get('total_tokens', 0):>8} tok  "
              f"{format_cost(report.get('estimated_cost_usd'))} (estimate)")
        if getattr(args, "record", False):
            _record_scorecard(report)
        return 0
    if args.lift:
        from prax.eval.benchmarks import run_benchmark_lift
        summary = run_benchmark_lift(
            args.name, tier=args.tier, model=args.model, resume=not args.no_resume,
        )
        _print_summary(summary)
        agg = summary.get("aggregate") or {}
        lift = agg.get("harness_lift")
        if lift is not None:
            print(f"\n>>> {args.name} harness lift (full − bare pass-rate): {lift:+.3f}")
            print(f"    full {agg.get('full_pass_rate')} vs bare {agg.get('bare_pass_rate')} "
                  f"(tokens: full {agg.get('avg_full_tokens')} / bare {agg.get('avg_bare_tokens')})")
        return 0
    from prax.eval.benchmarks import run_benchmark_live
    summary = run_benchmark_live(
        args.name, tier=args.tier, model=args.model, resume=not args.no_resume,
    )
    _print_summary(summary)
    return 0


def main() -> int:
    # Route eval model calls through the keyless secrets-proxy the SAME way the
    # server does: export ONLY the non-secret proxy allowlist (HTTPS_PROXY, CA
    # paths) from .env. Without this the eval CLI runs no proxy env, so a keyless
    # box (placeholder model keys, real keys in the forward proxy) 401s every call
    # — the bug that voided the first matrix baseline. This is a trusted local
    # CLI, and it never exports a secret (settings._PROXY_ENV_ALLOWLIST).
    try:
        from prax.settings import _export_proxy_env_from_dotenv
        _export_proxy_env_from_dotenv()
    except Exception:  # noqa: BLE001 — proxy env is optional; keys-in-.env still works
        pass

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def _common(sp):
        sp.add_argument("--tier", default="medium", help="model tier (low/medium/high/pro)")
        sp.add_argument("--model", default=None, help="explicit model id (overrides --tier)")
        sp.add_argument("--no-resume", action="store_true", help="start fresh, ignore prior results")
        sp.add_argument("--concurrency", type=int, default=None,
                        help="parallel tasks (default PRAX_EVAL_CONCURRENCY, =1)")
        sp.add_argument("--suite-dir", default=None,
                        help="explicit run dir (default: stable per-config, so re-running "
                             "the same command resumes). Pass to keep runs separate.")

    sp_cap = sub.add_parser("capability", help="capability checks through the full harness")
    _common(sp_cap)
    sp_cap.add_argument("--skip", action="append", default=[],
                        help="case id to exclude (repeatable) — e.g. a case whose "
                             "external dependency is down, so A/B arms stay comparable")
    sp_cap.set_defaults(func=cmd_capability)

    sp_lift = sub.add_parser("harness-lift", help="full harness vs bare model, same model")
    _common(sp_lift)
    sp_lift.set_defaults(func=cmd_harness_lift)

    sp_mt = sub.add_parser("multiturn",
                           help="multi-turn persona conversations, graded on final state, reported as pass^k")
    _common(sp_mt)
    sp_mt.add_argument("--k", type=int, default=3, help="trials per case for pass^k (default 3)")
    sp_mt.set_defaults(func=cmd_multiturn)

    sp_sr = sub.add_parser("self-regen",
                           help="self-regeneration loop (#29): propose→verify→keep a system-prompt overlay")
    sp_sr.add_argument("--tier", default="high", help="model tier for proposer/auditor (default high)")
    sp_sr.add_argument("--rounds", type=int, default=3, help="candidate patches to try")
    sp_sr.add_argument("--apply", action="store_true",
                       help="auto-apply the winner (also needs SELF_REGEN_ENABLED); else propose-only")
    sp_sr.set_defaults(func=cmd_self_regen)

    sp_gaia = sub.add_parser("gaia", help="resumable GAIA batch (external scoreboard)")
    _common(sp_gaia)
    sp_gaia.add_argument("--level", type=int, default=1, help="GAIA level (default 1)")
    sp_gaia.add_argument("--limit", type=int, default=None, help="cap number of tasks")
    sp_gaia.add_argument("--with-files", action="store_true",
                         help="include tasks with attached files (default: text-only)")
    sp_gaia.add_argument("--retry-errors", action="store_true", help="re-run only errored tasks")
    sp_gaia.add_argument("--cost-limit", type=float, default=2.0,
                         help="USD safety rail per task (0 rates = local = free)")
    sp_gaia.set_defaults(func=cmd_gaia)

    sp_bench = sub.add_parser(
        "benchmark",
        help="run a standard benchmark adapter through the full harness")
    from prax.eval.benchmarks import ADAPTER_NAMES
    sp_bench.add_argument(
        "name",
        choices=[*ADAPTER_NAMES, "all"])  # kept in sync with the registry
    sp_bench.add_argument("--tier", default="low", help="model tier (default low)")
    sp_bench.add_argument("--model", default=None, help="explicit model id (overrides --tier)")
    sp_bench.add_argument("--no-resume", action="store_true", help="start fresh")
    sp_bench.add_argument("--lift", action="store_true",
                          help="full harness vs bare model (same model) → harness lift")
    sp_bench.add_argument("--record", action="store_true",
                          help="(name=all only) write an aggregates-only scorecard record "
                               "to docs/eval-results/ + update MATRIX.md")
    sp_bench.set_defaults(func=cmd_benchmark)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
