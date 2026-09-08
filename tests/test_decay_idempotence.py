"""Memory decay must be idempotent and must run on the cadence the docs state.

What was wrong (2026-09 review):

* `vector_store.decay_memories` multiplied the STORED importance by
  exp(-λ × days since last_accessed) and wrote it back, every time it ran.
  Nothing advanced a "last decayed" mark, so each pass re-applied the full
  factor to an already-decayed value — the exponent accumulated.
* It ran inside every consolidation, i.e. every 5 turns.  `last_decay_run`
  was written to the state file and never read.
* `graph_store.decay_graph` had the same write-back shape, and measured
  elapsed time as `duration.between(...).days` — the DAYS COMPONENT of a
  (months, days, seconds) duration, so a 45-day gap counted as 15.

Net effect: a "7-day half-life" pruned an active user's memories in 4-8 days.

Now: the decay pass computes effective importance from `last_accessed` and
prunes below threshold without writing anything back (so two passes for the
same `now` are one pass), the graph uses total days from epoch arithmetic,
and consolidation runs the pass at most once per `DECAY_MIN_INTERVAL`.
"""
from __future__ import annotations

import inspect
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from prax.services.memory import consolidation, graph_store, vector_store

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class _FakeQdrant:
    """Enough of the Qdrant client for decay_memories: scroll / set_payload / delete.

    set_payload MUTATES the stored payload, so a second pass sees whatever the
    first pass wrote back — exactly how the compounding happened for real.
    """

    def __init__(self, points: dict[str, dict]):
        self.points = {pid: dict(p) for pid, p in points.items()}
        self.set_payload_calls: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    def scroll(self, collection_name, scroll_filter, limit, offset, with_payload):
        return [SimpleNamespace(id=pid, payload=dict(p)) for pid, p in self.points.items()], None

    def set_payload(self, collection_name, payload, points):
        for pid in points:
            self.set_payload_calls.append((pid, dict(payload)))
            self.points[pid].update(payload)

    def delete(self, collection_name, points_selector):
        for pid in points_selector:
            self.deleted.append(pid)
            self.points.pop(pid, None)


def _mem(importance: float, days_ago: float) -> dict:
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    return {"user_id": "u1", "content": "m", "importance": importance,
            "created_at": ts, "last_accessed": ts, "source": "conversation"}


@contextmanager
def _qdrant(points: dict[str, dict]):
    fake = _FakeQdrant(points)
    with patch.object(vector_store, "_get_client", return_value=fake):
        yield fake


# --------------------------------------------------------------------------- #
# 1. Vector store: same "now" twice -> identical importances
# --------------------------------------------------------------------------- #

def test_two_passes_with_the_same_now_leave_importances_identical():
    points = {"a": _mem(0.8, 3), "b": _mem(0.5, 3), "c": _mem(0.3, 3)}
    original = {pid: p["importance"] for pid, p in points.items()}

    with _qdrant(points) as fake:
        first = vector_store.decay_memories("u1", halflife_days=7.0, now=NOW)
        after_first = {pid: p["importance"] for pid, p in fake.points.items()}
        second = vector_store.decay_memories("u1", halflife_days=7.0, now=NOW)
        after_second = {pid: p["importance"] for pid, p in fake.points.items()}

    assert first == second == 0
    assert after_first == after_second, "a second pass for the same moment changed importances"
    assert after_first == original, "the decay pass must not rewrite stored importance"
    assert fake.set_payload_calls == [], (
        f"decay wrote importance back: {fake.set_payload_calls[:2]} — that is the compounding bug"
    )


def test_prune_decision_depends_only_on_age_since_last_access():
    """0.5 at 40 days: 0.5 x 2^(-40/7) = 0.0096 < 0.02 -> pruned.
    0.5 at 6 days: 0.5 x 2^(-6/7) = 0.28 -> kept.  Repeating changes nothing."""
    points = {"old": _mem(0.5, 40), "fresh": _mem(0.5, 6)}
    with _qdrant(points) as fake:
        assert vector_store.decay_memories("u1", halflife_days=7.0, now=NOW) == 1
        assert fake.deleted == ["old"]
        assert "fresh" in fake.points and fake.points["fresh"]["importance"] == 0.5
        assert vector_store.decay_memories("u1", halflife_days=7.0, now=NOW) == 0
        assert vector_store.decay_memories("u1", halflife_days=7.0, now=NOW) == 0


def test_recall_restores_a_memory_fully():
    """Forgetting-curve semantics: reinforce resets last_accessed, and with no
    write-back the memory is back to its stored importance — not a permanently
    lowered one."""
    m = _mem(0.5, 30)
    m["last_accessed"] = NOW.isoformat()          # recalled just now
    with _qdrant({"m": m}) as fake:
        assert vector_store.decay_memories("u1", halflife_days=7.0, now=NOW) == 0
        assert fake.points["m"]["importance"] == 0.5


@pytest.mark.parametrize("days,expected", [(0, 1.0), (7, 0.5), (14, 0.25), (21, 0.125)])
def test_effective_importance_follows_the_documented_curve(days, expected):
    last = NOW - timedelta(days=days)
    assert vector_store.effective_importance(1.0, last, NOW, 7.0) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# 2. Graph store: total days, no write-back
# --------------------------------------------------------------------------- #

class _RecordingSession:
    def __init__(self, calls):
        self._calls = calls

    def run(self, query, parameters=None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        self._calls.append((query, params))
        return SimpleNamespace(single=lambda: None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_graph_decay_measures_total_days_not_the_days_component(monkeypatch):
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_session():
        yield _RecordingSession(calls)

    monkeypatch.setattr(graph_store, "_session", fake_session)
    graph_store.decay_graph("u1", halflife_days=14.0, now=NOW)

    decays = [q for q, _ in calls if "$lambda" in q]
    assert len(decays) == 2, "entity and relation decay statements"
    for q in decays:
        assert "duration.between" not in q, (
            "duration.between(...).days is the DAYS COMPONENT (45 days -> 15); use total days"
        )
        assert "epochSeconds" in q and "/ 86400" in q, "total days via epoch arithmetic"
        # Idempotence for the graph: effective weight is evaluated in the prune
        # predicate; the stored value is never SET back.
        assert "SET " not in q, "graph decay must not write decayed values back"
        assert "DELETE" in q
    for _, params in [(q, p) for q, p in calls if "$lambda" in q]:
        assert params["now"] == NOW.isoformat()


# --------------------------------------------------------------------------- #
# 3. Consolidation runs the pass at most once per DECAY_MIN_INTERVAL
# --------------------------------------------------------------------------- #

@pytest.fixture
def workspace(tmp_path):
    def _root(user_id: str) -> str:
        path = os.path.join(str(tmp_path), user_id)
        os.makedirs(path, exist_ok=True)
        return path

    with patch("prax.services.workspace_service.workspace_root", side_effect=_root):
        yield tmp_path


def _prepare(workspace, uid: str, last_decay_run: str | None) -> None:
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("[USER] something new to consolidate\n")
    state = {"last_consolidated_line": 0, "last_daily_summary": "", "trace_head": None}
    if last_decay_run is not None:
        state["last_decay_run"] = last_decay_run
    mem = workspace / uid / "memory"
    mem.mkdir(exist_ok=True)
    (mem / "consolidation_state.json").write_text(json.dumps(state))


@contextmanager
def _consolidation_without_llm():
    empty = {"entities": [], "relations": [], "facts": [], "temporal_events": [], "causal_links": []}
    with patch.object(consolidation, "_extract_entities_relations", return_value=empty), \
         patch.object(consolidation, "_build_daily_summary", return_value=""), \
         patch("prax.services.memory.vector_store.decay_memories", return_value=0) as vdecay, \
         patch("prax.services.memory.graph_store.decay_graph", return_value=0) as gdecay:
        yield vdecay, gdecay


def _saved_last_decay_run(workspace, uid: str) -> str:
    return json.loads((workspace / uid / "memory" / "consolidation_state.json").read_text())[
        "last_decay_run"
    ]


def test_decay_is_skipped_when_it_ran_less_than_24h_ago(workspace):
    """Old code ran decay on every consolidation and never read last_decay_run."""
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    _prepare(workspace, "u1", recent)

    with _consolidation_without_llm() as (vdecay, gdecay):
        consolidation.consolidate_user("u1")

    vdecay.assert_not_called()
    gdecay.assert_not_called()
    assert _saved_last_decay_run(workspace, "u1") == recent, "the mark must not move when the pass is skipped"


def test_decay_runs_when_the_last_pass_is_older_than_24h(workspace):
    stale = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    _prepare(workspace, "u1", stale)

    with _consolidation_without_llm() as (vdecay, gdecay):
        consolidation.consolidate_user("u1")

    vdecay.assert_called_once()
    gdecay.assert_called_once()
    assert _saved_last_decay_run(workspace, "u1") > stale


@pytest.mark.parametrize("missing", [None, "", "not-a-timestamp"])
def test_decay_runs_when_the_mark_is_absent_or_unreadable(workspace, missing):
    _prepare(workspace, "u2", missing)
    with _consolidation_without_llm() as (vdecay, gdecay):
        consolidation.consolidate_user("u2")
    vdecay.assert_called_once()
    gdecay.assert_called_once()


def test_decay_due_boundary():
    now = datetime(2026, 9, 8, tzinfo=UTC)
    assert consolidation._decay_due({"last_decay_run": (now - timedelta(hours=24)).isoformat()}, now)
    assert not consolidation._decay_due(
        {"last_decay_run": (now - timedelta(hours=23, minutes=59)).isoformat()}, now
    )
    # Naive timestamps (older state files) are read as UTC rather than crashing.
    assert consolidation._decay_due({"last_decay_run": "2026-01-01T00:00:00"}, now)


# --------------------------------------------------------------------------- #
# 4. The interaction-decay path stays deleted
# --------------------------------------------------------------------------- #

def test_interaction_decay_path_is_gone():
    """It never had a caller; `interaction_epoch` was hard-coded to 0 on every
    write; and wiring it up as written would have pruned everything.  Deleted,
    not armed — and this keeps it from quietly returning."""
    from prax.services.memory_service import MemoryService

    assert not hasattr(MemoryService, "track_interaction")
    for name in ("increment_interaction_epoch", "get_interaction_epoch", "_epoch_point_id"):
        assert not hasattr(vector_store, name), name
    assert "interaction_epoch" not in inspect.getsource(vector_store)
    assert "halflife_interactions" not in inspect.signature(vector_store.decay_memories).parameters
    assert "interaction_epoch" not in inspect.signature(vector_store.reinforce_memory).parameters
