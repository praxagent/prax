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

from prax.services.memory import failure_journal, graph_store, knowledge_graph

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


# --------------------------------------------------------------------------- #
# The same guard, over the other Cypher-issuing modules
# --------------------------------------------------------------------------- #
#
# graph_store is where the defect was found, but it is not the only module that
# writes Cypher: knowledge_graph has 26 distinct `$params` and failure_journal
# 14, neither previously exercised. Guarding only the module that happened to
# break is how you get the same bug twice — the fix has to cover the class.
#
# A static scan confirmed `$lambda` was the only Python-KEYWORD collision in
# the codebase, so no second instance of that exact spelling exists. These
# tests cover the broader class: a statement referencing a parameter that is
# never supplied, whatever the reason.
#
# failure_journal imports `_session` from graph_store, so patching one reaches
# both.

KG_ENTRY_POINTS = [
    ("list_namespaces", lambda: knowledge_graph.list_namespaces("u1")),
    ("get_namespace_stats", lambda: knowledge_graph.get_namespace_stats("u1", "ns")),
    ("delete_namespace", lambda: knowledge_graph.delete_namespace("u1", "ns")),
    ("get_concept", lambda: knowledge_graph.get_concept("u1", "widgets")),
    ("add_concept", lambda: knowledge_graph.add_concept("u1", "ns", "widgets")),
    ("add_knowledge_relation",
     lambda: knowledge_graph.add_knowledge_relation("u1", "ns", "a", "rel", "b")),
    ("link_to_memory", lambda: knowledge_graph.link_to_memory("u1", "c", "e")),
    ("list_concepts", lambda: knowledge_graph.list_concepts("u1", "ns")),
    ("list_relations", lambda: knowledge_graph.list_relations("u1", "ns")),
]

FJ_ENTRY_POINTS = [
    ("get_failures", lambda: failure_journal.get_failures("u1")),
    ("get_failure_stats", lambda: failure_journal.get_failure_stats("u1")),
    ("resolve_failure", lambda: failure_journal.resolve_failure("c1", "fixed")),
]


def _assert_params_supplied(label, calls):
    for query, params in calls:
        referenced = set(PARAM_RE.findall(query))
        missing = referenced - set(params)
        assert not missing, (
            f"{label}: Cypher references {sorted(missing)} but they were not "
            f"passed (supplied: {sorted(params)}).\n{query.strip()}"
        )


@pytest.mark.parametrize("name,call", KG_ENTRY_POINTS,
                         ids=[e[0] for e in KG_ENTRY_POINTS])
def test_knowledge_graph_cypher_parameters_are_supplied(name, call, monkeypatch):
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_session():
        yield _RecordingSession(calls)

    monkeypatch.setattr(knowledge_graph, "_session", fake_session)
    call()
    _assert_params_supplied(name, calls)


@pytest.mark.parametrize("name,call", FJ_ENTRY_POINTS,
                         ids=[e[0] for e in FJ_ENTRY_POINTS])
def test_failure_journal_cypher_parameters_are_supplied(name, call, monkeypatch):
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_session():
        yield _RecordingSession(calls)

    # failure_journal imports _session FROM graph_store at call time.
    monkeypatch.setattr(graph_store, "_session", fake_session)
    call()
    _assert_params_supplied(name, calls)


def test_no_cypher_parameter_collides_with_a_python_keyword():
    """`$lambda` could not be passed as a kwarg and so was never supplied.

    Static sweep over every Cypher-issuing module, so a NEW statement using
    `$from`, `$class`, `$import` etc. is caught at the point it is written
    rather than after a night of silently-skipped writes.
    """
    import keyword
    from pathlib import Path

    # Known and CORRECT: decay_graph names its parameter `lambda` and passes it
    # in a dict, which works. The dynamic test above proves it is supplied. The
    # spelling is allowed here so this sweep flags only NEW occurrences, which
    # are overwhelmingly likely to be the kwarg mistake.
    ALLOWED = {"services/memory/graph_store.py: $lambda"}

    root = Path(__file__).resolve().parents[1] / "prax"
    offenders: list[str] = []
    for py in root.rglob("*.py"):
        text = py.read_text()
        if "MATCH" not in text and "MERGE" not in text:
            continue
        for name in set(PARAM_RE.findall(text)):
            if keyword.iskeyword(name):
                entry = f"{py.relative_to(root)}: ${name}"
                if entry not in ALLOWED:
                    offenders.append(entry)
    assert not offenders, (
        "Cypher parameters named after Python keywords CANNOT be passed as "
        "kwargs. Pass a params dict instead — and add a dynamic test that the "
        "statement actually receives it:\n  " + "\n  ".join(offenders)
    )
