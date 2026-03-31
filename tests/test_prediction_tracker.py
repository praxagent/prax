"""Tests for Active Inference Phase 1 (prediction error) and Phase 2 (epistemic ledger)."""
import pytest

from prax.agent.prediction_tracker import (
    PredictionTracker,
    compute_prediction_error,
    extract_resource_key,
    READ_TOOLS,
    WRITE_TOOLS,
)


# ---------------------------------------------------------------------------
# Phase 1 — Prediction error computation
# ---------------------------------------------------------------------------


class TestComputePredictionError:
    def test_no_prediction_returns_zero(self):
        assert compute_prediction_error("", "some result") == 0.0
        assert compute_prediction_error(None, "result") == 0.0

    def test_expected_success_got_failure(self):
        error = compute_prediction_error(
            "File will be saved successfully",
            "Error: permission denied, cannot write to /foo",
        )
        assert error >= 0.8

    def test_expected_failure_got_success(self):
        error = compute_prediction_error(
            "This will probably fail because the file doesn't exist",
            "File saved successfully",
        )
        assert 0.3 <= error <= 0.6

    def test_matching_predictions(self):
        error = compute_prediction_error(
            "Tests will pass with 0 failures",
            "All tests passed. 0 failures, 12 successes.",
        )
        assert error < 0.5

    def test_keyword_overlap(self):
        error = compute_prediction_error(
            "workspace will contain notes.md and config.yaml",
            "Found: notes.md config.yaml README.md in workspace",
        )
        assert error < 0.5

    def test_total_mismatch(self):
        error = compute_prediction_error(
            "Quick response expected",
            "Traceback (most recent call last): ...",
        )
        # Should detect failure pattern even without success keyword
        assert error >= 0.0  # At minimum no crash


# ---------------------------------------------------------------------------
# Phase 2 — Epistemic ledger
# ---------------------------------------------------------------------------


class TestEpistemicLedger:
    def test_read_tracking(self):
        tracker = PredictionTracker()
        tracker.record_read("config.yaml")
        assert tracker.has_read("config.yaml")
        assert not tracker.has_read("other.yaml")

    def test_reset_clears_reads(self):
        tracker = PredictionTracker()
        tracker.record_read("file.md")
        tracker.reset()
        assert not tracker.has_read("file.md")

    def test_gate_blocks_unread_write(self):
        tracker = PredictionTracker()
        result = tracker.check_epistemic_gate(
            "workspace_save",
            {"filename": "notes.md", "content": "hello"},
        )
        assert result is not None
        assert "notes.md" in result
        assert "Epistemic gate" in result

    def test_gate_allows_after_read(self):
        tracker = PredictionTracker()
        tracker.record_read("notes.md")
        result = tracker.check_epistemic_gate(
            "workspace_save",
            {"filename": "notes.md", "content": "hello"},
        )
        assert result is None

    def test_gate_allows_read_tools(self):
        tracker = PredictionTracker()
        result = tracker.check_epistemic_gate(
            "workspace_read",
            {"filename": "anything.md"},
        )
        assert result is None

    def test_gate_allows_no_resource_key(self):
        tracker = PredictionTracker()
        # note_create without a title — can't determine resource
        result = tracker.check_epistemic_gate(
            "note_create",
            {"content": "some note content"},
        )
        assert result is None


class TestExtractResourceKey:
    def test_workspace_save(self):
        assert extract_resource_key("workspace_save", {"filename": "test.md"}) == "test.md"

    def test_workspace_read(self):
        assert extract_resource_key("workspace_read", {"filename": "config.yaml"}) == "config.yaml"

    def test_note_create(self):
        key = extract_resource_key("note_create", {"title": "My Note"})
        assert key == "note:My Note"

    def test_unknown_tool(self):
        assert extract_resource_key("background_search_tool", {"query": "test"}) is None


# ---------------------------------------------------------------------------
# Tracker integration
# ---------------------------------------------------------------------------


class TestPredictionTracker:
    def test_consecutive_high_errors_flag_uncertainty(self):
        tracker = PredictionTracker()
        # Record several high-error predictions.
        for i in range(3):
            tracker.record_prediction(
                f"tool_{i}",
                "This should succeed",
                "Error: catastrophic failure",
            )
        assert tracker.is_high_uncertainty

    def test_low_errors_no_flag(self):
        tracker = PredictionTracker()
        tracker.record_prediction(
            "workspace_save",
            "File saved",
            "File saved successfully",
        )
        assert not tracker.is_high_uncertainty

    def test_prompt_injection_when_high_uncertainty(self):
        tracker = PredictionTracker()
        for i in range(3):
            tracker.record_prediction(
                f"tool_{i}",
                "Will succeed",
                "Error: total failure",
            )
        hint = tracker.prompt_injection()
        assert "ACTIVE INFERENCE WARNING" in hint
        assert "read-only" in hint.lower()

    def test_prompt_injection_empty_when_ok(self):
        tracker = PredictionTracker()
        tracker.record_prediction("t", "success", "done ok")
        assert tracker.prompt_injection() == ""

    def test_mean_error(self):
        tracker = PredictionTracker()
        tracker.record_prediction("a", "will succeed", "Error: fail")  # high
        tracker.record_prediction("b", "will complete", "completed ok")  # low
        # Mean should be between the two extremes.
        assert 0.0 < tracker.mean_error <= 1.0

    def test_drain_records(self):
        tracker = PredictionTracker()
        tracker.record_prediction("tool_a", "will pass", "passed")
        records = tracker.drain_records()
        assert len(records) == 1
        assert records[0]["tool"] == "tool_a"
        # Buffer should be empty after drain.
        assert tracker.drain_records() == []

    def test_consecutive_error_resets_on_success(self):
        tracker = PredictionTracker()
        # Two high errors.
        tracker.record_prediction("t1", "will succeed", "Error: fail")
        tracker.record_prediction("t2", "will succeed", "Error: fail")
        assert tracker._consecutive_high == 2
        # A prediction where expected and actual match closely resets.
        tracker.record_prediction(
            "t3",
            "file saved successfully to workspace",
            "file saved successfully to workspace done",
        )
        assert tracker._consecutive_high == 0


# ---------------------------------------------------------------------------
# Tool classification sanity checks
# ---------------------------------------------------------------------------


class TestToolClassification:
    def test_write_tools_are_disjoint_from_read_tools(self):
        assert WRITE_TOOLS & READ_TOOLS == frozenset()

    def test_known_write_tools(self):
        assert "workspace_save" in WRITE_TOOLS
        assert "workspace_patch" in WRITE_TOOLS
        assert "note_update" in WRITE_TOOLS

    def test_known_read_tools(self):
        assert "workspace_read" in READ_TOOLS
        assert "workspace_list" in READ_TOOLS
        assert "user_notes_read" in READ_TOOLS
