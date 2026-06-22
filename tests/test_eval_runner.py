"""Tests for the eval runner."""
from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import MagicMock

from prax.eval.runner import EvalResult, run_eval

# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------


class TestEvalResult:
    def test_auto_generates_id(self):
        r = EvalResult(case_id="c1", passed=True, score=0.9)
        assert len(r.id) == 12
        assert r.created_at

    def test_decomposed_axes_default_zero(self):
        r = EvalResult(case_id="c1")
        assert r.grounding == 0.0
        assert r.relevancy == 0.0
        assert r.correctness == 0.0


class TestJudgeDecomposedAxes:
    def test_judge_output_parses_axes(self, monkeypatch):
        from prax.eval import runner

        fake_llm = MagicMock()
        fake_llm.invoke.return_value = MagicMock(
            content='{"score": 0.9, "passed": true, "grounding": 0.8, '
                    '"relevancy": 1.0, "correctness": 0.7, "reasoning": "ok"}'
        )
        fake_llm.model_name = "gpt-x"
        monkeypatch.setattr("prax.agent.llm_factory.build_llm", lambda **kw: fake_llm)

        out = runner._judge_output("q", "old", "new", "fb", "cat")
        assert len(out) == 5
        score, passed, reasoning, model, axes = out
        assert score == 0.9 and passed is True
        assert axes == {"grounding": 0.8, "relevancy": 1.0, "correctness": 0.7}

    def test_run_eval_stores_axes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir", lambda: tmp_path,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_neo4j", lambda c: None,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_qdrant", lambda c: None,
        )
        from prax.services.memory.failure_journal import record_failure
        case = record_failure(user_id="u1", user_input="x", agent_output="y", feedback_comment="z")

        monkeypatch.setattr(
            "prax.eval.runner._judge_output",
            lambda **kw: (0.9, True, "fixed", "gpt-x",
                          {"grounding": 0.8, "relevancy": 1.0, "correctness": 0.7}),
        )
        monkeypatch.setattr("prax.eval.runner._replay_input", lambda uid, inp: "new answer")
        results_dir = tmp_path / "eval_results"
        results_dir.mkdir()
        monkeypatch.setattr("prax.eval.runner._results_file", lambda: results_dir / "r.jsonl")

        result = run_eval(case_id=case.id, replay=True)
        assert result.grounding == 0.8
        assert result.relevancy == 1.0
        assert result.correctness == 0.7


# ---------------------------------------------------------------------------
# run_eval
# ---------------------------------------------------------------------------


class TestRunEval:
    def test_missing_case_returns_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir",
            lambda: tmp_path,
        )
        result = run_eval(case_id="nonexistent", replay=False)
        assert not result.passed
        assert "not found" in result.reasoning

    def test_eval_with_mocked_judge(self, tmp_path, monkeypatch):
        # Set up a failure case
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_neo4j",
            lambda c: None,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_qdrant",
            lambda c: None,
        )

        from prax.services.memory.failure_journal import record_failure
        case = record_failure(
            user_id="u1",
            user_input="make slides",
            agent_output="I can't do that",
            feedback_comment="you have a tool for that",
        )

        # Mock the judge and replay
        monkeypatch.setattr(
            "prax.eval.runner._judge_output",
            lambda **kw: (0.85, True, "Fixed — uses txt2presentation now", "gpt-5.4-nano"),
        )
        monkeypatch.setattr(
            "prax.eval.runner._replay_input",
            lambda uid, inp: "Here's your presentation!",
        )

        # Mock results persistence
        results_dir = tmp_path / "eval_results"
        results_dir.mkdir()
        monkeypatch.setattr(
            "prax.eval.runner._results_file",
            lambda: results_dir / "results-test.jsonl",
        )

        result = run_eval(case_id=case.id, replay=True, judge_tier="low")
        assert result.passed
        assert result.score == 0.85
        assert "txt2presentation" in result.reasoning

    def test_eval_no_replay(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_neo4j",
            lambda c: None,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_qdrant",
            lambda c: None,
        )

        from prax.services.memory.failure_journal import record_failure
        case = record_failure(
            user_id="u1",
            user_input="search X",
            agent_output="wrong",
            feedback_comment="bad",
        )

        monkeypatch.setattr(
            "prax.eval.runner._judge_output",
            lambda **kw: (0.3, False, "Still failing", "gpt-5.4-nano"),
        )
        results_dir = tmp_path / "eval_results"
        results_dir.mkdir()
        monkeypatch.setattr(
            "prax.eval.runner._results_file",
            lambda: results_dir / "results-test.jsonl",
        )

        result = run_eval(case_id=case.id, replay=False)
        assert not result.passed
        assert result.score == 0.3


# ---------------------------------------------------------------------------
# load_results
# ---------------------------------------------------------------------------


class TestLoadResults:
    def test_load_from_file(self, tmp_path, monkeypatch):
        results_dir = tmp_path / ".prax" / "eval_results"
        results_dir.mkdir(parents=True)

        result = EvalResult(
            case_id="c1", passed=True, score=0.9,
            reasoning="Fixed", judge_model="test",
        )
        filepath = results_dir / "results-2026-04-02.jsonl"
        filepath.write_text(json.dumps(asdict(result)) + "\n")

        # Patch settings to use tmp_path as workspace
        mock_settings = MagicMock()
        mock_settings.workspace_dir = str(tmp_path)
        monkeypatch.setattr("prax.eval.runner.Path", type(tmp_path))

        # Direct test of file loading
        results = []
        for line in filepath.read_text().strip().splitlines():
            results.append(EvalResult(**json.loads(line)))
        assert len(results) == 1
        assert results[0].passed
        assert results[0].score == 0.9
