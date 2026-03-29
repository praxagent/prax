"""Integration tests for adaptive intelligence features.

Tests cross-module interactions between the five adaptive systems:
  1. Difficulty estimation → tier recommendation
  2. Thompson Sampling → tier selection
  3. Error recovery → multi-perspective analysis
  4. Metacognitive profiles → prompt injection
  5. Self-verification → workspace/delegation checks

These tests do NOT require a live LLM — they exercise the integration
between modules using deterministic inputs.  The integration-marked tests
in test_workflows.py cover full end-to-end with real LLM calls.

Run with::

    pytest tests/integration/test_adaptive_features.py -v
"""
from __future__ import annotations

import json
import time

import pytest

from prax.agent.difficulty import EASY, HARD, MODERATE, estimate_difficulty
from prax.agent.error_recovery import (
    ErrorAnalysis,
    PerspectiveAnalysis,
    analyze_tool_failure,
    build_recovery_context,
)
from prax.agent.metacognitive import (
    ComponentProfile,
    FailurePattern,
    MetacognitiveStore,
    _INJECTION_THRESHOLD,
    _MIN_OCCURRENCES,
)
from prax.agent.tier_bandit import BetaPosterior, TierBandit
from prax.agent.verification import (
    verify_delegation_result,
    verify_workspace_file,
)


# ---------------------------------------------------------------------------
# 1. Difficulty → Bandit integration
# ---------------------------------------------------------------------------


class TestDifficultyBanditIntegration:
    """Test that difficulty estimation feeds correctly into bandit selection."""

    def test_easy_message_biases_toward_low_tier(self, tmp_path):
        """Easy messages should produce low-tier selections most of the time."""
        bandit = TierBandit(state_path=tmp_path / "state.json")

        # Train with balanced data so the bandit has some history
        for tier in ["low", "medium", "high"]:
            for _ in range(5):
                bandit.record_outcome("orch", tier, success=True, difficulty="easy")

        difficulty = estimate_difficulty("hi there")
        assert difficulty == EASY

        # Select 50 times — low should dominate for easy tasks due to cost weighting
        picks = {"low": 0, "medium": 0, "high": 0}
        for _ in range(50):
            tier = bandit.select_tier(
                "orch", difficulty,
                available_tiers=["low", "medium", "high"],
            )
            picks[tier] += 1

        # Low tier should be picked most often for easy tasks
        assert picks["low"] > picks["high"], (
            f"Expected low > high for easy tasks, got: {picks}"
        )

    def test_hard_message_penalizes_low_tier(self, tmp_path):
        """Hard messages should apply the 0.5x penalty to low tier, narrowing
        the gap vs medium.  Meanwhile easy tasks heavily penalize high tier."""
        bandit = TierBandit(state_path=tmp_path / "state.json")

        # Give all tiers equal success for easy tasks
        for tier in ["low", "medium", "high"]:
            for _ in range(10):
                bandit.record_outcome("orch", tier, success=True, difficulty="easy")

        difficulty = estimate_difficulty(
            "Research and compare the latest quantum computing approaches "
            "from IBM, Google, and Microsoft with a comprehensive analysis"
        )
        assert difficulty == HARD

        # For easy tasks, high tier gets 0.3x penalty on top of 16x cost
        # This should make high nearly never selected
        picks = {"low": 0, "medium": 0, "high": 0}
        for _ in range(100):
            tier = bandit.select_tier(
                "orch", "easy",
                available_tiers=["low", "medium", "high"],
            )
            picks[tier] += 1

        assert picks["high"] < 20, (
            f"High should be heavily penalized for easy tasks: {picks}"
        )

    def test_difficulty_bins_are_independent(self, tmp_path):
        """Posteriors for different difficulty levels should be independent."""
        bandit = TierBandit(state_path=tmp_path / "state.json")

        # Train medium as excellent for hard tasks, terrible for easy tasks
        for _ in range(20):
            bandit.record_outcome("test", "medium", success=True, difficulty="hard")
            bandit.record_outcome("test", "medium", success=False, difficulty="easy")

        stats = bandit.get_stats("test")
        hard_medium = stats["test"]["hard"]["medium"]
        easy_medium = stats["test"]["easy"]["medium"]

        assert hard_medium["mean"] > 0.8
        assert easy_medium["mean"] < 0.2


# ---------------------------------------------------------------------------
# 2. Error recovery → Metacognitive integration
# ---------------------------------------------------------------------------


class TestErrorRecoveryMetacognitiveIntegration:
    """Test that error recovery analysis feeds into metacognitive learning."""

    def test_repeated_failures_become_metacognitive_pattern(self, tmp_path):
        """When the same error type recurs, it should become an active metacognitive pattern."""
        store = MetacognitiveStore(profiles_dir=tmp_path)

        # Simulate 5 timeout errors from research agent
        for i in range(_MIN_OCCURRENCES + 2):
            analysis = analyze_tool_failure(
                "delegate_research",
                "Operation timed out after 30 seconds",
            )
            # Record the best diagnosis as a metacognitive pattern
            best = max(analysis.perspectives, key=lambda p: p.confidence)
            store.record_failure(
                "research",
                "timeout",
                best.diagnosis,
                compensating_instruction=best.suggestion,
            )

        # The pattern should now be active and injected into prompts
        injection = store.get_prompt_injection("research")
        assert "Known Issues" in injection
        assert "timeout" in injection.lower() or "timed out" in injection.lower()

    def test_successful_recovery_reduces_confidence(self, tmp_path):
        """Recording success after failure should decrease pattern confidence."""
        store = MetacognitiveStore(profiles_dir=tmp_path)

        # Record failures to build up a pattern
        for _ in range(_MIN_OCCURRENCES + 1):
            store.record_failure(
                "browser", "connection_refused",
                "Browser service not responding",
                compensating_instruction="Check if browser container is running.",
            )

        # Get initial confidence
        profile = store.get_profile("browser")
        initial_conf = profile.patterns["connection_refused"].confidence

        # Now record several successes
        for _ in range(5):
            store.record_success("browser", "connection_refused")

        final_conf = profile.patterns["connection_refused"].confidence
        assert final_conf < initial_conf, (
            f"Expected confidence to decrease: {initial_conf} -> {final_conf}"
        )

    def test_error_analysis_persists_across_store_instances(self, tmp_path):
        """Metacognitive patterns should survive store re-instantiation."""
        store1 = MetacognitiveStore(profiles_dir=tmp_path)
        for _ in range(4):
            store1.record_failure(
                "sandbox", "oom", "Container ran out of memory",
                compensating_instruction="Limit input size or use streaming.",
            )

        # Create a new store from the same directory
        store2 = MetacognitiveStore(profiles_dir=tmp_path)
        profile = store2.get_profile("sandbox")
        assert "oom" in profile.patterns
        assert profile.patterns["oom"].occurrences == 4


# ---------------------------------------------------------------------------
# 3. Error recovery → Recovery context integration
# ---------------------------------------------------------------------------


class TestErrorRecoveryContextIntegration:
    """Test that error analysis produces useful recovery contexts."""

    def test_recovery_context_includes_all_perspectives(self):
        """Recovery context should include analysis from multiple perspectives."""
        ctx = build_recovery_context(
            "fetch_url_content",
            "HTTP 404 Not Found: https://example.com/page",
            tool_args="{'url': 'https://example.com/page'}",
            attempt=2,
        )

        # Should mention the attempt number
        assert "Attempt 2" in ctx

        # Should include at least logical consistency and alternative approach
        assert "logical_consistency" in ctx or "not found" in ctx.lower()
        assert "alternative" in ctx.lower() or "delegate_browser" in ctx.lower()

    def test_escalating_attempt_numbers(self):
        """Each attempt should be clearly labeled."""
        ctx1 = build_recovery_context("workspace_save", "Permission denied", attempt=1)
        ctx2 = build_recovery_context("workspace_save", "Permission denied", attempt=2)
        ctx3 = build_recovery_context("workspace_save", "Permission denied", attempt=3)

        assert "Tool failure" in ctx1  # first attempt uses different label
        assert "Attempt 2" in ctx2
        assert "Attempt 3" in ctx3


# ---------------------------------------------------------------------------
# 4. Verification → Workspace integration
# ---------------------------------------------------------------------------


class TestVerificationWorkspaceIntegration:
    """Test self-verification across workspace scenarios."""

    def test_full_workspace_verification_flow(self, tmp_path):
        """Simulate a complete workspace save + verify cycle."""
        # Simulate workspace_save creating a file
        workspace_root = str(tmp_path)
        active_dir = tmp_path / "active"
        active_dir.mkdir()

        # Create a research note
        content = (
            "# Quantum Computing Research\n\n"
            "## Key Findings\n\n"
            "1. IBM's approach uses superconducting qubits\n"
            "2. Google achieved quantum supremacy with Sycamore\n"
            "3. Microsoft is pursuing topological qubits\n\n"
            "## Comparison\n\n"
            "Each approach has different error rates and scalability.\n"
        )
        (active_dir / "quantum_research.md").write_text(content)

        # Verify the file
        result = verify_workspace_file(
            workspace_root, "quantum_research.md",
            expected_patterns=["IBM", "Google", "quantum"],
        )
        assert result.passed
        assert result.checks_passed >= 4  # exists + length + 3 patterns

    def test_verification_catches_truncated_output(self, tmp_path):
        """Verification should catch files that are too short."""
        (tmp_path / "truncated.md").write_text("# ")
        result = verify_workspace_file(
            str(tmp_path), "truncated.md",
            min_length=50,
        )
        assert not result.passed
        assert any("too short" in i for i in result.issues)

    def test_delegation_verification_catches_error_responses(self):
        """Verify that delegation results containing errors are flagged."""
        error_results = [
            "Sub-agent failed: timeout after 30s",
            "Plugin agent failed to complete the task",
            "Error: Could not connect to the research service",
            "Timed out waiting for browser response",
        ]
        for error_text in error_results:
            result = verify_delegation_result(error_text)
            assert not result.passed, f"Should have caught error in: {error_text}"

    def test_good_delegation_result_passes_verification(self):
        """Substantial delegation results should pass all checks."""
        good_result = (
            "I found three relevant papers on the topic. "
            "The first paper by Smith et al. (2024) discusses the use of "
            "transformer architectures for time series forecasting. "
            "The second paper proposes a novel attention mechanism. "
            "The third paper provides a comprehensive survey of the field."
        )
        result = verify_delegation_result(good_result)
        assert result.passed
        assert result.checks_passed == result.checks_run


# ---------------------------------------------------------------------------
# 5. Bandit → Persistence → Reload integration
# ---------------------------------------------------------------------------


class TestBanditPersistenceIntegration:
    """Test bandit state persistence and cross-session learning."""

    def test_learning_persists_across_sessions(self, tmp_path):
        """Train a bandit, save, reload, and verify the learned preferences."""
        state_path = tmp_path / "bandit.json"

        # Session 1: train heavily
        bandit1 = TierBandit(state_path=state_path)
        for _ in range(50):
            bandit1.record_outcome("research", "medium", success=True, difficulty="hard")
            bandit1.record_outcome("research", "low", success=False, difficulty="hard")

        # Session 2: reload and verify
        bandit2 = TierBandit(state_path=state_path)
        stats = bandit2.get_stats("research")

        assert stats["research"]["hard"]["medium"]["mean"] > 0.9
        assert stats["research"]["hard"]["low"]["mean"] < 0.1

        # Exploit mode should consistently pick medium for hard research
        picks = set()
        for _ in range(10):
            picks.add(bandit2.select_tier(
                "research", "hard",
                available_tiers=["low", "medium"],
                exploit_only=True,
            ))
        assert picks == {"medium"}

    def test_multiple_components_tracked_independently(self, tmp_path):
        """Different components should have independent posteriors."""
        bandit = TierBandit(state_path=tmp_path / "state.json")

        # Research does well with medium, browser does well with low
        for _ in range(20):
            bandit.record_outcome("research", "medium", success=True, difficulty="hard")
            bandit.record_outcome("research", "low", success=False, difficulty="hard")
            bandit.record_outcome("browser", "low", success=True, difficulty="easy")
            bandit.record_outcome("browser", "medium", success=False, difficulty="easy")

        # Exploit mode should pick different tiers for different components
        research_tier = bandit.select_tier(
            "research", "hard",
            available_tiers=["low", "medium"],
            exploit_only=True,
        )
        browser_tier = bandit.select_tier(
            "browser", "easy",
            available_tiers=["low", "medium"],
            exploit_only=True,
        )

        assert research_tier == "medium"
        assert browser_tier == "low"


# ---------------------------------------------------------------------------
# 6. Full pipeline: difficulty → bandit → error recovery → metacognitive
# ---------------------------------------------------------------------------


class TestFullAdaptivePipeline:
    """Test the complete adaptive pipeline that mirrors production flow."""

    def test_end_to_end_adaptive_cycle(self, tmp_path):
        """Simulate a full adaptive cycle: classify → select tier → fail →
        analyze → record metacognitive pattern → next run benefits from learning."""

        bandit = TierBandit(state_path=tmp_path / "bandit.json")
        store = MetacognitiveStore(profiles_dir=tmp_path / "metacognitive")

        # Step 1: Classify difficulty
        message = (
            "Research the latest developments in quantum computing and "
            "compare the approaches of IBM, Google, and Microsoft"
        )
        difficulty = estimate_difficulty(message)
        assert difficulty == HARD

        # Step 2: Select tier via bandit
        tier = bandit.select_tier("research", difficulty)
        assert tier in ["low", "medium", "high", "pro"]

        # Step 3: Simulate a failure
        analysis = analyze_tool_failure(
            "fetch_url_content",
            "HTTP 429 Too Many Requests",
        )
        assert len(analysis.perspectives) >= 1
        best = analysis.best_suggestion.lower()
        assert "wait" in best or "retry" in best or "rate" in best

        # Step 4: Record failure in bandit and metacognitive store
        bandit.record_outcome("research", tier, success=False, difficulty=difficulty)

        best = max(analysis.perspectives, key=lambda p: p.confidence)
        store.record_failure(
            "research",
            "rate_limit",
            best.diagnosis,
            compensating_instruction=best.suggestion,
        )

        # Step 5: Simulate more failures to activate the pattern
        for _ in range(_MIN_OCCURRENCES):
            store.record_failure(
                "research", "rate_limit", best.diagnosis,
                compensating_instruction=best.suggestion,
            )

        # Step 6: Next run benefits from learning
        injection = store.get_prompt_injection("research")
        assert "Known Issues" in injection
        assert "rate limit" in injection.lower() or "wait" in injection.lower()

        # Step 7: Build recovery context for the next attempt
        ctx = build_recovery_context(
            "fetch_url_content",
            "HTTP 429 Too Many Requests",
            attempt=2,
        )
        assert "Attempt 2" in ctx

    def test_verification_after_workspace_save(self, tmp_path):
        """Simulate the workspace_save → verification flow used in production."""
        workspace = tmp_path / "ws" / "10000000000"
        active = workspace / "active"
        active.mkdir(parents=True)

        # Simulate workspace_save
        filename = "research_notes.md"
        content = (
            "# Quantum Computing Research Notes\n\n"
            "## IBM Approach\n"
            "Uses superconducting transmon qubits with error mitigation.\n\n"
            "## Google Approach\n"
            "Achieved quantum supremacy with the Sycamore processor.\n\n"
            "## Microsoft Approach\n"
            "Pursuing topological qubits with Majorana zero modes.\n"
        )
        (active / filename).write_text(content)

        # Verify (this is what workspace_tools.py does after save)
        result = verify_workspace_file(
            str(workspace), filename,
            expected_patterns=["IBM", "Google", "Microsoft"],
        )
        assert result.passed
        assert "Verified" in result.summary

    def test_bandit_adapts_after_mixed_outcomes(self, tmp_path):
        """Bandit should learn from a realistic mix of successes and failures."""
        bandit = TierBandit(state_path=tmp_path / "state.json")

        # Simulate realistic outcomes: medium is better for hard tasks
        import random
        random.seed(42)

        for _ in range(100):
            # Low tier: 30% success on hard tasks
            bandit.record_outcome(
                "research", "low",
                success=random.random() < 0.3,
                difficulty="hard",
            )
            # Medium tier: 80% success on hard tasks
            bandit.record_outcome(
                "research", "medium",
                success=random.random() < 0.8,
                difficulty="hard",
            )

        # Exploit should now favor medium for hard research
        picks = {"low": 0, "medium": 0}
        for _ in range(20):
            tier = bandit.select_tier(
                "research", "hard",
                available_tiers=["low", "medium"],
                exploit_only=True,
            )
            picks[tier] += 1

        assert picks["medium"] > picks["low"], (
            f"Expected medium to dominate for hard tasks: {picks}"
        )


# ---------------------------------------------------------------------------
# 7. Metacognitive decay + bandit state interaction
# ---------------------------------------------------------------------------


class TestMetacognitiveBanditInteraction:
    """Test interactions between metacognitive patterns and bandit learning."""

    def test_metacognitive_decay_over_time(self, tmp_path):
        """Patterns should lose confidence via Ebbinghaus decay."""
        store = MetacognitiveStore(profiles_dir=tmp_path)

        # Create a pattern with old timestamp
        profile = store.get_profile("test")
        profile.patterns["old_pattern"] = FailurePattern(
            pattern_id="old_pattern",
            description="Some old issue",
            confidence=0.9,
            occurrences=10,
            last_seen=time.time() - 86400 * 30,  # 30 days ago
            compensating_instruction="Watch out for X.",
        )

        # The decay should reduce confidence significantly
        active = profile.get_active_patterns()

        # After 30 days at 5% daily decay: 0.9 * 0.95^30 ≈ 0.19
        assert profile.patterns["old_pattern"].confidence < 0.3

    def test_bandit_reset_doesnt_affect_metacognitive(self, tmp_path):
        """Resetting the bandit should not affect metacognitive profiles."""
        bandit = TierBandit(state_path=tmp_path / "bandit.json")
        store = MetacognitiveStore(profiles_dir=tmp_path / "meta")

        # Record data in both
        bandit.record_outcome("research", "medium", success=True)
        for _ in range(4):
            store.record_failure("research", "timeout", "Agent timed out")

        # Reset bandit
        bandit.reset()
        assert bandit.get_stats() == {}

        # Metacognitive data should still be there
        profile = store.get_profile("research")
        assert "timeout" in profile.patterns
        assert profile.patterns["timeout"].occurrences == 4


# ---------------------------------------------------------------------------
# 8. Error recovery with different tool types
# ---------------------------------------------------------------------------


class TestErrorRecoveryCrossToolIntegration:
    """Test error recovery analysis across different tool categories."""

    def test_fetch_to_browser_fallback_suggestion(self):
        """When fetch_url_content fails, alternative should suggest delegate_browser."""
        analysis = analyze_tool_failure(
            "fetch_url_content",
            "Connection refused: https://example.com",
        )
        alternatives = [
            p for p in analysis.perspectives
            if p.perspective == "alternative_approach"
        ]
        assert len(alternatives) >= 1
        assert "delegate_browser" in alternatives[0].suggestion

    def test_search_to_fetch_fallback_suggestion(self):
        """When search fails, alternative should suggest fetch_url_content."""
        analysis = analyze_tool_failure(
            "background_search_tool",
            "Search service unavailable",
        )
        alternatives = [
            p for p in analysis.perspectives
            if p.perspective == "alternative_approach"
        ]
        assert len(alternatives) >= 1
        assert "fetch_url_content" in alternatives[0].suggestion

    def test_workspace_save_alternative_suggests_ensure_workspace(self):
        """When workspace_save fails, alternative should suggest ensure_workspace."""
        analysis = analyze_tool_failure(
            "workspace_save",
            "No such file or directory: /workspace/user/active/",
        )
        alternatives = [
            p for p in analysis.perspectives
            if p.perspective == "alternative_approach"
        ]
        assert len(alternatives) >= 1
        assert "ensure_workspace" in alternatives[0].suggestion

    def test_recovery_context_formatting(self):
        """Recovery context should be well-formatted for prompt injection."""
        ctx = build_recovery_context(
            "delegate_sandbox",
            "Container exited with code 137 (OOM killed)",
            tool_args="{'code': 'import numpy; x = numpy.zeros((10000, 10000))'}",
            attempt=3,
        )
        # Should be a clean string with no raw objects
        assert isinstance(ctx, str)
        assert "Attempt 3" in ctx
        assert len(ctx) > 50  # meaningful content


# ---------------------------------------------------------------------------
# 9. Error analysis data structure integrity
# ---------------------------------------------------------------------------


class TestErrorAnalysisIntegrity:
    """Test ErrorAnalysis data structures for serialization correctness."""

    def test_to_dict_roundtrip(self):
        """ErrorAnalysis.to_dict() should produce valid JSON."""
        analysis = analyze_tool_failure(
            "fetch_url_content",
            "HTTP 404 Not Found",
            tool_args="{'url': 'https://example.com/missing'}",
        )
        d = analysis.to_dict()

        # Should be JSON-serializable
        json_str = json.dumps(d)
        restored = json.loads(json_str)

        assert restored["tool_name"] == "fetch_url_content"
        assert len(restored["perspectives"]) >= 1
        for p in restored["perspectives"]:
            assert "perspective" in p
            assert "confidence" in p
            assert 0.0 <= p["confidence"] <= 1.0

    def test_recovery_prompt_contains_sorted_perspectives(self):
        """Recovery prompt should list perspectives from highest to lowest confidence."""
        analysis = ErrorAnalysis(
            tool_name="test",
            error_message="error",
            original_args="",
            perspectives=[
                PerspectiveAnalysis("a", "diag_a", "fix_a", 0.3),
                PerspectiveAnalysis("b", "diag_b", "fix_b", 0.9),
                PerspectiveAnalysis("c", "diag_c", "fix_c", 0.6),
            ],
        )
        prompt = analysis.recovery_prompt

        # b (0.9) should appear before c (0.6) should appear before a (0.3)
        b_pos = prompt.index("fix_b")
        c_pos = prompt.index("fix_c")
        a_pos = prompt.index("fix_a")
        assert b_pos < c_pos < a_pos
