"""Generic benchmark-adapter seam — plug a standard benchmark into Prax's own
resumable batch runner (`prax.eval.batch.run_batch`).

The [2026 benchmark scan](../../../docs/research/benchmark-scan-2026-adopt.md) found
Prax has the eval *engine* but almost no standard-benchmark *coverage*. Rather than
one bespoke runner per benchmark, an adapter supplies three things and reuses the
existing resume/telemetry/summary machinery:

    cases()            -> list[dict]           # each has a stable "id"
    prompt(case)       -> str                  # the user message to send
    score(case, resp)  -> {"passed", "score", ...}   # DETERMINISTIC — no LLM judge

Determinism is the point: it keeps grading un-gameable and CPU/keyless (the
"verifiable beats judgeable" rule), so a whole benchmark runs offline in CI.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class BenchmarkAdapter(Protocol):
    name: str

    def cases(self) -> list[dict]: ...
    def prompt(self, case: dict) -> str: ...
    def score(self, case: dict, response: str) -> dict: ...


def run_benchmark(
    adapter: BenchmarkAdapter,
    replay_fn: Callable[[str], str],
    *,
    out_dir: str | Path,
    concurrency: int = 1,
    resume: bool = True,
    cost_model: str | None = None,
) -> dict:
    """Run every case of *adapter* through *replay_fn* and score deterministically.

    ``replay_fn(prompt) -> response`` is the executor (the live orchestrator, a bare
    model, or a fake in tests). Resumable + summarized via ``run_batch``.

    Captures real per-case token usage (via ``collect_usage``) and, when
    *cost_model* is known, an estimated USD cost (``prax.eval.pricing``) — so the
    summary carries ``prompt_tokens``/``completion_tokens``/``total_tokens`` and
    ``estimated_cost_usd`` (None when the model's price is unknown). A fake
    ``replay_fn`` (keyless CI) simply reports zero tokens / zero cost.
    """
    from prax.eval.batch import run_batch
    from prax.eval.telemetry import collect_usage

    by_id = {str(c["id"]): c for c in adapter.cases()}

    def _run_one(case_id: str) -> dict:
        case = by_id[case_id]
        with collect_usage() as usage:
            response = replay_fn(adapter.prompt(case))
        snap = usage.snapshot()
        graded = adapter.score(case, response) or {}
        return {
            "id": case_id,
            "passed": bool(graded.get("passed")),
            "score": float(graded.get("score", 0.0)),
            "checks": graded.get("checks"),
            "prompt_tokens": int(snap.get("prompt_tokens", 0)),
            "completion_tokens": int(snap.get("completion_tokens", 0)),
            "answer_preview": (response or "")[:300],
        }

    def _summarize(results: list[dict]) -> dict:
        from prax.eval.pricing import estimate_cost
        graded = [r for r in results if not r.get("error")]
        n = len(graded)
        passed = sum(1 for r in graded if r.get("passed"))
        pt = sum(int(r.get("prompt_tokens", 0)) for r in graded)
        ct = sum(int(r.get("completion_tokens", 0)) for r in graded)
        from prax.eval.benchmarks.datasets import full_datasets_enabled, sample_seed
        return {
            "benchmark": adapter.name,
            "graded": n,
            "passed": passed,
            "pass_rate": round(passed / n, 3) if n else 0.0,
            "avg_score": round(sum(r.get("score", 0.0) for r in graded) / n, 3) if n else 0.0,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "cost_model": cost_model,
            "estimated_cost_usd": estimate_cost(cost_model, pt, ct),
            # Reporting checklist (external review 2026-07-17): the protocol a
            # reader needs to interpret the number — task variant + scoring rule,
            # attempt semantics, and how the subset was drawn.
            "protocol": {
                "variant": getattr(adapter, "variant", None),
                "attempts": getattr(adapter, "attempts", "pass@1"),
                "dataset": ("real" if full_datasets_enabled() else "seed"),
                "sampling": f"seeded-random(seed={sample_seed()})",
            },
        }

    return run_batch(
        list(by_id), _run_one, out_dir=out_dir, label=adapter.name,
        concurrency=concurrency, resume=resume, summarize=_summarize,
    )


# ---------------------------------------------------------------------------
# Registry + live executor — make the adapters runnable against the real harness
# ---------------------------------------------------------------------------

ADAPTER_NAMES = ("ifeval", "injecagent", "sycophancy", "bfcl", "halueval", "truthfulqa",
                 "gsm8k", "mmlu_pro", "gpqa", "math", "simpleqa", "humaneval", "arc_agi_2",
                 "longcontext", "agentsafety")


def get_adapter(name: str, **kwargs) -> BenchmarkAdapter:
    """Return a benchmark adapter by name (lazy import to keep this module light)."""
    key = (name or "").lower()
    if key == "ifeval":
        from prax.eval.benchmarks.ifeval import IFEvalAdapter
        return IFEvalAdapter(**kwargs)
    if key == "injecagent":
        from prax.eval.benchmarks.injecagent import InjecAgentAdapter
        return InjecAgentAdapter(**kwargs)
    if key == "sycophancy":
        from prax.eval.benchmarks.sycophancy import SycophancyAdapter
        return SycophancyAdapter(**kwargs)
    if key == "bfcl":
        from prax.eval.benchmarks.bfcl import BFCLAdapter
        return BFCLAdapter(**kwargs)
    if key == "halueval":
        from prax.eval.benchmarks.halueval import HaluEvalAdapter
        return HaluEvalAdapter(**kwargs)
    if key == "truthfulqa":
        from prax.eval.benchmarks.truthfulqa import TruthfulQAAdapter
        return TruthfulQAAdapter(**kwargs)
    if key == "gsm8k":
        from prax.eval.benchmarks.gsm8k import GSM8KAdapter
        return GSM8KAdapter(**kwargs)
    if key == "mmlu_pro":
        from prax.eval.benchmarks.mmlu_pro import MMLUProAdapter
        return MMLUProAdapter(**kwargs)
    if key == "gpqa":
        from prax.eval.benchmarks.gpqa import GPQAAdapter
        return GPQAAdapter(**kwargs)
    if key == "math":
        from prax.eval.benchmarks.math_bench import MATHAdapter
        return MATHAdapter(**kwargs)
    if key == "simpleqa":
        from prax.eval.benchmarks.simpleqa import SimpleQAAdapter
        return SimpleQAAdapter(**kwargs)
    if key == "humaneval":
        from prax.eval.benchmarks.humaneval import HumanEvalAdapter
        return HumanEvalAdapter(**kwargs)
    if key == "arc_agi_2":
        from prax.eval.benchmarks.arc_agi_2 import ARCAGI2Adapter
        return ARCAGI2Adapter(**kwargs)
    if key == "longcontext":
        from prax.eval.benchmarks.longcontext import LongContextAdapter
        return LongContextAdapter(**kwargs)
    if key == "agentsafety":
        from prax.eval.benchmarks.agentsafety import AgentSafetyAdapter
        return AgentSafetyAdapter(**kwargs)
    raise ValueError(f"unknown benchmark {name!r} (have: {', '.join(ADAPTER_NAMES)})")


def live_orchestrator_replay(*, tier: str = "low", model: str | None = None):
    """A ``replay_fn(prompt) -> str`` backed by the REAL Prax orchestrator (isolated
    workspace + telemetry), reusing the capability suite's executor. Needs API keys
    or a local model at run time — keyless CI never calls it. Pair with
    ``run_benchmark(adapter, live_orchestrator_replay(), ...)``.
    """
    from prax.eval.capability import orchestrator_executor
    counter = {"n": 0}

    def _replay(prompt: str) -> str:
        counter["n"] += 1
        run = orchestrator_executor(
            prompt, tier=tier, model_override=model, case_id=f"bench-{counter['n']}",
            fold_artifacts=False,  # benchmarks score the direct answer, not workspace files
        )
        return run.answer or ""

    return _replay


def _cost_model(tier: str, model: str | None) -> str | None:
    """The concrete model the run will actually use — for cost attribution."""
    if model:
        return model
    try:
        from prax.agent.model_tiers import resolve_model
        return resolve_model(tier)
    except Exception:
        return None


def run_benchmark_live(name: str, *, tier: str = "low", model: str | None = None,
                       out_dir=None, resume: bool = True, **adapter_kwargs) -> dict:
    """Run a named benchmark through the full Prax harness (isolated). Convenience
    over ``get_adapter`` + ``live_orchestrator_replay`` + ``run_benchmark``."""
    adapter = get_adapter(name, **adapter_kwargs)
    if out_dir is None:
        from prax.eval import PRAX_EVAL_DIR
        slug = model or tier
        out_dir = PRAX_EVAL_DIR / "suites" / f"bench-{adapter.name}-{slug}"
    return run_benchmark(
        adapter, live_orchestrator_replay(tier=tier, model=model),
        out_dir=out_dir, resume=resume, cost_model=_cost_model(tier, model),
    )


def run_benchmark_lift(name: str, *, tier: str = "low", model: str | None = None,
                       out_dir=None, resume: bool = True, **adapter_kwargs) -> dict:
    """Run a benchmark through BOTH the full harness and a BARE model (same model),
    scoring each with the adapter's deterministic scorer → the harness LIFT.

    The headline "does the scaffold help THIS model on THIS benchmark" number:
    ``harness_lift = full_pass_rate − bare_pass_rate`` (with the token cost of each,
    per the HAL discipline). Isolated + resumable; needs a model at run time.
    """
    from prax.eval.batch import run_batch
    from prax.eval.capability import bare_executor, orchestrator_executor

    adapter = get_adapter(name, **adapter_kwargs)
    by_id = {str(c["id"]): c for c in adapter.cases()}
    counter = {"n": 0}

    def _run_one(cid: str) -> dict:
        case = by_id[cid]
        prompt = adapter.prompt(case)
        counter["n"] += 1
        full = orchestrator_executor(prompt, tier=tier, model_override=model,
                                     case_id=f"lift-{name}-{counter['n']}",
                                     fold_artifacts=False)  # score the direct answer
        bare = bare_executor(prompt, tier=tier, model_override=model)
        gf, gb = adapter.score(case, full.answer or ""), adapter.score(case, bare.answer or "")
        return {
            "id": cid,
            "full_passed": bool(gf.get("passed")), "bare_passed": bool(gb.get("passed")),
            "full_tokens": full.tokens, "bare_tokens": bare.tokens,
            "full_error": full.error or None, "bare_error": bare.error or None,
        }

    def _summarize(results: list[dict]) -> dict:
        ok = [r for r in results if not r.get("full_error") and not r.get("bare_error")]
        n = len(ok)
        fr = round(sum(1 for r in ok if r["full_passed"]) / n, 3) if n else 0.0
        br = round(sum(1 for r in ok if r["bare_passed"]) / n, 3) if n else 0.0
        return {
            "benchmark": name, "cases": n,
            "full_pass_rate": fr, "bare_pass_rate": br,
            "harness_lift": round(fr - br, 3),
            "avg_full_tokens": round(sum(r.get("full_tokens", 0) for r in ok) / n) if n else 0,
            "avg_bare_tokens": round(sum(r.get("bare_tokens", 0) for r in ok) / n) if n else 0,
        }

    if out_dir is None:
        from prax.eval import PRAX_EVAL_DIR
        out_dir = PRAX_EVAL_DIR / "suites" / f"bench-lift-{name}-{model or tier}"
    return run_batch(list(by_id), _run_one, out_dir=out_dir, label=f"{name}-lift",
                     resume=resume, summarize=_summarize)


def run_all_benchmarks(replay_fn=None, *, tier: str = "low", model: str | None = None,
                       out_dir=None, resume: bool = True) -> dict:
    """Run EVERY registered benchmark and return a consolidated report.

    Uses a shared ``replay_fn`` (fake in tests) or the live orchestrator when
    ``replay_fn is None``. Returns ``{benchmarks: {name: aggregate}, avg_pass_rate,
    n_benchmarks}`` — a one-command coverage dashboard over the whole seam.
    """
    if replay_fn is None:
        replay_fn = live_orchestrator_replay(tier=tier, model=model)
    if out_dir is None:
        from prax.eval import PRAX_EVAL_DIR
        out_dir = PRAX_EVAL_DIR / "suites" / f"bench-all-{model or tier}"
    base = Path(out_dir)
    cost_model = _cost_model(tier, model)
    report: dict[str, dict] = {}
    for name in ADAPTER_NAMES:
        summary = run_benchmark(get_adapter(name), replay_fn,
                                out_dir=base / name, resume=resume, cost_model=cost_model)
        report[name] = summary.get("aggregate") or {}
    rates = [a.get("pass_rate", 0.0) for a in report.values() if a]
    total_tokens = sum(int(a.get("total_tokens", 0)) for a in report.values() if a)
    costs = [a.get("estimated_cost_usd") for a in report.values() if a]
    known = [c for c in costs if isinstance(c, (int, float))]
    return {
        "benchmarks": report,
        "n_benchmarks": len(report),
        "avg_pass_rate": round(sum(rates) / len(rates), 3) if rates else 0.0,
        "cost_model": cost_model,
        "total_tokens": total_tokens,
        # None if NO benchmark had a known price; else the sum of the known ones.
        "estimated_cost_usd": round(sum(known), 4) if known else None,
    }
