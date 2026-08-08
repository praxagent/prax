"""Every `$param` a Cypher statement references must actually be supplied.

Grounded in a real defect: `decay_graph` wrote `exp(-$lambda * days)` but
passed it as the Python kwarg `lambda_=` (because `lambda` is a reserved
word).  Neo4j therefore raised ParameterMissing on the *first* statement, the
function's broad `except` swallowed it, and memory decay silently never ran —
`memories_forgotten` was 0 for every consolidation.  The only existing test
mocked `decay_graph` out entirely, so nothing exercised the query.

This guard is deliberately written against the CLASS, not that one query: it
drives each graph_store entry point through a fake session that records every
(query, params) pair, then asserts the parameter set referenced by the Cypher
is covered by the parameters passed.  Any future statement with a typo'd or
missing parameter fails here rather than in a swallowed exception at 3am.

Keyless and driver-free — no Neo4j required.
"""

import re
from contextlib import contextmanager

import pytest

from prax.services.memory import graph_store

# `$name` but not `$$` or a property-map key; Cypher params are word-chars.
PARAM_RE = re.compile(r"(?<![\w$])\$(\w+)")


class _FakeResult:
    """Neo4j results are consumed several different ways in this module."""

    def __init__(self):
        self._rows = []

    def single(self):
        return None

    def __iter__(self):
        return iter(self._rows)

    def data(self):
        return []


class _RecordingSession:
    def __init__(self, calls):
        self._calls = calls

    def run(self, query, parameters=None, **kwargs):
        # Mirror the driver's own signature: positional dict and/or kwargs.
        params = dict(parameters or {})
        params.update(kwargs)
        self._calls.append((query, params))
        return _FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# Each entry point with arguments plausible enough to reach its queries.
ENTRY_POINTS = [
    ("merge_entity", lambda: graph_store.merge_entity("u1", "Ada", "person")),
    ("add_relation", lambda: graph_store.add_relation("u1", "Ada", "Bob", "knows")),
    ("current_targets", lambda: graph_store.current_targets("u1", "Ada", "lives_in")),
    ("supersede_relation", lambda: graph_store.supersede_relation("u1", "Ada", "lives_in", "Paris")),
    ("get_entity", lambda: graph_store.get_entity("u1", "Ada")),
    ("get_neighbours", lambda: graph_store.get_neighbours("u1", "Ada")),
    ("search_entities", lambda: graph_store.search_entities("u1", "ada")),
    ("decay_graph", lambda: graph_store.decay_graph("u1")),
    ("merge_temporal_event", lambda: graph_store.merge_temporal_event("u1", "moved house")),
    ("add_causal_link", lambda: graph_store.add_causal_link("u1", "rain", "delay")),
    ("get_stats", lambda: graph_store.get_stats("u1")),
]


@pytest.mark.parametrize("name,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_every_cypher_parameter_is_supplied(name, call, monkeypatch):
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_session():
        yield _RecordingSession(calls)

    monkeypatch.setattr(graph_store, "_session", fake_session)
    call()

    assert calls, f"{name} issued no Cypher — the fake session was not reached"

    for query, params in calls:
        referenced = set(PARAM_RE.findall(query))
        missing = referenced - set(params)
        assert not missing, (
            f"{name}: Cypher references {sorted(missing)} but they were not "
            f"passed (supplied: {sorted(params)}). A parameter named after a "
            f"Python keyword must be passed in a dict, not as a kwarg.\n"
            f"{query.strip()}"
        )


def test_decay_graph_passes_the_lambda_parameter(monkeypatch):
    """The specific regression, pinned by name so it cannot silently return."""
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_session():
        yield _RecordingSession(calls)

    monkeypatch.setattr(graph_store, "_session", fake_session)
    graph_store.decay_graph("u1", halflife_days=7.0)

    decays = [(q, p) for q, p in calls if "$lambda" in q]
    assert len(decays) == 2, "expected entity-importance and relation-weight decay"
    for _query, params in decays:
        assert "lambda" in params
        assert params["lambda"] == pytest.approx(0.6931471805599453 / 7.0)


def test_decay_graph_reaches_the_prune_statements(monkeypatch):
    """The original bug aborted the whole function on statement 1, so pruning
    never happened either. Assert the later statements are actually issued."""
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_session():
        yield _RecordingSession(calls)

    monkeypatch.setattr(graph_store, "_session", fake_session)
    graph_store.decay_graph("u1")

    assert sum("DELETE" in q for q, _ in calls) == 2, (
        "entity and relation pruning must both run"
    )
