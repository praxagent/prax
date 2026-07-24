"""The committed public scorecard — turn a matrix run into a trackable record.

`make eval-matrix` runs every benchmark on real data; this module distils that run
into a small, **aggregates-only** record committed under `docs/eval-results/` so
progress is public and trend-tracked. The hard rule (contamination firewall,
CLAUDE.md): the committed record carries ONLY pass-rate / n / tokens / cost / config
/ commit — **NEVER a benchmark question or answer.** `assert_no_leak` enforces it,
and a test pins that a leak would fail loudly.

Raw per-case runs stay in `$PRAX_EVAL_DIR` (never committed); this is the distilled,
publishable layer on top.
"""
from __future__ import annotations

import json
from pathlib import Path

RESULTS_DIR = Path("docs/eval-results")

# Per-benchmark keys we publish (all aggregates). Anything else is dropped so a
# future field can't silently leak per-case content.
_BENCH_KEYS = ("pass_rate", "n", "errors", "total_tokens", "dataset", "variant")

# A matrix run with more than this fraction of cases failing to execute (auth,
# timeout, provider errors) is untrustworthy — its "scores" are mostly noise. The
# scorecard refuses to record such a run (the first baseline was ~65% auth-failed
# and never should have been published). Override with PRAX_SCORECARD_MAX_ERROR_RATE.
MAX_ERROR_RATE = 0.20


class UnhealthyRunError(RuntimeError):
    """A matrix run had too high an error rate to be a trustworthy record."""


def assert_run_healthy(report: dict) -> None:
    """Raise :class:`UnhealthyRunError` if the run's error rate exceeds the ceiling.

    Guards the public scorecard against publishing a broken run (mass auth/infra
    failures scored as zeros). Reads the ceiling from PRAX_SCORECARD_MAX_ERROR_RATE.
    """
    import os
    try:
        ceiling = float(os.environ.get("PRAX_SCORECARD_MAX_ERROR_RATE", "") or MAX_ERROR_RATE)
    except ValueError:
        ceiling = MAX_ERROR_RATE
    rate = float(report.get("error_rate") or 0.0)
    errs, att = report.get("total_errors", 0), report.get("total_attempted", 0)
    if rate > ceiling:
        raise UnhealthyRunError(
            f"run error-rate {rate:.0%} ({errs}/{att} cases failed to execute) exceeds "
            f"the {ceiling:.0%} ceiling — NOT recording. Fix the run (commonly model "
            f"auth: check OPENROUTER_API_KEY / OPENROUTER_BASE_URL) and re-run. Override "
            f"with PRAX_SCORECARD_MAX_ERROR_RATE if you really mean to record it.")

# Substrings that would indicate per-case content leaked into the record. Checked
# recursively over the whole record; presence of any raises.
_FORBIDDEN_KEYS = frozenset({
    "question", "answer", "answers", "prompt", "response", "input", "output",
    "answer_preview", "checks", "cases", "case", "expected", "reference", "label",
})


def build_record(report: dict, *, git_commit: str, model: str, provider: str,
                 matrix_limit: int, timestamp: str) -> dict:
    """Distil a ``run_all_benchmarks`` report into an aggregates-only record."""
    benchmarks: dict[str, dict] = {}
    for name, agg in (report.get("benchmarks") or {}).items():
        if not agg:
            continue
        proto = agg.get("protocol") or {}
        benchmarks[name] = {
            "pass_rate": agg.get("pass_rate"),
            "n": agg.get("graded"),
            "errors": agg.get("errors", 0),       # cases that failed to execute
            "total_tokens": agg.get("total_tokens"),
            "dataset": proto.get("dataset"),      # "real" | "seed"
            "variant": proto.get("variant"),
        }
    record = {
        "timestamp": timestamp,
        "git_commit": git_commit,
        "model": model,
        "provider": provider,
        "matrix_limit": matrix_limit,
        # Stated in the record itself so a reader of the raw JSON — with no access
        # to MATRIX.md's header — cannot mistake a capped sample for a suite run.
        "sampling": {
            "sampled": True,
            "cases_per_benchmark_cap": matrix_limit,
            "full_suite": False,
            "comparable_to_public_leaderboards": False,
            "note": ("Each benchmark was capped at matrix_limit cases; benchmarks "
                     "with dataset='seed' ran on a small in-repo case set, not the "
                     "public benchmark of that name."),
        },
        "aggregate": {
            "n_benchmarks": report.get("n_benchmarks"),
            # Blended across real + seed benchmarks — kept for continuity, but
            # `by_provenance` is the honest read. Never headline this one.
            "avg_pass_rate": report.get("avg_pass_rate"),
            "by_provenance": split_averages(benchmarks),
            "total_tokens": report.get("total_tokens"),
            "estimated_cost_usd": report.get("estimated_cost_usd"),
            # Run health — so a reader can see how much of the run actually executed.
            "error_rate": report.get("error_rate", 0.0),
            "total_errors": report.get("total_errors", 0),
            "total_attempted": report.get("total_attempted", 0),
        },
        "benchmarks": benchmarks,
    }
    assert_no_leak(record)
    return record


def assert_no_leak(obj, _path: str = "record") -> None:
    """Raise if any forbidden (per-case) key appears anywhere in *obj*.

    The contamination firewall as an assertion: the committed scorecard must be
    aggregates only. Fails loudly rather than silently publishing a question/answer.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _FORBIDDEN_KEYS:
                raise ValueError(f"scorecard leak: forbidden key {k!r} at {_path} "
                                 "— the committed record must be aggregates only")
            assert_no_leak(v, f"{_path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_no_leak(v, f"{_path}[{i}]")


def write_record(record: dict, *, root: Path | None = None, run_id: str) -> Path:
    """Write the immutable per-run record under ``docs/eval-results/<year>/``."""
    assert_no_leak(record)
    root = root or RESULTS_DIR
    year = str(record["timestamp"])[:4]
    date = str(record["timestamp"])[:10]
    out = root / year / f"{date}-{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=False) + "\n")
    return out


def load_records(root: Path | None = None) -> list[dict]:
    root = root or RESULTS_DIR
    records = []
    for p in sorted(root.glob("*/*.json")):
        try:
            records.append(json.loads(p.read_text()))
        except Exception:  # noqa: BLE001 - one bad record shouldn't break the dashboard
            continue
    return records


def split_averages(benchmarks: dict) -> dict:
    """Average pass-rate split by dataset provenance, plus the sampled case count.

    A single average blended across real-dataset benchmarks and small
    self-authored *seed* sets is the most misleading number a scorecard can
    publish — a 1.00 on a 6-case seed set drags the headline up while saying
    nothing about the public benchmark. So we never render one blended number:
    callers get ``real`` and ``seed`` separately, with the n behind each.
    """
    real, seed, n_real, n_seed = [], [], 0, 0
    for b in (benchmarks or {}).values():
        pr, n = (b or {}).get("pass_rate"), (b or {}).get("n") or 0
        if not isinstance(pr, (int, float)):
            continue
        if (b or {}).get("dataset") == "real":
            real.append(pr)
            n_real += n
        else:
            seed.append(pr)
            n_seed += n
    return {
        "real_avg": (sum(real) / len(real)) if real else None,
        "seed_avg": (sum(seed) / len(seed)) if seed else None,
        "n_real_benchmarks": len(real), "n_seed_benchmarks": len(seed),
        "cases_real": n_real, "cases_seed": n_seed,
        "cases_graded": n_real + n_seed,
    }


_HEADER = """# Eval Matrix — public scorecard

> ## ⚠️ These are SAMPLED results — NOT full benchmark suites.
>
> Each run grades at most `cap/bench` cases per benchmark (see the table). A full
> public benchmark suite is typically hundreds to thousands of cases, so a score
> here is a **small sample**, not a suite result. **Do not quote these numbers as
> benchmark scores or compare them to published leaderboards** — the sample is far
> too small to support that, and the confidence interval on 10 cases is enormous.
>
> **`*` marks a seed benchmark**: a small case set authored *in this repo* because
> no full dataset was fetched. Those rows smoke-test the harness path — they are
> **not** the public benchmark of that name and carry no external comparability.
>
> Cells are `pass_rate(n)`, where `n` is the number of cases actually graded.
> Averages are reported **split** by provenance (real vs seed) and never blended.
>
> Prax is a **harness, not a model**: a number here is *this model through this
> harness*. The harness's own contribution is the **lift** (full vs bare, same
> model), not the absolute score.

Aggregates only (contamination firewall): pass-rate / n / tokens / config / commit.
Raw per-case runs live in `$PRAX_EVAL_DIR`, never here. Generated by
`make eval-matrix RECORD=1`.

"""


def render_dashboard(records: list[dict]) -> str:
    """Render MATRIX.md — the latest run in detail, then one row per run.

    Every number is rendered with the two facts that make it honest: the **n** it
    was measured over and whether the dataset was **real or a seed set**. Dropping
    either turns a 10-case sample into something that reads as a suite score.
    """
    if not records:
        return "# Eval Matrix — scorecard\n\n_No runs recorded yet._\n"
    records = sorted(records, key=lambda r: r.get("timestamp", ""))
    bench_names: list[str] = []
    for r in records:
        for name in (r.get("benchmarks") or {}):
            if name not in bench_names:
                bench_names.append(name)
    bench_names.sort()

    out = _HEADER + _render_latest(records[-1]) + "\n## All runs\n\n"
    cols = ["date", "commit", "model", "cap/bench", "cases", "real avg", "seed avg",
            "err%"] + bench_names
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in records:
        agg, b = r.get("aggregate") or {}, r.get("benchmarks") or {}
        sp, er = split_averages(b), agg.get("error_rate")
        row = [
            str(r.get("timestamp", ""))[:10],
            str(r.get("git_commit", ""))[:7],
            str(r.get("model", "")).split("/")[-1],
            f"≤{r.get('matrix_limit')}" if r.get("matrix_limit") else "—",
            str(sp["cases_graded"]),
            _fmt_avg(sp["real_avg"], sp["n_real_benchmarks"]),
            _fmt_avg(sp["seed_avg"], sp["n_seed_benchmarks"]),
            (f"{er:.0%}" if isinstance(er, (int, float)) else "—"),
        ] + [_fmt_cell(b.get(n)) for n in bench_names]
        lines.append("| " + " | ".join(row) + " |")
    return out + "\n".join(lines) + "\n"


def _render_latest(rec: dict) -> str:
    """Per-benchmark detail for the most recent run — the honest primary view."""
    b = rec.get("benchmarks") or {}
    sp = split_averages(b)
    date, commit = str(rec.get("timestamp", ""))[:10], str(rec.get("git_commit", ""))[:7]
    cap = rec.get("matrix_limit")
    s = (f"## Latest run — {date} (`{commit}`, {rec.get('model', '?')}, "
         f"≤{cap} cases/benchmark)\n\n")
    s += ("| benchmark | pass rate | n graded | dataset | comparable to public "
          "leaderboard? |\n|---|---|---|---|---|\n")
    for name in sorted(b):
        e = b.get(name) or {}
        real = e.get("dataset") == "real"
        s += (f"| {name}{'' if real else '*'} | {_fmt(e.get('pass_rate'))} | "
              f"{e.get('n') or 0} | {'real (sampled)' if real else 'seed (in-repo)'} | "
              f"{'no — sample too small' if real else 'no — not the public set'} |\n")
    s += (f"\n**Real-dataset benchmarks:** {sp['n_real_benchmarks']}, mean "
          f"{_fmt(sp['real_avg'])} over {sp['cases_real']} sampled cases. "
          f"**Seed benchmarks:** {sp['n_seed_benchmarks']}, mean "
          f"{_fmt(sp['seed_avg'])} over {sp['cases_seed']} in-repo cases. "
          f"These are reported separately on purpose — a blended average would be "
          f"misleading.\n\n")
    return s


def _fmt(x) -> str:
    return f"{x:.2f}" if isinstance(x, (int, float)) else "—"


def _fmt_avg(x, k: int) -> str:
    """An average is meaningless without how many benchmarks it spans."""
    return f"{x:.2f} ({k})" if isinstance(x, (int, float)) else "—"


def _fmt_cell(entry) -> str:
    """``pass_rate(n)`` — plus ``*`` when the row is a seed set, not the real one."""
    e = entry or {}
    pr = e.get("pass_rate")
    if not isinstance(pr, (int, float)):
        return "—"
    return f"{pr:.2f}({e.get('n') or 0}){'' if e.get('dataset') == 'real' else '*'}"


def _fmt(x) -> str:
    return f"{x:.2f}" if isinstance(x, (int, float)) else "—"


def write_dashboard(root: Path | None = None) -> Path:
    root = root or RESULTS_DIR
    out = root / "MATRIX.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard(load_records(root)))
    return out
