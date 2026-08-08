"""A kill condition must not rest on the mean of a heavy-tailed per-case cost.

Grounded in campaign spiral-20260808 (2026-08-08). The condition was written
against `avg_tokens`. Aggregate |baseline - replicate| came to 349,279 tokens
while the SUM of per-case absolute differences was 3,529,759 — 10x larger —
because one case fell ~1.03M and another rose ~0.93M between two runs of the
SAME configuration. They cancelled, the aggregate looked stable, and a -30%
"effect" that was entirely inside per-case noise survived the kill condition.

`flag_ab.py` had printed a warning about exactly this at the end of the very
campaign it broke. A guard in output nobody reads while designing the
experiment is not a guard — hence this one, at registration time.
"""

import pytest

from prax.eval import prereg


def _kill(_):
    return False


class TestAggregateMeanIsRefused:
    def test_the_actual_spiral_condition_is_now_refused(self):
        """The verbatim condition from spiral-20260808. If this ever stops
        raising, the guard has stopped guarding the case it was built for."""
        with pytest.raises(ValueError, match="heavy-tailed"):
            prereg.register(
                experiment="spiral-regression-check",
                hypothesis="spiral recovery reduces token spend",
                kill_condition=(
                    "KILLED if spiral_on's avg_tokens is not at least 10% below "
                    "baseline, OR if the token gap between spiral_on and "
                    "baseline is no larger than the gap between baseline and "
                    "baseline_replicate"
                ),
                kill=_kill,
            )

    @pytest.mark.parametrize("metric", [
        "avg_tokens", "mean_tokens", "avg_cost", "mean_duration", "avg_latency",
    ])
    def test_each_heavy_tailed_aggregate_is_caught(self, metric):
        with pytest.raises(ValueError, match="heavy-tailed"):
            prereg.register(experiment="e", hypothesis="h",
                            kill_condition=f"KILLED if {metric} does not drop 10%",
                            kill=_kill)

    def test_case_insensitive(self):
        with pytest.raises(ValueError, match="heavy-tailed"):
            prereg.register(experiment="e", hypothesis="h",
                            kill_condition="KILLED unless AVG_TOKENS falls",
                            kill=_kill)


class TestLegitimateConditionsStillWork:
    def test_per_case_condition_is_accepted(self):
        """The correct formulation — the guard must not block the fix."""
        reg = prereg.register(
            experiment="e", hypothesis="h",
            kill_condition=("KILLED if the median per-case token delta is "
                            "smaller than the summed per-case noise floor"),
            kill=_kill)
        assert reg.kill_condition

    def test_bounded_outcome_means_are_fine(self):
        """A mean over a BINARY outcome is well-behaved. Blocking pass_rate
        would make the rule noise, and a rule that fires on everything gets
        overridden reflexively."""
        reg = prereg.register(
            experiment="e", hypothesis="h",
            kill_condition="KILLED if pass_rate drops by more than 2 cases",
            kill=_kill)
        assert reg.kill_condition

    def test_empty_condition_still_refused(self):
        """The original guard must survive the new one."""
        with pytest.raises(ValueError, match="non-empty"):
            prereg.register(experiment="e", hypothesis="h",
                            kill_condition="   ", kill=_kill)


class TestDeliberateOverride:
    def test_override_is_allowed(self):
        reg = prereg.register(
            experiment="e", hypothesis="h",
            kill_condition="KILLED if avg_tokens does not drop 10%",
            kill=_kill, allow_aggregate_mean=True)
        assert reg.kill_condition

    def test_override_is_recorded_not_silent(self, tmp_path, monkeypatch):
        """An override must be auditable after the fact — otherwise the escape
        hatch quietly becomes the default."""
        monkeypatch.setenv("PRAX_EVAL_DIR", str(tmp_path))
        prereg.register(experiment="override-audit", hypothesis="h",
                        kill_condition="KILLED if avg_tokens does not drop",
                        kill=_kill, allow_aggregate_mean=True)
        entries = [e for e in prereg.history("override-audit")
                   if e.get("event") == "registered"]
        assert entries, "registration was not persisted"
        assert entries[-1].get("allow_aggregate_mean") is True
        assert "avg_tokens" in entries[-1].get("aggregate_metrics", [])


def test_detector_reports_which_metrics():
    assert prereg.uses_heavy_tailed_aggregate("avg_tokens and avg_cost") == [
        "avg_tokens", "avg_cost"]
    assert prereg.uses_heavy_tailed_aggregate("pass_rate only") == []
    assert prereg.uses_heavy_tailed_aggregate("") == []
