"""Tests for reference-free live-traffic evaluation (prax.eval.live_eval)."""
from __future__ import annotations

import json

from prax.eval import live_eval


def test_extract_case_skips_running():
    assert live_eval._extract_case({"status": "running", "trigger": "x", "nodes": []}) is None


def test_extract_case_skips_no_trigger():
    assert live_eval._extract_case({"status": "completed", "nodes": [{"summary": "did a thing"}]}) is None


def test_extract_case_builds_summary():
    graph = {
        "status": "completed",
        "trigger": "what's the weather",
        "nodes": [
            {"name": "orchestrator", "summary": "fetched weather via tool", "spoke_or_category": "weather"},
            {"name": "child", "parent_id": "root", "summary": "ignored child"},
        ],
    }
    case = live_eval._extract_case(graph)
    assert case is not None
    trigger, summary = case
    assert trigger == "what's the weather"
    assert "fetched weather via tool" in summary


def test_run_live_traffic_eval_aggregates(tmp_path, monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))

    graphs_dir = tmp_path / ".prax" / "graphs"
    graphs_dir.mkdir(parents=True)
    rows = [
        {"status": "completed", "trigger": "q1", "nodes": [{"name": "o", "summary": "s1"}]},
        {"status": "completed", "trigger": "q2", "nodes": [{"name": "o", "summary": "s2"}]},
        {"status": "running", "trigger": "q3", "nodes": [{"name": "o", "summary": "s3"}]},
    ]
    (graphs_dir / "graphs-2026-06-16.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    # Deterministic judge so we can assert the aggregate.
    monkeypatch.setattr(
        live_eval, "_judge_trace",
        lambda trigger, summary, tier="low": {"grounding": 0.8, "relevancy": 0.6, "correctness": 1.0},
    )

    report = live_eval.run_live_traffic_eval(sample_size=10)
    assert report["scored"] == 2  # the 'running' trace is skipped
    assert report["axes"] == {"grounding": 0.8, "relevancy": 0.6, "correctness": 1.0}
    assert report["overall"] == 0.8


def test_run_live_traffic_eval_no_traces(tmp_path, monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    report = live_eval.run_live_traffic_eval(sample_size=10)
    assert report == {"sampled": 0, "scored": 0, "axes": {}, "overall": 0.0}
