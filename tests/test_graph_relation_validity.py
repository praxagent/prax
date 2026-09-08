"""A superseded (closed) edge must stay closed; re-learning the fact opens a new one.

`add_relation` used `MERGE (s)-[r:RELATES_TO {type: $rtype}]->(t)`, which
matches on type alone.  Once `supersede_relation` had set `valid_until` on
A -lives_in-> Paris, learning "lives in Paris" again MERGE-matched that closed
edge and only bumped its weight.  After Paris -> Berlin -> Paris the graph held
two closed edges and NO current one: `current_targets` said nowhere,
`get_entity` (which hides superseded edges by default) showed no residence,
and the consistency pass had nothing to compare against.

The fix matches only an OPEN edge (`valid_until IS NULL`) and otherwise
CREATEs a new one, in a single statement.

Two layers of test:

* Keyless, with the recording fake session from
  tests/test_graph_store_cypher_params.py: drives the A -> B -> A sequence
  and pins the SHAPE of every statement it issues.  The fake does not
  evaluate Cypher, so this cannot prove graph semantics — it proves the
  defective statement is gone and the intended one is what is sent.
* Opt-in (`PRAX_LIVE_NEO4J=1`) against a real Neo4j: runs the same sequence
  and asserts the resulting graph.  Skipped in CI; run locally with the
  docker stack up.  Uses a throwaway user_id and removes everything it made.
"""
from __future__ import annotations

import os
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from prax.services.memory import graph_store

PARAM_RE = re.compile(r"(?<![\w$])\$(\w+)")


class _FakeResult:
    def single(self):
        return None

    def __iter__(self):
        return iter(())


class _RecordingSession:
    def __init__(self, calls):
        self._calls = calls

    def run(self, query, parameters=None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        self._calls.append((query, params))
        return _FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _a_b_a(uid: str) -> None:
    """lives_in Paris, superseded; lives_in Berlin, superseded; lives_in Paris again."""
    graph_store.add_relation(uid, "ada", "lives_in", "paris", evidence="moved 2020")
    graph_store.supersede_relation(uid, "ada", "lives_in", "paris")
    graph_store.add_relation(uid, "ada", "lives_in", "berlin", evidence="moved 2023")
    graph_store.supersede_relation(uid, "ada", "lives_in", "berlin")
    graph_store.add_relation(uid, "ada", "lives_in", "paris", evidence="moved back 2026")


# --------------------------------------------------------------------------- #
# Keyless: statement shape over the A -> B -> A sequence
# --------------------------------------------------------------------------- #

def test_add_relation_never_merges_on_type_alone(monkeypatch):
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_session():
        yield _RecordingSession(calls)

    monkeypatch.setattr(graph_store, "_session", fake_session)
    _a_b_a("u1")

    adds = [(q, p) for q, p in calls if "CREATE (s)-[:RELATES_TO" in q or "MERGE (s)-[r:RELATES_TO" in q]
    assert len(adds) == 3, "three add_relation statements expected"

    for query, params in adds:
        # The defect, verbatim: MERGE on {type} matches a closed edge.
        assert "MERGE (s)-[r:RELATES_TO {type: $rtype}]->(t)" not in query
        assert "MERGE" not in query, "a MERGE on RELATES_TO cannot exclude closed edges"
        # Only an OPEN edge may be strengthened...
        assert re.search(r"OPTIONAL MATCH \(s\)-\[open:RELATES_TO \{type: \$rtype\}\]->\(t\)\s+WHERE open\.valid_until IS NULL", query)
        # ...and when there is none, a NEW edge is created, itself open.
        assert "CREATE (s)-[:RELATES_TO {" in query
        assert "valid_until: null" in query
        # Same guard as tests/test_graph_store_cypher_params.py: no dangling $param.
        missing = set(PARAM_RE.findall(query)) - set(params)
        assert not missing, f"Cypher references {sorted(missing)} but they were not passed"

    closes = [q for q, _ in calls if "SET r.valid_until" in q]
    assert len(closes) == 2
    for q in closes:
        assert "WHERE r.valid_until IS NULL" in q, "supersede must close only the current edge"


def test_third_add_carries_fresh_validity_not_the_closed_edges(monkeypatch):
    """The third write must be a statement that CAN create: it carries its own
    valid_from and evidence, not a weight bump keyed to the closed edge."""
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_session():
        yield _RecordingSession(calls)

    monkeypatch.setattr(graph_store, "_session", fake_session)
    _a_b_a("u1")

    third = [p for q, p in calls if "CREATE (s)-[:RELATES_TO" in q][-1]
    assert third["tgt"] == "paris" and third["rtype"] == "lives_in"
    assert third["evidence"] == "moved back 2026"
    assert third["vf"], "a new edge needs its own valid_from"


# --------------------------------------------------------------------------- #
# Opt-in: the same sequence against a real Neo4j
# --------------------------------------------------------------------------- #

live = pytest.mark.skipif(
    os.environ.get("PRAX_LIVE_NEO4J") != "1",
    reason="set PRAX_LIVE_NEO4J=1 with the docker stack up to run against a real Neo4j",
)


@pytest.fixture
def live_uid():
    uid = f"test-relation-validity-{uuid.uuid4().hex[:8]}"
    try:
        yield uid
    finally:
        try:
            with graph_store._session() as s:
                s.run("MATCH (e:Entity {user_id: $uid}) DETACH DELETE e", uid=uid)
        finally:
            graph_store.close()


@live
def test_live_a_b_a_leaves_exactly_one_current_edge(live_uid):
    uid = live_uid
    for name, typ in (("ada", "person"), ("paris", "place"), ("berlin", "place")):
        graph_store.merge_entity(uid, name, typ)

    _a_b_a(uid)

    assert graph_store.current_targets(uid, "ada", "lives_in") == ["paris"]
    with graph_store._session() as s:
        rows = s.run(
            """
            MATCH (:Entity {user_id: $uid, name: 'ada'})-[r:RELATES_TO {type: 'lives_in'}]->(t)
            RETURN t.name AS target, r.valid_until IS NULL AS current, r.weight AS weight
            ORDER BY r.first_seen
            """,
            uid=uid,
        ).data()
    assert [(r["target"], r["current"]) for r in rows] == [
        ("paris", False), ("berlin", False), ("paris", True),
    ], rows
    assert rows[-1]["weight"] == 1.0, "the new edge starts fresh, it is not the closed one re-weighted"

    # Strengthening still works on the OPEN edge.
    graph_store.add_relation(uid, "ada", "lives_in", "paris", weight=0.5)
    with graph_store._session() as s:
        rows = s.run(
            """
            MATCH (:Entity {user_id: $uid, name: 'ada'})-[r:RELATES_TO {type: 'lives_in'}]->(t)
            RETURN count(r) AS n, sum(CASE WHEN r.valid_until IS NULL THEN r.weight ELSE 0 END) AS open_w
            """,
            uid=uid,
        ).single()
    assert rows["n"] == 3 and rows["open_w"] == 1.5


@live
def test_live_graph_decay_prunes_by_total_days(live_uid):
    """40 days at a 14-day half-life: 0.2 x 2^(-40/14) = 0.028 < 0.05 -> pruned.
    With the old days-COMPONENT arithmetic (1 month 9 days -> 9 or 10 days),
    0.2 x 2^(-10/14) = 0.12 -> kept."""
    uid = live_uid
    graph_store.merge_entity(uid, "stale", "concept", importance=0.2)
    graph_store.merge_entity(uid, "fresh", "concept", importance=0.2)
    with graph_store._session() as s:
        s.run(
            "MATCH (e:Entity {user_id: $uid, name: 'stale'}) SET e.last_seen = $ts",
            uid=uid, ts=(datetime.now(UTC) - timedelta(days=40)).isoformat(),
        )

    pruned = graph_store.decay_graph(uid, halflife_days=14.0)
    assert pruned == 1
    with graph_store._session() as s:
        names = sorted(r["name"] for r in s.run(
            "MATCH (e:Entity {user_id: $uid}) RETURN e.name AS name", uid=uid))
    assert names == ["fresh"]
    # Idempotent: nothing more to prune for the same moment.
    assert graph_store.decay_graph(uid, halflife_days=14.0) == 0
