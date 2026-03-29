"""Multi-perspective error recovery — structured failure analysis before retry.

When a tool call or sub-agent delegation fails, instead of blindly retrying,
analyze the failure from multiple perspectives to generate a targeted fix.

Four perspectives (adapted from PR-CoT):
  1. Logical consistency — was the tool called correctly?
  2. Information completeness — was the input missing something?
  3. Assumptions — did the agent assume something false?
  4. Alternative approach — is there a completely different way?

References:
    - Wei et al. (2022). "Chain-of-Thought Prompting Elicits Reasoning in
      Large Language Models." NeurIPS 2022. arXiv:2201.11903.
    - Shinn et al. (2023). "Reflexion: Language Agents with Verbal
      Reinforcement Learning." NeurIPS 2023. arXiv:2303.11366.
    - ATLAS project (itigges22/ATLAS) — PR-CoT multi-perspective repair
      with 85.7% rescue rate on code generation failures.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PerspectiveAnalysis:
    """Analysis of a failure from a single perspective."""

    perspective: str
    diagnosis: str
    suggestion: str
    confidence: float = 0.5  # 0-1, how likely this is the root cause


@dataclass
class ErrorAnalysis:
    """Multi-perspective analysis of a failure."""

    tool_name: str
    error_message: str
    original_args: str
    perspectives: list[PerspectiveAnalysis] = field(default_factory=list)

    @property
    def best_suggestion(self) -> str:
        """Return the suggestion with highest confidence."""
        if not self.perspectives:
            return ""
        best = max(self.perspectives, key=lambda p: p.confidence)
        return best.suggestion

    @property
    def recovery_prompt(self) -> str:
        """Generate a recovery prompt incorporating all perspectives."""
        if not self.perspectives:
            return f"The tool '{self.tool_name}' failed with: {self.error_message}"

        lines = [
            f"The tool '{self.tool_name}' failed.",
            f"Error: {self.error_message}",
            "",
            "Analysis from multiple perspectives:",
        ]

        for p in sorted(self.perspectives, key=lambda x: x.confidence, reverse=True):
            lines.append(
                f"  [{p.perspective}] (confidence: {p.confidence:.0%}) "
                f"{p.diagnosis} → {p.suggestion}"
            )

        lines.append("")
        lines.append(
            "Based on this analysis, try the most promising approach first. "
            "If it fails, try the next perspective."
        )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "error_message": self.error_message,
            "perspectives": [
                {
                    "perspective": p.perspective,
                    "diagnosis": p.diagnosis,
                    "suggestion": p.suggestion,
                    "confidence": p.confidence,
                }
                for p in self.perspectives
            ],
        }


def analyze_tool_failure(
    tool_name: str,
    error_message: str,
    tool_args: str = "",
    context: str = "",
) -> ErrorAnalysis:
    """Analyze a tool failure from multiple perspectives.

    This is a deterministic (non-LLM) analysis based on error patterns.
    For deeper analysis, use analyze_with_llm() which delegates to an LLM.

    Args:
        tool_name: Name of the failed tool.
        error_message: The error message or traceback.
        tool_args: The arguments that were passed to the tool.
        context: Additional context (user message, plan step, etc.).
    """
    analysis = ErrorAnalysis(
        tool_name=tool_name,
        error_message=error_message[:500],
        original_args=tool_args[:500],
    )

    error_lower = error_message.lower()

    # Perspective 1: Logical consistency
    logical = _analyze_logical(tool_name, error_lower, tool_args)
    if logical:
        analysis.perspectives.append(logical)

    # Perspective 2: Information completeness
    completeness = _analyze_completeness(tool_name, error_lower, tool_args)
    if completeness:
        analysis.perspectives.append(completeness)

    # Perspective 3: Assumptions
    assumptions = _analyze_assumptions(tool_name, error_lower, tool_args)
    if assumptions:
        analysis.perspectives.append(assumptions)

    # Perspective 4: Alternative approach
    alternative = _analyze_alternative(tool_name, error_lower, context)
    if alternative:
        analysis.perspectives.append(alternative)

    # If no specific analysis matched, add a generic fallback
    if not analysis.perspectives:
        analysis.perspectives.append(PerspectiveAnalysis(
            perspective="general",
            diagnosis="No specific failure pattern recognized.",
            suggestion=f"Retry with different parameters or try an alternative tool.",
            confidence=0.2,
        ))

    logger.info(
        "Error analysis for %s: %d perspectives, best=%s",
        tool_name,
        len(analysis.perspectives),
        analysis.best_suggestion[:80] if analysis.best_suggestion else "none",
    )
    return analysis


def _analyze_logical(
    tool_name: str, error: str, args: str,
) -> PerspectiveAnalysis | None:
    """Check for logical errors in how the tool was called."""

    if "invalid" in error or "validation" in error:
        return PerspectiveAnalysis(
            perspective="logical_consistency",
            diagnosis="Tool arguments failed validation.",
            suggestion=f"Check the argument types and format for {tool_name}.",
            confidence=0.8,
        )

    if "not found" in error or "404" in error:
        return PerspectiveAnalysis(
            perspective="logical_consistency",
            diagnosis="The referenced resource does not exist.",
            suggestion="Verify the path, URL, or ID exists before using it.",
            confidence=0.7,
        )

    if "permission" in error or "forbidden" in error or "403" in error:
        return PerspectiveAnalysis(
            perspective="logical_consistency",
            diagnosis="Access denied — insufficient permissions.",
            suggestion="Check if the operation requires elevated access or a different auth context.",
            confidence=0.8,
        )

    if "timeout" in error or "timed out" in error:
        return PerspectiveAnalysis(
            perspective="logical_consistency",
            diagnosis="The operation timed out.",
            suggestion="Try a simpler request, reduce the scope, or increase the timeout.",
            confidence=0.6,
        )

    if "rate limit" in error or "429" in error or "too many requests" in error:
        return PerspectiveAnalysis(
            perspective="logical_consistency",
            diagnosis="Rate limit exceeded.",
            suggestion="Wait briefly before retrying, or reduce the number of parallel calls.",
            confidence=0.9,
        )

    return None


def _analyze_completeness(
    tool_name: str, error: str, args: str,
) -> PerspectiveAnalysis | None:
    """Check for missing or incomplete information."""

    if "required" in error or "missing" in error:
        return PerspectiveAnalysis(
            perspective="information_completeness",
            diagnosis="Required information is missing from the tool arguments.",
            suggestion="Check which parameters are required and ensure all are provided.",
            confidence=0.8,
        )

    if "empty" in error or "no content" in error or "null" in error:
        return PerspectiveAnalysis(
            perspective="information_completeness",
            diagnosis="Input content is empty or null.",
            suggestion="Ensure the input data was properly collected before calling the tool.",
            confidence=0.7,
        )

    if not args or args.strip() in ("", "{}", "None"):
        return PerspectiveAnalysis(
            perspective="information_completeness",
            diagnosis="Tool was called with empty/minimal arguments.",
            suggestion="The tool needs substantive input — collect the required data first.",
            confidence=0.6,
        )

    return None


def _analyze_assumptions(
    tool_name: str, error: str, args: str,
) -> PerspectiveAnalysis | None:
    """Check for false assumptions the agent might have made."""

    if "json" in error and ("parse" in error or "decode" in error):
        return PerspectiveAnalysis(
            perspective="assumptions",
            diagnosis="Assumed response would be valid JSON.",
            suggestion="The response may be HTML, plain text, or malformed JSON. Parse defensively.",
            confidence=0.7,
        )

    if "encoding" in error or "codec" in error or "decode" in error:
        return PerspectiveAnalysis(
            perspective="assumptions",
            diagnosis="Assumed response would be valid JSON.",
            suggestion="The response may be HTML, plain text, or malformed JSON. Parse defensively.",
            confidence=0.7,
        )

    if "type" in error and "error" in error:
        return PerspectiveAnalysis(
            perspective="assumptions",
            diagnosis="Wrong data type — assumed one type but got another.",
            suggestion="Check the actual type of the data before processing it.",
            confidence=0.6,
        )

    if "connection" in error or "network" in error or "dns" in error:
        return PerspectiveAnalysis(
            perspective="assumptions",
            diagnosis="Assumed network/service availability.",
            suggestion="The service may be down or the URL may be wrong. Try an alternative source.",
            confidence=0.6,
        )

    return None


def _analyze_alternative(
    tool_name: str, error: str, context: str,
) -> PerspectiveAnalysis | None:
    """Suggest alternative approaches based on the tool and context."""

    # Map tools to their alternatives
    alternatives = {
        "fetch_url_content": (
            "URL fetching failed",
            "Try delegate_browser for JS-heavy pages, or background_search_tool for the topic instead.",
        ),
        "background_search_tool": (
            "Web search failed",
            "Try fetch_url_content on a known URL, or delegate_research for deeper investigation.",
        ),
        "workspace_save": (
            "Workspace save failed",
            "Check if the workspace exists (ensure_workspace), verify the filename is valid.",
        ),
        "delegate_knowledge": (
            "Knowledge delegation failed",
            "Try workspace_save for a simple file, or delegate_task(category='workspace').",
        ),
        "delegate_sandbox": (
            "Sandbox delegation failed",
            "Check if the sandbox container is running. Try a simpler command first.",
        ),
        "delegate_browser": (
            "Browser delegation failed",
            "Try fetch_url_content for simple pages, or background_search_tool for the content.",
        ),
    }

    if tool_name in alternatives:
        diagnosis, suggestion = alternatives[tool_name]
        return PerspectiveAnalysis(
            perspective="alternative_approach",
            diagnosis=diagnosis,
            suggestion=suggestion,
            confidence=0.5,
        )

    return PerspectiveAnalysis(
        perspective="alternative_approach",
        diagnosis=f"{tool_name} failed.",
        suggestion="Consider whether a different tool or delegation path could achieve the same goal.",
        confidence=0.3,
    )


def build_recovery_context(
    tool_name: str,
    error_message: str,
    tool_args: str = "",
    context: str = "",
    attempt: int = 1,
) -> str:
    """Build a recovery context string for injection into retry prompts.

    This is the main entry point for the orchestrator/subagent error paths.
    Returns a formatted string that can be appended to the system message
    or injected as a HumanMessage before retry.
    """
    analysis = analyze_tool_failure(tool_name, error_message, tool_args, context)

    header = f"[Attempt {attempt} failed] " if attempt > 1 else "[Tool failure] "

    return header + analysis.recovery_prompt
