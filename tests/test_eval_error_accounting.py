"""An errored case must not be able to improve the score.

The capability aggregate used to drop every errored run from the pass rate AND
from avg_tokens. A real run then hid its worst case: the agent burned 727k
tokens over 180s on an unsatisfiable request, never answered, timed out — and
the suite reported 87.5% (n=8) off the remaining cases, with the cost blowout
absent from the token average too.

That is the same defect as the MATRIX.md sampling incident: honest data, a
rendered number that quietly excluded its own worst input. These tests pin the
repaired contract.
"""

import pytest

from prax.eval import is_infrastructure_error
from prax.eval.capability import summarize_capability_results
from prax.eval.stats import attach_ci

# --------------------------------------------------------------------------- #
# Error attribution
# --------------------------------------------------------------------------- #

class TestErrorAttribution:
    def test_agent_timeout_is_not_infrastructure(self):
        """The load-bearing case. Running out of budget is a capability
        outcome, not an environment fault."""
        assert is_infrastructure_error(
            "TimeoutError: task exceeded 180.0s wall-clock limit") is False

    def test_no_error_is_not_infrastructure(self):
        assert is_infrastructure_error(None) is False
        assert is_infrastructure_error("") is False

    @pytest.mark.parametrize("err", [
        "ConnectionRefusedError: [Errno 111] Connection refused",
        "APIError: 503 Service Unavailable",
        "openai.RateLimitError: rate limit exceeded",
        "OSError: [Errno 28] No space left on device",
    ])
    def test_environment_faults_are_infrastructure(self, err):
        assert is_infrastructure_error(err) is True

    def test_unknown_errors_count_against_the_agent(self):
        """Fail-closed: an unrecognised error is the agent's until someone
        deliberately classifies it otherwise. The opposite default is what let
        the timeout vanish."""
        assert is_infrastructure_error("ValueError: something odd happened") is False


# --------------------------------------------------------------------------- #
# The aggregate contract
# --------------------------------------------------------------------------- #

def summarize(results):
    """The REAL aggregator, plus the CI/rendering step run_batch applies.

    Deliberately not a reimplementation: the original defect survived because
    the only test of this logic was a copy of it. A mirror test asserts that
    the mirror is correct.
    """
    return attach_ci(summarize_capability_results(results))


GOOD = [{"passed": True, "tokens": 30_000} for _ in range(7)]
BLOWOUT = {"passed": False, "tokens": 727_000,
           "error": "TimeoutError: task exceeded 180.0s wall-clock limit"}


class TestAggregateAccounting:
    def test_timeout_lowers_the_pass_rate(self):
        without = summarize(GOOD)
        with_blowout = summarize([*GOOD, BLOWOUT])
        assert without["pass_rate"] == 1.0
        assert with_blowout["pass_rate"] < without["pass_rate"], (
            "adding a timed-out case must not leave the score untouched")
        assert with_blowout["graded"] == 8

    def test_timeout_tokens_reach_the_cost_axis(self):
        """Never report accuracy without cost — and never let the most
        expensive run be the one that escapes the average."""
        without = summarize(GOOD)
        with_blowout = summarize([*GOOD, BLOWOUT])
        assert with_blowout["avg_tokens"] > without["avg_tokens"] * 2

    def test_infrastructure_fault_is_excluded_but_reported(self):
        infra = {"passed": False, "tokens": 500,
                 "error": "ConnectionRefusedError: Connection refused"}
        agg = summarize([*GOOD, infra])
        assert agg["graded"] == 7, "an infra fault must not be blamed on the agent"
        assert agg["excluded_infra"] == 1
        assert "excluded (infra)" in agg["pass_rate_str"], (
            "an exclusion that appears only in a sibling field is a silent "
            "exclusion — it must ride along with the number")

    def test_rendered_string_names_agent_errors(self):
        agg = summarize([*GOOD, BLOWOUT])
        assert "scored as failure (agent error)" in agg["pass_rate_str"]

    def test_clean_run_string_stays_clean(self):
        """No caveats invented when there is nothing to caveat."""
        s = summarize(GOOD)["pass_rate_str"]
        assert "infra" not in s and "agent error" not in s

    def test_failing_harder_can_never_score_better(self):
        """The property, stated directly: for any case, erroring must score no
        better than answering wrongly."""
        wrong = summarize([*GOOD, {"passed": False, "tokens": 30_000}])
        errored = summarize([*GOOD, BLOWOUT])
        assert errored["pass_rate"] <= wrong["pass_rate"]
