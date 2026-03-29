"""Compare two IntegrationResult runs and produce a structured report.

Used by A/B replay tests to quantify the impact of tier overrides on
cost, latency, and output quality.

Usage::

    from tests.integration.compare import build_comparison

    report = build_comparison(
        baseline=result_a,
        experiment=result_b,
        experiment_name="upgrade-research",
        overrides={"subagent_research": {"tier": "medium"}},
    )
    print(report.summary_md)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.integration.conftest import IntegrationResult


@dataclass
class ComparisonReport:
    """Side-by-side comparison of baseline vs experiment run."""

    experiment_name: str
    scenario: str
    overrides: dict
    baseline_cost: float
    experiment_cost: float
    cost_delta_pct: float
    baseline_duration: float
    experiment_duration: float
    duration_delta_pct: float
    baseline_tiers: list[dict]
    experiment_tiers: list[dict]
    tier_diff: list[dict]
    baseline_passed: bool | None
    experiment_passed: bool | None
    summary_md: str
    raw: dict = field(default_factory=dict)


def _pct_delta(baseline: float, experiment: float) -> float:
    """Percentage change from baseline to experiment."""
    if baseline == 0:
        return 0.0 if experiment == 0 else 100.0
    return ((experiment - baseline) / baseline) * 100


def _tier_summary(choices: list[dict]) -> dict[str, int]:
    """Compact tier→model counts."""
    counts: dict[str, int] = {}
    for tc in choices:
        key = f"{tc.get('tier_requested', '?')}→{tc.get('model', '?')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _build_tier_diff(baseline: list[dict], experiment: list[dict]) -> list[dict]:
    """Per-span tier differences between runs."""
    def _by_span(choices: list[dict]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for tc in choices:
            span = tc.get("span_name", "unknown")
            tier = tc.get("tier_requested", "?")
            result.setdefault(span, []).append(tier)
        return result

    base_spans = _by_span(baseline)
    exp_spans = _by_span(experiment)
    all_spans = sorted(set(base_spans) | set(exp_spans))

    diffs = []
    for span in all_spans:
        base_tiers = base_spans.get(span, [])
        exp_tiers = exp_spans.get(span, [])
        if base_tiers != exp_tiers:
            diffs.append({
                "span": span,
                "baseline_tiers": base_tiers,
                "experiment_tiers": exp_tiers,
            })
    return diffs


def build_comparison(
    *,
    baseline: IntegrationResult,
    experiment: IntegrationResult,
    experiment_name: str,
    scenario: str = "",
    overrides: dict | None = None,
    baseline_verdict: object | None = None,
    experiment_verdict: object | None = None,
) -> ComparisonReport:
    """Build a structured comparison report from two runs."""
    overrides = overrides or {}

    b_cost = baseline.cost.get("total_cost_usd", 0)
    e_cost = experiment.cost.get("total_cost_usd", 0)
    cost_delta = _pct_delta(b_cost, e_cost)

    dur_delta = _pct_delta(baseline.duration_seconds, experiment.duration_seconds)

    tier_diff = _build_tier_diff(baseline.tier_choices, experiment.tier_choices)

    b_passed = getattr(baseline_verdict, "passed", None) if baseline_verdict else None
    e_passed = getattr(experiment_verdict, "passed", None) if experiment_verdict else None

    # Build overrides table
    override_lines = []
    for comp, vals in sorted(overrides.items()):
        for key, val in sorted(vals.items()):
            override_lines.append(f"| {comp} | {key} | {val} |")
    overrides_table = (
        "| Component | Setting | Value |\n|---|---|---|\n"
        + "\n".join(override_lines)
    ) if override_lines else "_None_"

    # Cost table
    b_in = baseline.cost.get("total_input_tokens", 0)
    e_in = experiment.cost.get("total_input_tokens", 0)
    b_out = baseline.cost.get("total_output_tokens", 0)
    e_out = experiment.cost.get("total_output_tokens", 0)
    b_calls = baseline.cost.get("total_llm_calls", 0)
    e_calls = experiment.cost.get("total_llm_calls", 0)

    # Tier choices table
    b_tier_summary = _tier_summary(baseline.tier_choices)
    e_tier_summary = _tier_summary(experiment.tier_choices)

    # Quality row
    def _qual(v):
        if v is None:
            return "N/A"
        return "PASS" if v else "FAIL"

    quality_delta = "inconclusive"
    if b_passed is not None and e_passed is not None:
        if b_passed == e_passed:
            quality_delta = "same"
        elif e_passed and not b_passed:
            quality_delta = "better"
        elif b_passed and not e_passed:
            quality_delta = "worse"

    md = f"""# A/B Comparison: {experiment_name}
**Scenario:** {scenario}

## Overrides Applied
{overrides_table}

## Cost
| Metric | Baseline | Experiment | Delta |
|--------|----------|------------|-------|
| Total cost | ${b_cost:.4f} | ${e_cost:.4f} | {cost_delta:+.1f}% |
| Input tokens | {b_in:,} | {e_in:,} | {_pct_delta(b_in, e_in):+.1f}% |
| Output tokens | {b_out:,} | {e_out:,} | {_pct_delta(b_out, e_out):+.1f}% |
| LLM calls | {b_calls} | {e_calls} | {_pct_delta(b_calls, e_calls):+.1f}% |

## Timing
| Metric | Baseline | Experiment | Delta |
|--------|----------|------------|-------|
| Duration | {baseline.duration_seconds:.1f}s | {experiment.duration_seconds:.1f}s | {dur_delta:+.1f}% |

## Tier Choices
### Baseline
{json.dumps(b_tier_summary, indent=2)}

### Experiment
{json.dumps(e_tier_summary, indent=2)}

### Differences by Span
{json.dumps(tier_diff, indent=2) if tier_diff else "_No differences_"}

## Quality
| Criterion | Baseline | Experiment |
|-----------|----------|------------|
| Judge verdict | {_qual(b_passed)} | {_qual(e_passed)} |
| Quality delta | {quality_delta} | |

## Response Preview
### Baseline (first 500 chars)
```
{baseline.response[:500]}
```

### Experiment (first 500 chars)
```
{experiment.response[:500]}
```

## Execution Graphs
### Baseline
```
{baseline.graph_summary}
```

### Experiment
```
{experiment.graph_summary}
```
"""

    raw = {
        "experiment": experiment_name,
        "scenario": scenario,
        "overrides": overrides,
        "baseline": {
            "cost_usd": b_cost,
            "duration_s": baseline.duration_seconds,
            "input_tokens": b_in,
            "output_tokens": b_out,
            "llm_calls": b_calls,
            "passed": b_passed,
            "tier_choices": baseline.tier_choices,
        },
        "experiment": {
            "cost_usd": e_cost,
            "duration_s": experiment.duration_seconds,
            "input_tokens": e_in,
            "output_tokens": e_out,
            "llm_calls": e_calls,
            "passed": e_passed,
            "tier_choices": experiment.tier_choices,
        },
        "deltas": {
            "cost_pct": round(cost_delta, 1),
            "duration_pct": round(dur_delta, 1),
            "quality": quality_delta,
        },
        "tier_diff": tier_diff,
    }

    return ComparisonReport(
        experiment_name=experiment_name,
        scenario=scenario,
        overrides=overrides,
        baseline_cost=b_cost,
        experiment_cost=e_cost,
        cost_delta_pct=round(cost_delta, 1),
        baseline_duration=baseline.duration_seconds,
        experiment_duration=experiment.duration_seconds,
        duration_delta_pct=round(dur_delta, 1),
        baseline_tiers=baseline.tier_choices,
        experiment_tiers=experiment.tier_choices,
        tier_diff=tier_diff,
        baseline_passed=b_passed,
        experiment_passed=e_passed,
        summary_md=md,
        raw=raw,
    )
