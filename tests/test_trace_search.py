"""Tests for trace_search_service + trace_tools.

Qdrant and the embedder are mocked — these tests exercise the
routing, graceful degradation, rendering, and payload shape.
Real semantic relevance is not in scope here.
"""
from __future__ import annotations

import importlib
import json
from unittest.mock import MagicMock

import pytest

from prax.services import trace_search_service


def _write_trace(graphs_dir, trace_id: str, trigger: str, status: str = "completed"):
    """Append one trace to a daily JSONL file."""
    import datetime as _dt
    fname = f"graphs-{_dt.date.today().isoformat()}.jsonl"
    (graphs_dir / fname).parent.mkdir(parents=True, exist_ok=True)
    graph = {
        "trace_id": trace_id,
        "status": status,
        "node_count": 2,
        "nodes": [
            {
                "span_id": "root",
                "name": "orchestrator",
                "parent_id": None,
                "status": status,
                "spoke_or_category": "orchestrator",
                "started_at": "2026-04-19T10:00:00",
                "finished_at": "2026-04-19T10:00:30",
                "tool_calls": 3,
                "summary": f"Handled task: {trigger}",
                "duration_s": 30.0,
            },
            {
                "span_id": "sub1",
                "name": "delegate_browser",
                "parent_id": "root",
                "status": "completed",
                "spoke_or_category": "browser",
                "started_at": "2026-04-19T10:00:05",
                "finished_at": "2026-04-19T10:00:25",
                "tool_calls": 2,
                "summary": "Browsed example.com and extracted main content.",
                "duration_s": 20.0,
            },
        ],
        "trigger": trigger,
        "session_id": "session-abc",
    }
    with (graphs_dir / fname).open("a", encoding="utf-8") as f:
        f.write(json.dumps(graph) + "\n")
    return graph


@pytest.fixture
def trace_dir(tmp_path, monkeypatch):
    """Create a .prax/graphs dir with a couple of sample traces and point settings at it."""
    ws = tmp_path / "workspaces"
    ws.mkdir()
    graphs = ws / ".prax" / "graphs"
    graphs.mkdir(parents=True)
    monkeypatch.setattr(trace_search_service.settings, "workspace_dir", str(ws))
    _write_trace(graphs, "trace-plan-tokyo", "plan a 3-day trip to Tokyo with budget $2000")
    _write_trace(graphs, "trace-arxiv-rag", "find arxiv papers on RAG and summarise them")
    _write_trace(graphs, "trace-broken", "pay bills", status="failed")
    # Reset the lazy-index cache between tests.
    trace_search_service._indexed_cache.clear()
    return graphs


class TestExtraction:
    def test_doc_builder_includes_trigger_and_summaries(self):
        graph = {
            "trace_id": "t1",
            "trigger": "plan a trip",
            "nodes": [
                {"name": "orch", "summary": "root summary"},
                {"name": "sub1", "summary": "did browser work"},
            ],
        }
        doc = trace_search_service._extract_search_document(graph)
        assert "plan a trip" in doc
        assert "browser work" in doc

    def test_doc_builder_caps_length(self):
        long_trigger = "x" * 3000
        graph = {"trace_id": "t1", "trigger": long_trigger, "nodes": []}
        doc = trace_search_service._extract_search_document(graph)
        assert len(doc) <= trace_search_service.MAX_DOC_CHARS + 3

    def test_summary_payload_has_expected_keys(self):
        graph = {
            "trace_id": "t1",
            "trigger": "do thing",
            "status": "completed",
            "node_count": 2,
            "nodes": [{"tool_calls": 3, "started_at": "2026-01-01T00:00:00"}],
            "session_id": "s1",
        }
        p = trace_search_service._extract_summary_payload(graph)
        assert p["trace_id"] == "t1"
        assert p["tool_calls"] == 3
        assert p["status"] == "completed"
        assert p["session_id"] == "s1"


class TestQdrantUnavailable:
    def test_search_returns_not_available_when_qdrant_down(self, trace_dir, monkeypatch):
        """When Qdrant can't be reached, tools return a graceful message."""
        # Patch vector_store._get_client to raise.
        from prax.services.memory import vector_store

        def raise_err():
            raise ConnectionError("Qdrant unreachable")

        monkeypatch.setattr(vector_store, "_get_client", raise_err)
        result = trace_search_service.search_traces("plan a trip")
        assert result["status"] == "not_available"
        assert "not available" in result["message"].lower()

    def test_is_available_false_when_qdrant_down(self, monkeypatch):
        from prax.services.memory import vector_store

        def raise_err():
            raise ConnectionError("x")

        monkeypatch.setattr(vector_store, "_get_client", raise_err)
        assert trace_search_service.is_available() is False


class TestQdrantPath:
    @pytest.fixture
    def fake_qdrant(self, monkeypatch):
        """Mock the Qdrant client + embedder to simulate a healthy full-mode deployment."""
        from prax.services.memory import embedder, vector_store
        client = MagicMock()
        client.get_collections.return_value = MagicMock(collections=[])
        client.scroll.return_value = ([], None)
        client.upsert.return_value = None

        # Query returns the first indexed trace as a "match".
        def fake_query(**kwargs):
            pts = MagicMock()
            pts.points = [
                MagicMock(
                    score=0.91,
                    payload={
                        "trace_id": "trace-plan-tokyo",
                        "trigger": "plan a 3-day trip to Tokyo with budget $2000",
                        "status": "completed",
                        "tool_calls": 3,
                        "node_count": 2,
                        "started_at": "2026-04-19T10:00:00",
                    },
                ),
            ]
            return pts

        client.query_points.side_effect = fake_query
        monkeypatch.setattr(vector_store, "_get_client", lambda: client)
        monkeypatch.setattr(embedder, "embed_texts", lambda docs: [[0.1] * 1536 for _ in docs])
        monkeypatch.setattr(embedder, "embed_text", lambda text: [0.1] * 1536)
        return client

    def test_search_indexes_and_queries(self, trace_dir, fake_qdrant):
        result = trace_search_service.search_traces("Tokyo")
        assert result["status"] == "ok"
        assert len(result["matches"]) == 1
        assert result["matches"][0]["trace_id"] == "trace-plan-tokyo"
        assert result["matches"][0]["score"] == pytest.approx(0.91)
        # Upsert was called with 3 points (one per trace).
        assert fake_qdrant.upsert.called

    def test_index_is_idempotent_within_process(self, trace_dir, fake_qdrant):
        trace_search_service.search_traces("Tokyo")
        fake_qdrant.upsert.reset_mock()
        # Second call should not re-upsert the same traces.
        trace_search_service.search_traces("arxiv")
        assert fake_qdrant.upsert.call_count == 0

    def test_empty_query_rejected(self, trace_dir, fake_qdrant):
        result = trace_search_service.search_traces("")
        assert result["status"] == "error"


class TestTraceDetail:
    def test_detail_from_disk_when_not_in_memory(self, trace_dir, monkeypatch):
        # Force in-memory lookup to miss.
        import prax.agent.trace as trace_module
        monkeypatch.setattr(trace_module, "_active_graphs", {})
        monkeypatch.setattr(trace_module, "_load_persisted_graphs", lambda: None)
        result = trace_search_service.get_trace_detail("trace-plan-tokyo")
        assert result["status"] == "ok"
        assert result["trace"]["trace_id"] == "trace-plan-tokyo"
        assert "Tokyo" in result["trace"]["trigger"]

    def test_detail_returns_not_found_for_missing_id(self, trace_dir):
        result = trace_search_service.get_trace_detail("nonexistent-trace-id")
        assert result["status"] == "not_found"

    def test_detail_empty_id_rejected(self, trace_dir):
        result = trace_search_service.get_trace_detail("")
        assert result["status"] == "error"


class TestToolRendering:
    def test_trace_search_tool_renders_not_available(self, trace_dir, monkeypatch):
        from prax.services.memory import vector_store
        monkeypatch.setattr(
            vector_store, "_get_client",
            lambda: (_ for _ in ()).throw(ConnectionError("no qdrant")),
        )
        module = importlib.reload(importlib.import_module("prax.agent.trace_tools"))
        result = module.trace_search.invoke({"query": "anything"})
        assert "not available" in result.lower() or "⚠️" in result

    def test_trace_search_tool_renders_matches(self, trace_dir, monkeypatch):
        # Inject a fake service-level search_traces to skip Qdrant entirely.
        monkeypatch.setattr(
            trace_search_service, "search_traces",
            lambda query, top_k=5: {
                "status": "ok",
                "matches": [{
                    "trace_id": "trace-plan-tokyo",
                    "trigger": "plan a 3-day trip to Tokyo",
                    "status": "completed",
                    "tool_calls": 3,
                    "started_at": "2026-04-19T10:00:00",
                    "score": 0.88,
                }],
            },
        )
        module = importlib.reload(importlib.import_module("prax.agent.trace_tools"))
        result = module.trace_search.invoke({"query": "japan trip"})
        assert "trace-plan-tokyo" in result
        assert "0.88" in result
        assert "Tokyo" in result

    def test_trace_detail_tool_renders_trace(self, trace_dir):
        module = importlib.reload(importlib.import_module("prax.agent.trace_tools"))
        result = module.trace_detail.invoke({"trace_id": "trace-plan-tokyo"})
        assert "trace-plan-tokyo" in result
        assert "completed" in result
        assert "Tokyo" in result

    def test_trace_detail_tool_handles_missing(self, trace_dir):
        module = importlib.reload(importlib.import_module("prax.agent.trace_tools"))
        result = module.trace_detail.invoke({"trace_id": "does-not-exist"})
        assert "not found" in result.lower() or "⚠️" in result


class TestToolRegistration:
    def test_trace_tools_in_orchestrator_toolset(self):
        from prax.agent.workspace_tools import build_workspace_tools
        tools = build_workspace_tools()
        names = {t.name for t in tools}
        assert "trace_search" in names
        assert "trace_detail" in names
