"""A/B replay tests — run the same scenario with different tier configurations.

Each experiment YAML in tests/integration/experiments/ defines:
  - A base scenario (user message to replay)
  - Tier overrides to apply

The test runs the scenario twice (baseline + experiment), judges both,
and produces a comparison report saved to .artifacts/experiments/.

Run::

    # All experiments
    pytest tests/integration/test_ab_replay.py -m ab -v -s

    # Single experiment
    pytest tests/integration/test_ab_replay.py -k upgrade_research -v -s
"""
from __future__ import annotations

import pytest

from tests.integration.experiment import discover_experiments, find_scenario, load_experiment

pytestmark = [pytest.mark.ab, pytest.mark.integration]

_EXPERIMENT_FILES = discover_experiments()


@pytest.mark.parametrize(
    "experiment_path",
    _EXPERIMENT_FILES,
    ids=lambda p: p.stem,
)
def test_ab_experiment(
    run_prax,
    run_prax_with_overrides,
    judge,
    save_artifacts,
    compare_runs,
    experiment_path,
):
    """Run baseline and experiment, judge both, compare."""
    exp = load_experiment(experiment_path)
    scenario = find_scenario(exp.scenario)

    # ---- Run A: baseline (no overrides) ----
    print(f"\n  === BASELINE run for '{exp.name}' ===")
    result_a = run_prax(
        scenario.message,
        timeout=scenario.max_duration,
        require_plugins=scenario.require_plugins,
    )
    assert result_a.response, "Baseline produced no response"

    # ---- Run B: with overrides ----
    print(f"\n  === EXPERIMENT run for '{exp.name}' ===")
    result_b = run_prax_with_overrides(
        scenario.message,
        overrides=exp.overrides,
        timeout=scenario.max_duration,
        require_plugins=scenario.require_plugins,
    )
    assert result_b.response, "Experiment produced no response"

    # ---- Judge both ----
    verdict_a = judge(
        result_a,
        message=scenario.message,
        expected_flow=scenario.expected_flow,
        quality_criteria=scenario.quality_criteria,
    )
    verdict_b = judge(
        result_b,
        message=scenario.message,
        expected_flow=scenario.expected_flow,
        quality_criteria=scenario.quality_criteria,
    )

    # ---- Save individual artifacts ----
    dir_a = save_artifacts(f"experiments/{exp.name}/baseline", result_a, verdict_a)
    dir_b = save_artifacts(f"experiments/{exp.name}/experiment", result_b, verdict_b)

    # ---- Compare ----
    report = compare_runs(
        result_a,
        result_b,
        experiment_name=exp.name,
        scenario=scenario.name,
        overrides=exp.overrides,
        baseline_verdict=verdict_a,
        experiment_verdict=verdict_b,
    )

    # ---- Print summary ----
    print(f"\n  {'='*60}")
    print(f"  A/B COMPARISON: {exp.name}")
    print(f"  {'='*60}")
    print(f"  Baseline:   ${report.baseline_cost:.4f} | {report.baseline_duration:.1f}s | {'PASS' if verdict_a.passed else 'FAIL'}")
    print(f"  Experiment: ${report.experiment_cost:.4f} | {report.experiment_duration:.1f}s | {'PASS' if verdict_b.passed else 'FAIL'}")
    print(f"  Cost delta: {report.cost_delta_pct:+.1f}%")
    print(f"  Time delta: {report.duration_delta_pct:+.1f}%")
    if report.tier_diff:
        for diff in report.tier_diff:
            print(f"  Tier diff [{diff['span']}]: {diff['baseline_tiers']} → {diff['experiment_tiers']}")
    print(f"  Artifacts: {dir_a.parent}")
    print(f"  Report: {dir_a.parent / 'comparison.md'}")

    # The test itself does NOT assert that the experiment is better —
    # that's for the human to decide from the report.  We only assert
    # both runs completed successfully (non-empty response).
