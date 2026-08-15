"""Valid time and record time are independent axes, and may disagree.

# INVARIANT: valid_from/valid_until describe when a fact was TRUE IN THE WORLD;
# first_seen/last_seen describe when WE OBSERVED it. A fact learned in August
# can stop being true in March.

#65. Prax was already bitemporal in structure — RELATES_TO carries both pairs,
`add_relation` accepts `valid_from`, and the extraction prompt asks for it — but
the CLOSING boundary collapsed: `supersede_relation` hardcoded
`valid_until = now`, so it recorded when we found out rather than when the fact
ceased to hold.

The failure that motivates it: told in August "I moved to Berlin back in
March", consolidation closed `lives_in -> Paris` at AUGUST. A point-in-time
query for April then answers Paris — wrong, and the correct date was present in
the very utterance that triggered the supersede.

The only test that distinguishes the fix from the status quo is one where the
two axes DISAGREE. A test using `now` for both would pass either way.
"""

from contextlib import contextmanager

import pytest

from prax.services.memory import graph_store


class _Result:
    def single(self):
        return {"n": 1}

    def __iter__(self):
        return iter([])


class _Session:
    def __init__(self, calls):
        self._calls = calls

    def run(self, query, parameters=None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        self._calls.append((query, params))
        return _Result()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def calls(monkeypatch):
    recorded: list[tuple[str, dict]] = []

    @contextmanager
    def fake_session():
        yield _Session(recorded)

    monkeypatch.setattr(graph_store, "_session", fake_session)
    return recorded


def _valid_until(calls):
    for _q, params in calls:
        if "now" in params:
            return params["now"]
    raise AssertionError(f"no supersede parameter found in {calls}")


class TestValidTimeIsIndependentOfRecordTime:
    def test_explicit_valid_until_is_used(self, calls):
        """The load-bearing case: the fact stopped being true in March; we are
        told in August. The closing boundary must read March."""
        graph_store.supersede_relation(
            "u1", "tj", "lives_in", "paris", valid_until="2026-03-01T00:00:00+00:00")
        assert _valid_until(calls).startswith("2026-03-01")

    def test_the_two_axes_can_disagree(self, calls):
        """Stated as the property rather than a date: whatever 'now' is, an
        explicit valid_until must not be overwritten by it."""
        past = "2019-07-04T12:00:00+00:00"
        graph_store.supersede_relation("u1", "tj", "lives_in", "paris",
                                       valid_until=past)
        recorded = _valid_until(calls)
        assert recorded == past
        assert not recorded.startswith("202"[:3] + "6"), (
            "valid_until was replaced by the current time")


class TestBackCompat:
    def test_omitting_valid_until_still_means_now(self, calls):
        """An existing caller that does not know the effective date keeps prior
        behaviour. `now` is the honest default for 'we just found out'."""
        from datetime import UTC, datetime

        before = datetime.now(UTC).isoformat()
        graph_store.supersede_relation("u1", "tj", "lives_in", "paris")
        after = datetime.now(UTC).isoformat()
        assert before <= _valid_until(calls) <= after

    def test_none_is_treated_as_omitted(self, calls):
        from datetime import UTC, datetime

        before = datetime.now(UTC).isoformat()
        graph_store.supersede_relation("u1", "tj", "lives_in", "paris",
                                       valid_until=None)
        assert _valid_until(calls) >= before


def test_consolidation_forwards_the_effective_date():
    """Pin the wiring. The graph_store change is useless if the caller that has
    the date does not pass it — which was the actual defect."""
    import inspect

    from prax.services.memory import consolidation

    src = inspect.getsource(consolidation)
    assert "valid_until=supersedes.get(\"valid_until\")" in src, (
        "consolidation must forward the effective date it extracted")


def test_extraction_prompt_asks_for_the_effective_date():
    """And the model must be asked for it, or the field is always absent."""
    import inspect

    from prax.services.memory import consolidation

    src = inspect.getsource(consolidation)
    assert "STOPPED BEING TRUE" in src.upper(), (
        "the extraction prompt must ask when the OLD fact ceased to hold, not "
        "when the change was reported")
