"""Metacognitive failure profiles — track and learn from systematic weaknesses.

Maintains per-component profiles of recurring failure patterns.  When a
spoke or sub-agent fails in a characteristic way, the pattern is recorded
with a confidence score.  On subsequent runs, high-confidence patterns are
injected as warnings into the component's system prompt so it can
compensate.

Confidence scores decay over time (Ebbinghaus-inspired forgetting curve)
so stale patterns are naturally pruned.

References:
    - Flavell, J.H. (1979). "Metacognition and Cognitive Monitoring: A New
      Area of Cognitive-Developmental Inquiry." American Psychologist.
    - Shinn et al. (2023). "Reflexion: Language Agents with Verbal
      Reinforcement Learning." NeurIPS 2023. arXiv:2303.11366.
    - Madaan et al. (2023). "Self-Refine: Iterative Refinement with
      Self-Feedback." NeurIPS 2023. arXiv:2303.17651.
    - ATLAS project (itigges22/ATLAS) — metacognitive model with
      per-category failure pattern tracking and confidence decay.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PROFILES_DIR = Path(__file__).parent.parent / "data" / "metacognitive"

# Minimum confidence to inject a pattern as a prompt warning
_INJECTION_THRESHOLD = 0.4

# Decay rate per day (Ebbinghaus-inspired)
_DECAY_RATE_PER_DAY = 0.05

# Maximum patterns to inject into a prompt (token budget)
_MAX_INJECTED_PATTERNS = 5

# Minimum occurrences before a pattern becomes active
_MIN_OCCURRENCES = 3


@dataclass
class FailurePattern:
    """A recurring failure pattern for a component."""

    pattern_id: str
    description: str
    category: str = ""  # task category when the failure occurred
    confidence: float = 0.5
    occurrences: int = 1
    last_seen: float = field(default_factory=time.time)
    compensating_instruction: str = ""

    @property
    def is_active(self) -> bool:
        """A pattern is active when it has enough occurrences and confidence."""
        return self.occurrences >= _MIN_OCCURRENCES and self.confidence >= _INJECTION_THRESHOLD

    def decay(self) -> None:
        """Apply time-based confidence decay."""
        days_elapsed = (time.time() - self.last_seen) / 86400
        if days_elapsed > 0:
            self.confidence *= (1.0 - _DECAY_RATE_PER_DAY) ** days_elapsed

    def reinforce(self, *, success: bool) -> None:
        """Update the pattern based on a new observation."""
        self.last_seen = time.time()
        self.occurrences += 1
        if success:
            # Pattern was compensated for successfully — decrease confidence
            # (the fix is working, pattern is less of a problem)
            self.confidence = max(0.0, self.confidence - 0.1)
        else:
            # Pattern recurred — increase confidence
            self.confidence = min(1.0, self.confidence + 0.15)


@dataclass
class ComponentProfile:
    """Metacognitive profile for a single component (spoke/sub-agent)."""

    component: str
    patterns: dict[str, FailurePattern] = field(default_factory=dict)

    def record_failure(
        self,
        pattern_id: str,
        description: str,
        *,
        category: str = "",
        compensating_instruction: str = "",
    ) -> FailurePattern:
        """Record a failure pattern occurrence."""
        if pattern_id in self.patterns:
            pattern = self.patterns[pattern_id]
            pattern.reinforce(success=False)
            if compensating_instruction:
                pattern.compensating_instruction = compensating_instruction
        else:
            pattern = FailurePattern(
                pattern_id=pattern_id,
                description=description,
                category=category,
                compensating_instruction=compensating_instruction,
            )
            self.patterns[pattern_id] = pattern

        logger.info(
            "Metacognitive: recorded failure pattern '%s' for %s "
            "(confidence=%.2f, occurrences=%d)",
            pattern_id, self.component, pattern.confidence, pattern.occurrences,
        )
        return pattern

    def record_success(self, pattern_id: str) -> None:
        """Record that a known pattern was successfully compensated."""
        if pattern_id in self.patterns:
            self.patterns[pattern_id].reinforce(success=True)

    def get_active_patterns(self) -> list[FailurePattern]:
        """Return patterns that should be injected into prompts."""
        # Apply decay first
        for pattern in self.patterns.values():
            pattern.decay()

        # Prune dead patterns (confidence < 0.1 and old)
        dead = [
            pid for pid, p in self.patterns.items()
            if p.confidence < 0.1 and p.occurrences > 1
        ]
        for pid in dead:
            del self.patterns[pid]
            logger.debug("Pruned dead pattern: %s/%s", self.component, pid)

        active = [p for p in self.patterns.values() if p.is_active]
        # Sort by confidence descending, take top N
        active.sort(key=lambda p: p.confidence, reverse=True)
        return active[:_MAX_INJECTED_PATTERNS]

    def prompt_injection(self) -> str:
        """Generate prompt text with warnings about known failure patterns.

        Returns empty string if no active patterns.
        """
        active = self.get_active_patterns()
        if not active:
            return ""

        lines = [
            "\n--- Known Issues (from past runs) ---",
            "The following patterns have caused failures in previous runs. "
            "Be aware and compensate:",
        ]
        for i, p in enumerate(active, 1):
            warning = p.compensating_instruction or f"Watch out for: {p.description}"
            lines.append(
                f"  {i}. [{p.confidence:.0%} confidence] {warning}"
            )
        lines.append("---")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "patterns": {pid: asdict(p) for pid, p in self.patterns.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> ComponentProfile:
        profile = cls(component=data["component"])
        for pid, pdata in data.get("patterns", {}).items():
            profile.patterns[pid] = FailurePattern(**pdata)
        return profile


class MetacognitiveStore:
    """Persistent store for all component metacognitive profiles."""

    def __init__(self, profiles_dir: Path | None = None):
        self._dir = profiles_dir or _DEFAULT_PROFILES_DIR
        self._lock = threading.Lock()
        self._profiles: dict[str, ComponentProfile] = {}
        self._load_all()

    def get_profile(self, component: str) -> ComponentProfile:
        """Get or create a profile for a component."""
        with self._lock:
            if component not in self._profiles:
                self._profiles[component] = ComponentProfile(component=component)
            return self._profiles[component]

    def record_failure(
        self,
        component: str,
        pattern_id: str,
        description: str,
        *,
        category: str = "",
        compensating_instruction: str = "",
    ) -> FailurePattern:
        """Record a failure pattern for a component."""
        profile = self.get_profile(component)
        pattern = profile.record_failure(
            pattern_id, description,
            category=category,
            compensating_instruction=compensating_instruction,
        )
        self._save(component)
        return pattern

    def record_success(self, component: str, pattern_id: str) -> None:
        """Record successful compensation of a known pattern."""
        profile = self.get_profile(component)
        profile.record_success(pattern_id)
        self._save(component)

    def get_prompt_injection(self, component: str) -> str:
        """Get prompt injection text for a component's known issues."""
        return self.get_profile(component).prompt_injection()

    def get_all_stats(self) -> dict:
        """Return statistics for all components."""
        with self._lock:
            return {
                comp: {
                    "total_patterns": len(profile.patterns),
                    "active_patterns": len(profile.get_active_patterns()),
                    "patterns": [
                        {
                            "id": p.pattern_id,
                            "description": p.description,
                            "confidence": round(p.confidence, 3),
                            "occurrences": p.occurrences,
                            "active": p.is_active,
                        }
                        for p in profile.patterns.values()
                    ],
                }
                for comp, profile in self._profiles.items()
            }

    def _save(self, component: str) -> None:
        """Persist a single component profile."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            profile = self._profiles.get(component)
            if profile:
                path = self._dir / f"{component}.json"
                path.write_text(json.dumps(profile.to_dict(), indent=2))
        except Exception:
            logger.debug("Failed to save metacognitive profile for %s", component, exc_info=True)

    def _load_all(self) -> None:
        """Load all profiles from disk."""
        if not self._dir.exists():
            return
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                profile = ComponentProfile.from_dict(data)
                self._profiles[profile.component] = profile
            except Exception:
                logger.debug("Failed to load profile from %s", path, exc_info=True)
        if self._profiles:
            logger.info(
                "Loaded metacognitive profiles: %s",
                list(self._profiles.keys()),
            )

    def reset(self, component: str | None = None) -> None:
        """Clear profiles.  If component is given, clear only that one."""
        with self._lock:
            if component:
                self._profiles.pop(component, None)
                path = self._dir / f"{component}.json"
                if path.exists():
                    path.unlink()
            else:
                self._profiles.clear()
                if self._dir.exists():
                    for path in self._dir.glob("*.json"):
                        path.unlink()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: MetacognitiveStore | None = None
_store_lock = threading.Lock()


def get_metacognitive_store(
    profiles_dir: Path | None = None,
) -> MetacognitiveStore:
    """Return the singleton MetacognitiveStore."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MetacognitiveStore(profiles_dir=profiles_dir)
    return _store
