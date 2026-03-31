"""Phase 4 — Semantic Entropy gate for HIGH-risk tool calls.

For HIGH-risk tool calls, queries the LLM k=3 times at elevated
temperature (T=0.7) with the same context.  Compares the proposed
tool calls across samples.  If they diverge (all 3 different tools),
the model is uncertain — block and force read-only mode.

This is an *expensive* check (3x LLM cost per gated call) and is
therefore gated behind the ``ACTIVE_INFERENCE_SEMANTIC_GATE`` env var.
Only enabled when the env var is set to "1" or "true".

See Research S17 in README — "Active Inference, Extrinsic Uncertainty
Measurement, and the Harness as Markov Blanket".
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Number of re-query samples for divergence detection.
_K_SAMPLES = 3

# Temperature used for the divergence re-queries.
_SAMPLE_TEMPERATURE = 0.7

# Minimum agreement ratio to pass the gate (2 out of 3).
_MIN_AGREEMENT_RATIO = 2 / 3


# ---------------------------------------------------------------------------
# Result data
# ---------------------------------------------------------------------------


@dataclass
class SemanticEntropyResult:
    """Outcome of a semantic entropy check for a single tool call."""

    proposed_tool: str
    sampled_tools: list[str] = field(default_factory=list)
    agreement_ratio: float = 1.0
    blocked: bool = False
    error: str | None = None

    @property
    def converged(self) -> bool:
        """True when the majority of samples agree on the tool name."""
        return self.agreement_ratio >= _MIN_AGREEMENT_RATIO


# ---------------------------------------------------------------------------
# Module-level buffer for trace integration
# ---------------------------------------------------------------------------

_entropy_results: list[SemanticEntropyResult] = []
_entropy_lock = threading.Lock()


def drain_semantic_entropy_buffer() -> list[SemanticEntropyResult]:
    """Return and clear all buffered semantic entropy results."""
    with _entropy_lock:
        entries = list(_entropy_results)
        _entropy_results.clear()
    return entries


def _record_result(result: SemanticEntropyResult) -> None:
    """Append a result to the module-level buffer (thread-safe)."""
    with _entropy_lock:
        _entropy_results.append(result)


# ---------------------------------------------------------------------------
# Gate implementation
# ---------------------------------------------------------------------------


class SemanticEntropyGate:
    """Re-queries the LLM multiple times to detect tool-call divergence.

    Parameters
    ----------
    llm : BaseLanguageModel
        The LLM instance to use for re-queries.  A copy is made internally
        with temperature overridden to ``_SAMPLE_TEMPERATURE``.
    messages : list
        The conversation messages that led to the proposed tool call.
    proposed_tool : str
        The tool name the model originally chose.
    """

    def __init__(self, llm, messages: list, proposed_tool: str) -> None:
        self.llm = llm
        self.messages = messages
        self.proposed_tool = proposed_tool

    def check(self) -> SemanticEntropyResult:
        """Run k re-queries and return the divergence verdict."""
        sampled_tools: list[str] = []

        try:
            sample_llm = self._build_sample_llm()
        except Exception as exc:
            logger.debug("Failed to build sample LLM: %s", exc)
            result = SemanticEntropyResult(
                proposed_tool=self.proposed_tool,
                error=f"LLM build error: {exc}",
            )
            _record_result(result)
            return result

        for i in range(_K_SAMPLES):
            try:
                tool_name = self._sample_tool_call(sample_llm)
                sampled_tools.append(tool_name)
            except Exception as exc:
                logger.debug("Semantic entropy sample %d failed: %s", i, exc)
                sampled_tools.append("__error__")

        # Compute agreement: what fraction of samples match the proposed tool?
        if sampled_tools:
            matching = sum(1 for t in sampled_tools if t == self.proposed_tool)
            agreement = matching / len(sampled_tools)
        else:
            agreement = 1.0  # No samples collected — allow.

        blocked = agreement < _MIN_AGREEMENT_RATIO

        result = SemanticEntropyResult(
            proposed_tool=self.proposed_tool,
            sampled_tools=sampled_tools,
            agreement_ratio=round(agreement, 3),
            blocked=blocked,
        )

        _record_result(result)

        logger.info(
            "Semantic entropy: tool=%s samples=%s agreement=%.2f blocked=%s",
            self.proposed_tool, sampled_tools, agreement, blocked,
        )

        return result

    def _build_sample_llm(self):
        """Create a copy of the LLM with elevated temperature for sampling.

        Uses bind() to override kwargs where possible; falls back to a
        fresh build_llm() call with the same provider/model.
        """
        # Try the LangChain bind approach first (works for ChatOpenAI, etc.)
        if hasattr(self.llm, "bind"):
            try:
                return self.llm.bind(temperature=_SAMPLE_TEMPERATURE)
            except Exception:
                pass

        # Fallback: build a new LLM instance with sampling temperature.
        from prax.agent.llm_factory import build_llm

        model_name = getattr(self.llm, "model_name", None) or getattr(self.llm, "model", None)
        provider = _detect_provider(self.llm)

        return build_llm(
            provider=provider,
            model=model_name,
            temperature=_SAMPLE_TEMPERATURE,
        )

    def _sample_tool_call(self, sample_llm) -> str:
        """Invoke the LLM once and extract the proposed tool name."""
        response = sample_llm.invoke(self.messages)

        # Extract tool call from the response.
        tool_calls = getattr(response, "tool_calls", None) or []
        if tool_calls:
            return tool_calls[0].get("name", "__no_name__")

        # Some providers put tool calls in additional_kwargs.
        additional = getattr(response, "additional_kwargs", {}) or {}
        tc = additional.get("tool_calls", [])
        if tc:
            func = tc[0].get("function", {})
            return func.get("name", "__no_name__")

        return "__no_tool__"


def _detect_provider(llm) -> str:
    """Best-effort detection of the provider from an LLM instance."""
    cls_name = type(llm).__name__.lower()
    if "openai" in cls_name:
        return "openai"
    if "anthropic" in cls_name:
        return "anthropic"
    if "vertex" in cls_name or "google" in cls_name:
        return "google"
    if "ollama" in cls_name:
        return "ollama"
    return "openai"  # Safe default.


# ---------------------------------------------------------------------------
# Public API — called from governed_tool.py
# ---------------------------------------------------------------------------


def check_semantic_entropy(
    tool_name: str,
    kwargs: dict,
    *,
    llm=None,
    messages: list | None = None,
) -> str | None:
    """Check semantic entropy for a HIGH-risk tool call.

    Returns ``None`` if the gate is disabled, the check passes, or on
    failure (graceful fallback).  Returns a warning string if the check
    detects divergence and the tool call should be blocked.

    Parameters
    ----------
    tool_name : str
        The name of the tool being called.
    kwargs : dict
        The tool call arguments (for logging only).
    llm : optional
        LLM instance to use for re-queries.  If not provided, attempts
        to retrieve it from the current orchestrator context.
    messages : list, optional
        Conversation messages for re-query context.  If not provided,
        the check returns None (cannot perform divergence analysis).
    """
    # Gate check: only active when explicitly enabled.
    gate_value = os.environ.get("ACTIVE_INFERENCE_SEMANTIC_GATE", "").lower()
    if gate_value not in ("1", "true"):
        return None

    # If no LLM or messages provided, try to get them from context.
    if llm is None:
        try:
            from prax.agent.semantic_entropy_context import get_semantic_context
            ctx = get_semantic_context()
            if ctx:
                llm = ctx.get("llm")
                messages = messages or ctx.get("messages")
        except Exception:
            pass

    # Cannot run the check without an LLM and messages.
    if llm is None or not messages:
        logger.debug(
            "Semantic entropy gate: skipped for %s (no LLM/messages available)",
            tool_name,
        )
        return None

    try:
        gate = SemanticEntropyGate(llm, messages, tool_name)
        result = gate.check()

        if result.blocked:
            return (
                f"Semantic entropy BLOCKED {tool_name}: "
                f"LLM tool-call divergence detected. "
                f"Proposed={tool_name}, samples={result.sampled_tools}, "
                f"agreement={result.agreement_ratio:.0%}. "
                f"Switch to read-only tools to verify assumptions before "
                f"retrying this action."
            )

        return None

    except Exception as exc:
        logger.debug(
            "Semantic entropy gate failed for %s: %s", tool_name, exc,
        )
        return None  # Graceful fallback — don't block on gate failure.
