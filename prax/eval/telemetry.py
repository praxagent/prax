"""Real token + latency telemetry for eval runs.

The GAIA runner historically *estimated* token usage as ``len(response) // 4``
with hardcoded Opus USD rates — useless for ranking runs and meaningless for a
local model (where USD ≈ 0 and the real axes are **tokens** and **wall-time**).

This module captures the *actual* usage of every LLM call inside an eval run by
patching the one chokepoint every provider flows through:
``prax.observability.callbacks.get_otel_callbacks`` is called fresh by
``build_llm`` on every construction (see ``prax/agent/llm_factory.py``), so
appending a collector there instruments the whole orchestrator — sub-agents,
judges, retries — without touching a single call site.

Usage::

    from prax.eval.telemetry import collect_usage

    with collect_usage() as usage:
        response = agent.run(...)
    snap = usage.snapshot()   # {prompt_tokens, completion_tokens, llm_calls, ...}

Everything degrades gracefully: if the callback hook can't be patched (lite
deployments, refactors), the collector simply reports zeros instead of raising.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

@dataclass
class UsageCollector:
    """Thread-safe accumulator for token usage across an eval run.

    A single instance is shared by every LLM built during the run (the
    orchestrator runs spokes in threads, so all mutation takes the lock).
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    tools: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, prompt: int, completion: int) -> None:
        with self._lock:
            self.prompt_tokens += int(prompt or 0)
            self.completion_tokens += int(completion or 0)
            self.llm_calls += 1

    def add_tool(self, name: str) -> None:
        if not name:
            return
        with self._lock:
            self.tools.append(str(name))

    def spokes(self) -> list[str]:
        """Spokes routed to, derived from ``delegate_<spoke>`` tool calls.

        The orchestrator reaches every sub-agent through a ``delegate_*`` tool,
        so the captured tool stream IS the routing trace — no separate plumbing.
        """
        seen: list[str] = []
        for t in self.tools:
            if t.startswith("delegate_"):
                spoke = t[len("delegate_"):]
                if spoke not in seen:
                    seen.append(spoke)
        return seen

    def snapshot(self) -> dict:
        with self._lock:
            total = self.prompt_tokens + self.completion_tokens
            return {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": total,
                "llm_calls": self.llm_calls,
                "tools": list(self.tools),
                "spokes": self.spokes(),
                "wall_time_s": round(time.monotonic() - self.started_at, 2),
            }


def _extract_usage(response) -> tuple[int, int]:
    """Pull (prompt, completion) token counts from a LangChain ``LLMResult``.

    Tolerates the two shapes in the wild: the OpenAI-style ``llm_output``
    ``token_usage`` block, and the provider-agnostic ``usage_metadata`` carried
    on each generation's message (newer LangChain, what local/vLLM/Ollama emit).
    Returns ``(0, 0)`` if neither is present.
    """
    # Shape 1: llm_output.token_usage (ChatOpenAI / vLLM-via-OpenAI / ds4)
    llm_output = getattr(response, "llm_output", None) or {}
    tu = llm_output.get("token_usage") or llm_output.get("usage") or {}
    prompt = tu.get("prompt_tokens") or tu.get("input_tokens") or 0
    completion = tu.get("completion_tokens") or tu.get("output_tokens") or 0
    if prompt or completion:
        return int(prompt), int(completion)

    # Shape 2: per-generation usage_metadata on the AIMessage (ChatOpenAI, most).
    prompt = completion = 0
    for batch in getattr(response, "generations", None) or []:
        for gen in batch:
            msg = getattr(gen, "message", None)
            um = getattr(msg, "usage_metadata", None) or {}
            prompt += int(um.get("input_tokens", 0) or 0)
            completion += int(um.get("output_tokens", 0) or 0)
    if prompt or completion:
        return prompt, completion

    # Shape 3: community ChatOllama emits counts ONLY in generation_info
    # (prompt_eval_count / eval_count) — no llm_output, no usage_metadata.
    # Without this an entire Ollama run would silently record zero tokens.
    prompt = completion = 0
    for batch in getattr(response, "generations", None) or []:
        for gen in batch:
            gi = getattr(gen, "generation_info", None) or {}
            prompt += int(gi.get("prompt_eval_count", 0) or 0)
            completion += int(gi.get("eval_count", 0) or 0)
    return prompt, completion


def _make_handler(collector: UsageCollector):
    """Build a LangChain callback handler that feeds *collector* on each call.

    Imported lazily and behind a guard so a missing/changed langchain API never
    breaks an eval — we just lose token accounting, not the run.
    """
    try:
        from langchain_core.callbacks import BaseCallbackHandler
    except Exception:  # pragma: no cover - langchain always present in prax
        return None

    class _UsageHandler(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):  # noqa: D401
            try:
                prompt, completion = _extract_usage(response)
                collector.add(prompt, completion)
            except Exception:
                logger.debug("usage extraction failed", exc_info=True)

        def on_tool_start(self, serialized, input_str, **kwargs):  # noqa: D401
            try:
                name = (serialized or {}).get("name") or kwargs.get("name") or ""
                collector.add_tool(name)
            except Exception:
                logger.debug("tool capture failed", exc_info=True)

    return _UsageHandler()


# ---------------------------------------------------------------------------
# Context manager — instrument every LLM built during the block
# ---------------------------------------------------------------------------

# Reference-counted patch state so nested/concurrent collect_usage() contexts
# don't clobber each other's restore (which previously leaked a dead patch and
# cross-contaminated attribution under concurrency>1).
_patch_lock = threading.Lock()
_active_handlers: list = []
_true_original = None


@contextlib.contextmanager
def collect_usage():
    """Capture real token usage for every LLM call made inside the block.

    Patches ``get_otel_callbacks`` so the collector rides along on every
    ``build_llm`` construction.  Yields the :class:`UsageCollector`; read
    ``.snapshot()`` after the block.  A no-op (still yields a zeroed collector)
    if the hook can't be patched.

    The patch is **reference-counted**: the true original is saved once on first
    entry and restored only when the last active context exits, so overlapping
    contexts never leave a dangling patch.  Note that with multiple contexts
    open at once every collector receives every call — for clean per-task
    attribution keep the live-orchestrator suites at concurrency 1 (they do).
    """
    global _true_original
    collector = UsageCollector()
    handler = _make_handler(collector)
    if handler is None:
        yield collector
        return

    try:
        from prax.observability import callbacks as cb_mod
    except Exception:
        # No observability layer (lite build) — nothing to patch.
        yield collector
        return

    installed = False
    with _patch_lock:
        orig = getattr(cb_mod, "get_otel_callbacks", None)
        if callable(orig):
            if not _active_handlers:
                _true_original = orig

                def _patched(*args, **kwargs):
                    base = []
                    if callable(_true_original):
                        try:
                            base = list(_true_original(*args, **kwargs) or [])
                        except Exception:
                            base = []
                    with _patch_lock:
                        extra = list(_active_handlers)
                    return base + extra

                cb_mod.get_otel_callbacks = _patched
            _active_handlers.append(handler)
            installed = True

    try:
        yield collector
    finally:
        if installed:
            with _patch_lock:
                try:
                    _active_handlers.remove(handler)
                except ValueError:
                    pass
                if not _active_handlers and _true_original is not None:
                    cb_mod.get_otel_callbacks = _true_original
                    _true_original = None
