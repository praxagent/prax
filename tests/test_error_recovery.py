"""Tests for prax.agent.error_recovery — multi-perspective failure analysis."""
from __future__ import annotations

from prax.agent.error_recovery import (
    ErrorAnalysis,
    PerspectiveAnalysis,
    analyze_tool_failure,
    build_recovery_context,
)


class TestAnalyzeToolFailure:
    def test_not_found_error(self):
        analysis = analyze_tool_failure(
            "fetch_url_content",
            "HTTP 404 Not Found: https://example.com/missing",
        )
        assert len(analysis.perspectives) >= 1
        # Should identify logical consistency (resource not found)
        logical = [p for p in analysis.perspectives if p.perspective == "logical_consistency"]
        assert len(logical) >= 1
        assert logical[0].confidence >= 0.5

    def test_rate_limit_error(self):
        analysis = analyze_tool_failure(
            "background_search_tool",
            "HTTP 429 Too Many Requests",
        )
        logical = [p for p in analysis.perspectives if p.perspective == "logical_consistency"]
        assert len(logical) >= 1
        assert logical[0].confidence >= 0.8
        assert "rate limit" in logical[0].diagnosis.lower()

    def test_missing_args_error(self):
        analysis = analyze_tool_failure(
            "workspace_save",
            "Required parameter 'content' is missing",
            tool_args="{'filename': 'test.md'}",
        )
        completeness = [p for p in analysis.perspectives if p.perspective == "information_completeness"]
        assert len(completeness) >= 1

    def test_empty_args_detected(self):
        analysis = analyze_tool_failure(
            "workspace_save",
            "Some error",
            tool_args="",
        )
        completeness = [p for p in analysis.perspectives if p.perspective == "information_completeness"]
        assert len(completeness) >= 1
        assert "empty" in completeness[0].diagnosis.lower()

    def test_json_parse_error(self):
        analysis = analyze_tool_failure(
            "fetch_url_content",
            "JSONDecodeError: Expecting value: line 1 column 1",
        )
        assumptions = [p for p in analysis.perspectives if p.perspective == "assumptions"]
        assert len(assumptions) >= 1
        assert "json" in assumptions[0].diagnosis.lower()

    def test_timeout_error(self):
        analysis = analyze_tool_failure(
            "delegate_sandbox",
            "Operation timed out after 30 seconds",
        )
        assert any(
            "timed out" in p.diagnosis.lower() or "timeout" in p.suggestion.lower()
            for p in analysis.perspectives
        )

    def test_alternative_approach_for_known_tools(self):
        analysis = analyze_tool_failure(
            "fetch_url_content",
            "Connection refused",
        )
        alternatives = [p for p in analysis.perspectives if p.perspective == "alternative_approach"]
        assert len(alternatives) >= 1
        assert "delegate_browser" in alternatives[0].suggestion

    def test_unknown_error_gets_generic(self):
        analysis = analyze_tool_failure(
            "some_custom_tool",
            "Something completely unexpected happened",
        )
        assert len(analysis.perspectives) >= 1  # at least a fallback

    def test_permission_error(self):
        analysis = analyze_tool_failure(
            "workspace_save",
            "Permission denied: /root/secret",
        )
        logical = [p for p in analysis.perspectives if p.perspective == "logical_consistency"]
        assert len(logical) >= 1
        assert "permission" in logical[0].diagnosis.lower()


class TestErrorAnalysis:
    def test_best_suggestion(self):
        analysis = ErrorAnalysis(
            tool_name="test",
            error_message="error",
            original_args="",
            perspectives=[
                PerspectiveAnalysis("a", "diag_a", "fix_a", 0.3),
                PerspectiveAnalysis("b", "diag_b", "fix_b", 0.9),
                PerspectiveAnalysis("c", "diag_c", "fix_c", 0.5),
            ],
        )
        assert analysis.best_suggestion == "fix_b"

    def test_recovery_prompt(self):
        analysis = ErrorAnalysis(
            tool_name="test_tool",
            error_message="Something broke",
            original_args="",
            perspectives=[
                PerspectiveAnalysis("logical", "Wrong args", "Fix args", 0.8),
            ],
        )
        prompt = analysis.recovery_prompt
        assert "test_tool" in prompt
        assert "Something broke" in prompt
        assert "Fix args" in prompt
        assert "logical" in prompt

    def test_to_dict(self):
        analysis = ErrorAnalysis(
            tool_name="test",
            error_message="error",
            original_args="args",
            perspectives=[
                PerspectiveAnalysis("a", "diag", "fix", 0.5),
            ],
        )
        d = analysis.to_dict()
        assert d["tool_name"] == "test"
        assert len(d["perspectives"]) == 1
        assert d["perspectives"][0]["confidence"] == 0.5


class TestBuildRecoveryContext:
    def test_includes_attempt_number(self):
        ctx = build_recovery_context(
            "workspace_save",
            "File not found",
            attempt=3,
        )
        assert "Attempt 3" in ctx

    def test_first_attempt_label(self):
        ctx = build_recovery_context(
            "workspace_save",
            "Permission denied",
            attempt=1,
        )
        assert "Tool failure" in ctx

    def test_includes_analysis(self):
        ctx = build_recovery_context(
            "fetch_url_content",
            "HTTP 404 Not Found",
        )
        assert "404" in ctx or "not found" in ctx.lower()
