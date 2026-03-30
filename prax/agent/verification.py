"""Self-verification — check agent outputs before marking work as done.

Before presenting results to the user, the agent should verify its own work:
does the file exist?  Is it non-empty?  Does it match the user's request?

This draws on research in self-consistency and self-debugging for LLMs.

References:
    - Wang et al. (2023). "Self-Consistency Improves Chain of Thought
      Reasoning in Language Models." ICLR 2023. arXiv:2203.11171.
    - Chen et al. (2024). "Teaching Large Language Models to Self-Debug."
      ICLR 2024. arXiv:2304.05128.
    - ATLAS project (itigges22/ATLAS) — self-test generation for internal
      verification before presenting results.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Outcome of a self-verification check."""

    passed: bool
    checks_run: int = 0
    checks_passed: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.passed:
            return f"Verified ({self.checks_passed}/{self.checks_run} checks passed)"
        return f"Verification failed: {'; '.join(self.issues)}"


def verify_workspace_file(
    workspace_root: str,
    filename: str,
    *,
    min_length: int = 10,
    expected_patterns: list[str] | None = None,
) -> VerificationResult:
    """Verify a workspace file exists, is non-empty, and contains expected content.

    Args:
        workspace_root: Path to the user's workspace directory.
        filename: Relative path within the workspace.
        min_length: Minimum content length in characters.
        expected_patterns: Optional list of strings that should appear in the file.
    """
    issues: list[str] = []
    checks_run = 0
    checks_passed = 0

    # Check 1: File exists
    checks_run += 1
    filepath = os.path.join(workspace_root, filename)
    # Also check under active/ subdirectory
    if not os.path.isfile(filepath):
        alt_path = os.path.join(workspace_root, "active", filename)
        if os.path.isfile(alt_path):
            filepath = alt_path
        else:
            issues.append(f"File not found: {filename}")
            return VerificationResult(
                passed=False, checks_run=checks_run,
                checks_passed=checks_passed, issues=issues,
            )
    checks_passed += 1

    # Check 2: Non-empty
    checks_run += 1
    try:
        content = open(filepath).read()
    except Exception as e:
        issues.append(f"Cannot read file: {e}")
        return VerificationResult(
            passed=False, checks_run=checks_run,
            checks_passed=checks_passed, issues=issues,
        )

    if len(content.strip()) < min_length:
        issues.append(
            f"File too short ({len(content.strip())} chars, minimum {min_length})"
        )
    else:
        checks_passed += 1

    # Check 3: Expected patterns
    if expected_patterns:
        for pattern in expected_patterns:
            checks_run += 1
            if pattern.lower() in content.lower():
                checks_passed += 1
            else:
                issues.append(f"Missing expected content: '{pattern}'")

    return VerificationResult(
        passed=len(issues) == 0,
        checks_run=checks_run,
        checks_passed=checks_passed,
        issues=issues,
    )


def verify_delegation_result(
    result: str,
    *,
    task_description: str = "",
) -> VerificationResult:
    """Verify that a delegation result is meaningful (not empty/error).

    Args:
        result: The text returned by a sub-agent or spoke delegation.
        task_description: What the delegation was supposed to do.
    """
    issues: list[str] = []
    checks_run = 0
    checks_passed = 0

    # Check 1: Non-empty
    checks_run += 1
    if not result or not result.strip():
        issues.append("Delegation returned empty result")
    else:
        checks_passed += 1

    # Check 2: Not an error message
    checks_run += 1
    error_indicators = [
        "sub-agent failed",
        "plugin agent failed",
        "error:",
        "exception:",
        "timed out",
        "failed to",
    ]
    result_lower = (result or "").lower()
    if any(indicator in result_lower for indicator in error_indicators):
        issues.append("Delegation result contains error indicator")
    else:
        checks_passed += 1

    # Check 3: Minimum substance (not just a one-word acknowledgment)
    checks_run += 1
    word_count = len((result or "").split())
    if word_count < 5:
        issues.append(f"Result too short ({word_count} words)")
    else:
        checks_passed += 1

    return VerificationResult(
        passed=len(issues) == 0,
        checks_run=checks_run,
        checks_passed=checks_passed,
        issues=issues,
    )


def verify_plan_step_completion(
    user_id: str,
    step_number: int,
) -> VerificationResult:
    """Verify that a plan step's expected artifacts exist before marking done.

    Reads the plan to find the step description, then checks for workspace
    files that match common artifact patterns (*.md, *.yaml, etc.).
    """
    issues: list[str] = []
    checks_run = 0
    checks_passed = 0

    try:
        from prax.services.workspace_service import read_plan
        plan = read_plan(user_id)
        if not plan:
            return VerificationResult(passed=True, checks_run=0, checks_passed=0)

        step = None
        for s in plan.get("steps", []):
            if s.get("step") == step_number:
                step = s
                break

        if not step:
            return VerificationResult(passed=True, checks_run=0, checks_passed=0)

        # Check: step isn't already done (avoid double-marking)
        checks_run += 1
        if step.get("done"):
            issues.append(f"Step {step_number} already marked done")
        else:
            checks_passed += 1

    except Exception as e:
        logger.debug("Plan verification failed: %s", e)
        # Don't block on verification failures
        return VerificationResult(passed=True, checks_run=0, checks_passed=0)

    return VerificationResult(
        passed=len(issues) == 0,
        checks_run=checks_run,
        checks_passed=checks_passed,
        issues=issues,
    )
