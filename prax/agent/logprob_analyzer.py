"""Phase 3 — Conditional logprob entropy analysis (OpenAI only).

When the LLM provider exposes token-level log-probabilities, this
module computes entropy on tool-call argument tokens.  High entropy
on critical tokens (file paths, command strings) indicates the model
is distributing probability mass across multiple alternatives — i.e.
it is *guessing* — even when the sampled output reads as confident.

This is a **conditional enhancement**: active only when the provider
supports ``logprobs`` (currently OpenAI).  When unavailable, the
system falls back to Phase 1 prediction error, which works universally.

See Research §17 in README — "Active Inference, Extrinsic Uncertainty
Measurement, and the Harness as Markov Blanket".
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

# Logprob below this threshold flags a token as "uncertain".
# -2.0 ≈ probability < 13.5%  (e^-2 ≈ 0.135)
_LOW_CONFIDENCE_THRESHOLD = -2.0

# Entropy score above this triggers a warning in the prediction tracker.
_HIGH_ENTROPY_THRESHOLD = 0.4


@dataclass
class ToolCallEntropy:
    """Entropy measurement for a single tool call's arguments."""

    tool_name: str
    mean_logprob: float    # Average logprob across argument tokens.
    min_logprob: float     # Worst-case token logprob.
    entropy_score: float   # Normalized: 0 = certain, 1 = uniform.
    high_entropy_tokens: list[str] = field(default_factory=list)

    @property
    def is_uncertain(self) -> bool:
        return self.entropy_score >= _HIGH_ENTROPY_THRESHOLD


# ---------------------------------------------------------------------------
# Module-level buffer — written by the callback, read by governed_tool
# ---------------------------------------------------------------------------

_entropy_buffer: list[ToolCallEntropy] = []
_entropy_lock = threading.Lock()


def drain_entropy_buffer() -> list[ToolCallEntropy]:
    """Return and clear all buffered entropy measurements."""
    with _entropy_lock:
        entries = list(_entropy_buffer)
        _entropy_buffer.clear()
    return entries


def get_entropy_for_tool(tool_name: str) -> ToolCallEntropy | None:
    """Look up the most recent entropy data for *tool_name*."""
    with _entropy_lock:
        for entry in reversed(_entropy_buffer):
            if entry.tool_name == tool_name:
                return entry
    return None


# ---------------------------------------------------------------------------
# LangChain callback handler
# ---------------------------------------------------------------------------


class LogprobCallbackHandler(BaseCallbackHandler):
    """Extracts logprob data from OpenAI LLM responses.

    Attach this callback when building an OpenAI LLM instance.
    It silently no-ops for providers that don't return logprobs.
    """

    def on_llm_end(self, response, **kwargs) -> None:  # type: ignore[override]
        try:
            self._process(response)
        except Exception:
            logger.debug("Logprob analysis failed", exc_info=True)

    # ── internals ─────────────────────────────────────────────────────

    def _process(self, response) -> None:
        if not hasattr(response, "generations") or not response.generations:
            return

        for generation_list in response.generations:
            for generation in generation_list:
                logprobs_data = self._extract_logprobs(generation)
                if not logprobs_data:
                    continue

                content_logprobs = logprobs_data.get("content") or []
                if not content_logprobs:
                    continue

                msg = getattr(generation, "message", None)
                tool_calls = getattr(msg, "tool_calls", []) if msg else []
                if not tool_calls:
                    continue

                logprob_values = [
                    t["logprob"]
                    for t in content_logprobs
                    if isinstance(t, dict) and "logprob" in t
                ]
                if not logprob_values:
                    continue

                mean_lp = sum(logprob_values) / len(logprob_values)
                min_lp = min(logprob_values)

                high_entropy_tokens = [
                    t.get("token", "?")
                    for t in content_logprobs
                    if isinstance(t, dict)
                    and t.get("logprob", 0.0) < _LOW_CONFIDENCE_THRESHOLD
                ]

                # Normalize: logprob 0 → score 0, logprob -5 → score 1.
                entropy_score = min(1.0, max(0.0, -mean_lp / 5.0))

                for tc in tool_calls:
                    entry = ToolCallEntropy(
                        tool_name=tc.get("name", "unknown"),
                        mean_logprob=round(mean_lp, 4),
                        min_logprob=round(min_lp, 4),
                        entropy_score=round(entropy_score, 4),
                        high_entropy_tokens=high_entropy_tokens[:10],
                    )
                    with _entropy_lock:
                        _entropy_buffer.append(entry)

                    logger.info(
                        "Logprob: tool=%s mean=%.3f min=%.3f entropy=%.3f "
                        "uncertain_tokens=%d",
                        entry.tool_name, mean_lp, min_lp,
                        entropy_score, len(high_entropy_tokens),
                    )

    @staticmethod
    def _extract_logprobs(generation) -> dict | None:
        """Try multiple locations where logprob data may live."""
        # Location 1: generation_info (older LangChain)
        info = getattr(generation, "generation_info", {}) or {}
        lp = info.get("logprobs")
        if lp:
            return lp

        # Location 2: message.response_metadata (newer LangChain)
        msg = getattr(generation, "message", None)
        if msg:
            meta = getattr(msg, "response_metadata", {}) or {}
            lp = meta.get("logprobs")
            if lp:
                return lp

        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_handler: LogprobCallbackHandler | None = None


def get_logprob_callback() -> LogprobCallbackHandler:
    """Return a singleton callback handler instance."""
    global _handler
    if _handler is None:
        _handler = LogprobCallbackHandler()
    return _handler
