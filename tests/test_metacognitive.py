"""Tests for prax.agent.metacognitive — failure pattern learning."""
from __future__ import annotations

import time

from prax.agent.metacognitive import (
    _INJECTION_THRESHOLD,
    _MIN_OCCURRENCES,
    ComponentProfile,
    FailurePattern,
    MetacognitiveStore,
)


class TestFailurePattern:
    def test_new_pattern_is_inactive(self):
        p = FailurePattern(pattern_id="test", description="Test pattern")
        assert not p.is_active  # needs >= 3 occurrences

    def test_pattern_becomes_active_after_occurrences(self):
        p = FailurePattern(
            pattern_id="test", description="Test",
            occurrences=_MIN_OCCURRENCES,
            confidence=_INJECTION_THRESHOLD + 0.1,
        )
        assert p.is_active

    def test_reinforce_failure_increases_confidence(self):
        p = FailurePattern(pattern_id="test", description="Test", confidence=0.5)
        p.reinforce(success=False)
        assert p.confidence > 0.5
        assert p.occurrences == 2

    def test_reinforce_success_decreases_confidence(self):
        p = FailurePattern(pattern_id="test", description="Test", confidence=0.5)
        p.reinforce(success=True)
        assert p.confidence < 0.5
        assert p.occurrences == 2

    def test_confidence_clamped(self):
        p = FailurePattern(pattern_id="test", description="Test", confidence=0.95)
        p.reinforce(success=False)
        assert p.confidence <= 1.0

        p2 = FailurePattern(pattern_id="test", description="Test", confidence=0.05)
        p2.reinforce(success=True)
        assert p2.confidence >= 0.0

    def test_decay(self):
        p = FailurePattern(
            pattern_id="test", description="Test",
            confidence=0.8,
            last_seen=time.time() - 86400 * 10,  # 10 days ago
        )
        p.decay()
        assert p.confidence < 0.8


class TestComponentProfile:
    def test_record_failure_creates_pattern(self):
        profile = ComponentProfile(component="research")
        pattern = profile.record_failure(
            "timeout", "Research agent times out on long queries",
            compensating_instruction="Set shorter timeouts for research queries.",
        )
        assert pattern.pattern_id == "timeout"
        assert pattern.occurrences == 1

    def test_record_failure_increments_existing(self):
        profile = ComponentProfile(component="research")
        profile.record_failure("timeout", "Timeout issue")
        profile.record_failure("timeout", "Timeout issue")
        assert profile.patterns["timeout"].occurrences == 2

    def test_record_success_decreases_confidence(self):
        profile = ComponentProfile(component="research")
        profile.record_failure("timeout", "Timeout", compensating_instruction="Fix it")
        initial_conf = profile.patterns["timeout"].confidence
        profile.record_success("timeout")
        assert profile.patterns["timeout"].confidence < initial_conf

    def test_get_active_patterns_filters(self):
        profile = ComponentProfile(component="research")
        # Create an active pattern (enough occurrences + confidence)
        profile.patterns["active"] = FailurePattern(
            pattern_id="active", description="Active one",
            occurrences=5, confidence=0.7,
            compensating_instruction="Be careful with X.",
        )
        # Create an inactive pattern (too few occurrences)
        profile.patterns["inactive"] = FailurePattern(
            pattern_id="inactive", description="Inactive one",
            occurrences=1, confidence=0.8,
        )
        active = profile.get_active_patterns()
        assert len(active) == 1
        assert active[0].pattern_id == "active"

    def test_prompt_injection_empty_when_no_active(self):
        profile = ComponentProfile(component="test")
        assert profile.prompt_injection() == ""

    def test_prompt_injection_includes_warnings(self):
        profile = ComponentProfile(component="test")
        profile.patterns["p1"] = FailurePattern(
            pattern_id="p1", description="Common failure",
            occurrences=5, confidence=0.7,
            compensating_instruction="Always check X before Y.",
        )
        injection = profile.prompt_injection()
        assert "Known Issues" in injection
        assert "Always check X before Y" in injection
        assert "70%" in injection

    def test_serialization_roundtrip(self):
        profile = ComponentProfile(component="test")
        profile.record_failure("p1", "Description 1", compensating_instruction="Fix 1")
        profile.record_failure("p2", "Description 2")

        data = profile.to_dict()
        restored = ComponentProfile.from_dict(data)
        assert restored.component == "test"
        assert len(restored.patterns) == 2
        assert restored.patterns["p1"].description == "Description 1"


class TestMetacognitiveStore:
    def test_get_profile_creates_if_missing(self, tmp_path):
        store = MetacognitiveStore(profiles_dir=tmp_path)
        profile = store.get_profile("orchestrator")
        assert profile.component == "orchestrator"
        assert len(profile.patterns) == 0

    def test_record_failure_and_persist(self, tmp_path):
        store = MetacognitiveStore(profiles_dir=tmp_path)
        store.record_failure(
            "research", "timeout", "Agent times out",
            compensating_instruction="Use shorter queries.",
        )

        # Verify persistence
        store2 = MetacognitiveStore(profiles_dir=tmp_path)
        profile = store2.get_profile("research")
        assert "timeout" in profile.patterns
        assert profile.patterns["timeout"].occurrences == 1

    def test_record_success(self, tmp_path):
        store = MetacognitiveStore(profiles_dir=tmp_path)
        store.record_failure("comp", "p1", "Issue")
        store.record_success("comp", "p1")
        profile = store.get_profile("comp")
        assert profile.patterns["p1"].occurrences == 2

    def test_get_prompt_injection(self, tmp_path):
        store = MetacognitiveStore(profiles_dir=tmp_path)
        # No patterns yet
        assert store.get_prompt_injection("comp") == ""

        # Add an active pattern
        store.get_profile("comp").patterns["p1"] = FailurePattern(
            pattern_id="p1", description="Known issue",
            occurrences=5, confidence=0.8,
            compensating_instruction="Watch out for this.",
        )
        injection = store.get_prompt_injection("comp")
        assert "Watch out for this" in injection

    def test_get_all_stats(self, tmp_path):
        store = MetacognitiveStore(profiles_dir=tmp_path)
        store.record_failure("a", "p1", "Issue A")
        store.record_failure("b", "p2", "Issue B")
        stats = store.get_all_stats()
        assert "a" in stats
        assert "b" in stats
        assert stats["a"]["total_patterns"] == 1

    def test_reset_component(self, tmp_path):
        store = MetacognitiveStore(profiles_dir=tmp_path)
        store.record_failure("a", "p1", "Issue")
        store.record_failure("b", "p2", "Issue")
        store.reset("a")
        assert "a" not in store.get_all_stats()
        assert "b" in store.get_all_stats()

    def test_reset_all(self, tmp_path):
        store = MetacognitiveStore(profiles_dir=tmp_path)
        store.record_failure("a", "p1", "Issue")
        store.reset()
        assert store.get_all_stats() == {}
