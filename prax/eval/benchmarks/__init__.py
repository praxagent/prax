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
) -> dict:
    """Run every case of *adapter* through *replay_fn* and score deterministically.

    ``replay_fn(prompt) -> response`` is the executor (the live orchestrator, a bare
    model, or a fake in tests). Resumable + summarized via ``run_batch``.
    """
    from prax.eval.batch import run_batch

    by_id = {str(c["id"]): c for c in adapter.cases()}

    def _run_one(case_id: str) -> dict:
        case = by_id[case_id]
        response = replay_fn(adapter.prompt(case))
        graded = adapter.score(case, response) or {}
        return {
            "id": case_id,
            "passed": bool(graded.get("passed")),
            "score": float(graded.get("score", 0.0)),
            "checks": graded.get("checks"),
            "answer_preview": (response or "")[:300],
        }

    def _summarize(results: list[dict]) -> dict:
        graded = [r for r in results if not r.get("error")]
        n = len(graded)
        passed = sum(1 for r in graded if r.get("passed"))
        return {
            "benchmark": adapter.name,
            "graded": n,
            "passed": passed,
            "pass_rate": round(passed / n, 3) if n else 0.0,
            "avg_score": round(sum(r.get("score", 0.0) for r in graded) / n, 3) if n else 0.0,
        }

    return run_batch(
        list(by_id), _run_one, out_dir=out_dir, label=adapter.name,
        concurrency=concurrency, resume=resume, summarize=_summarize,
    )


# ---------------------------------------------------------------------------
# Registry + live executor — make the adapters runnable against the real harness
# ---------------------------------------------------------------------------

ADAPTER_NAMES = ("ifeval", "injecagent", "sycophancy")


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
        )
        return run.answer or ""

    return _replay


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
        out_dir=out_dir, resume=resume,
    )
