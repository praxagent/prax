"""Scoring self-reported confidence against real outcomes."""
import pytest

from prax.eval.calibration import score_calibration


class TestEmptyAndInvalid:
    def test_empty_is_none_not_a_flattering_zero(self):
        r = score_calibration([])
        assert r.n == 0
        assert r.brier is None and r.overconfidence is None
        assert "no resolved forecasts" in r.summary()

    def test_missing_confidence_is_dropped_not_assumed_half(self):
        """A claim with no probability is not a forecast; inventing 0.5 would
        manufacture data."""
        r = score_calibration([(None, True), (None, False), (0.9, True)])
        assert r.n == 1

    @pytest.mark.parametrize("bad", ["", "abc", object(), 1.5, -0.2])
    def test_unparseable_or_out_of_range_dropped(self, bad):
        assert score_calibration([(bad, True)]).n == 0


class TestScores:
    def test_perfect_forecaster_scores_zero(self):
        r = score_calibration([(1.0, True), (0.0, False), (1.0, True)])
        assert r.brier == 0.0
        assert r.overconfidence == 0.0

    def test_always_half_scores_the_shrug_baseline(self):
        r = score_calibration([(0.5, True), (0.5, False)])
        assert r.brier == 0.25

    def test_confident_and_wrong_is_worse_than_a_shrug(self):
        r = score_calibration([(0.95, False), (0.95, False)])
        assert r.brier > 0.25

    def test_overconfidence_is_positive_when_claiming_too_much(self):
        """The observed failure mode: high confidence, low success."""
        pairs = [(0.9, False)] * 8 + [(0.9, True)] * 2
        r = score_calibration(pairs)
        assert r.mean_confidence == 0.9
        assert r.base_rate == 0.2
        assert r.overconfidence == pytest.approx(0.7)

    def test_underconfidence_is_negative(self):
        r = score_calibration([(0.2, True)] * 10)
        assert r.overconfidence == pytest.approx(-0.8)


class TestBins:
    def test_bins_group_by_decile_and_report_observed_rate(self):
        pairs = [(0.05, False), (0.05, False), (0.95, True), (0.95, False)]
        r = score_calibration(pairs)
        labels = {b[0]: b for b in r.bins}
        assert labels["0.0-0.1"][1] == 2 and labels["0.0-0.1"][3] == 0.0
        assert labels["0.9-1.0"][1] == 2 and labels["0.9-1.0"][3] == 0.5

    def test_confidence_of_exactly_one_lands_in_the_top_bin(self):
        r = score_calibration([(1.0, True)])
        assert r.bins[-1][0] == "0.9-1.0" and r.bins[-1][1] == 1

    def test_empty_bins_are_omitted_not_reported_as_zero(self):
        r = score_calibration([(0.55, True)])
        assert [b[0] for b in r.bins] == ["0.5-0.6"]


def test_summary_is_readable():
    r = score_calibration([(0.9, False)] * 9 + [(0.9, True)])
    s = r.summary()
    assert "brier=" in s and "overconfidence=+" in s and "n=10" in s


class TestHarborJobScoring:
    """Reading confidences back out of a real harbor job layout."""

    def _trial(self, tmp_path, name, reward, confidence):
        import json
        d = tmp_path / "2026-01-01__00-00-00" / f"{name}__abc123"
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps({
            "verifier_result": ({"rewards": {"reward": reward}}
                                if reward is not None else {}),
            "agent_result": {"metadata": {"confidence": confidence}},
        }))

    def test_resolves_confidence_against_the_hidden_verifier(self, tmp_path):
        from prax.eval.calibration import score_harbor_job
        self._trial(tmp_path, "a", 1.0, 0.9)
        self._trial(tmp_path, "b", 0.0, 0.9)
        r = score_harbor_job(tmp_path)
        assert r.n == 2
        assert r.base_rate == 0.5 and r.mean_confidence == 0.9
        assert r.overconfidence == pytest.approx(0.4)

    def test_unscored_trials_are_skipped_not_counted_as_wrong(self, tmp_path):
        """An infra timeout leaves the forecast unresolved — skipping it is
        the honest move; scoring it as a miss would punish the agent for the
        box falling over."""
        from prax.eval.calibration import score_harbor_job
        self._trial(tmp_path, "a", 1.0, 0.8)
        self._trial(tmp_path, "b", None, 0.8)
        assert score_harbor_job(tmp_path).n == 1

    def test_trials_without_a_confidence_are_dropped(self, tmp_path):
        from prax.eval.calibration import score_harbor_job
        self._trial(tmp_path, "a", 0.0, None)
        assert score_harbor_job(tmp_path).n == 0

    def test_missing_directory_is_empty_not_an_error(self, tmp_path):
        from prax.eval.calibration import score_harbor_job
        assert score_harbor_job(tmp_path / "nope").n == 0
