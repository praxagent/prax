"""Prax agent for the official Terminal-Bench 2.0 harness (harbor).

Terminal-Bench 2.0 runs through **harbor** (github.com/laude-institute/harbor):
the harness builds each task's container, hands the agent the task instruction
plus an ``environment.exec`` handle into that container, then runs the task's
own hidden verifier. This module is the Prax side of that contract:

- ``run_terminal_task`` is the harness-independent core: **Prax's own agent
  loop** (``build_agent_loop`` — so the middleware stack, model routing, and
  the keyless proxy path are all the production ones) driving a terminal
  through a caller-supplied ``execute`` callable.
- ``PraxAgent`` (built lazily via PEP-562 ``__getattr__`` so prax never
  imports harbor at module load; harbor is not a prax dependency) adapts that
  core to harbor's ``BaseAgent`` interface:
  ``harbor run --agent-import-path prax.eval.tb_agent:PraxAgent ...``.
  Setup + run instructions: ``docs/guides/terminal-bench.md``.

What this deliberately is NOT: the 97-tool orchestrator. A benchmark task
container is not the user's workspace — the spokes' tools (library, memory,
browser, Prax's own sandbox) point at surfaces that don't exist there. What
runs is Prax's loop, middleware, and model plumbing with a terminal tool
bound to the task container — and every result must be labeled exactly that
way. The tools here are eval-internal and skip ``governed_tool`` on purpose:
governance wraps tools that act on a *user's* resources with a user identity
attached; a disposable benchmark container has neither.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from langchain_core.tools import tool

# The single sanctioned loop seam (layer rule 4).
from prax.agent.agent_loop import GraphRecursionError, build_agent_loop

__all__ = ["run_terminal_task", "PraxAgent"]  # noqa: F822 — PraxAgent is PEP-562 lazy

# Bound tool output so one verbose command cannot flood the context window.
# Head+tail beats head-only: exit summaries and tracebacks live at the tail.
_MAX_TOOL_OUTPUT = 8_000

# A general terminal-work discipline. Deliberately benchmark-agnostic (no
# task shapes, no answer formats): spiking a benchmark via the prompt is
# forbidden here — see the prime directive in CLAUDE.md.
_SYSTEM_PROMPT = """You are an autonomous agent completing a task in a Linux \
terminal. You cannot ask questions — finish the task with the tools provided.

Discipline:
- Inspect before acting: look at the files, directories, and services the \
task mentions before changing anything.
- Commands run non-interactively: never start editors, pagers, or REPLs; use \
flags like -y and pipe input instead of typing at prompts.
- Prefer small verifiable steps over one large script; check each step's \
output before building on it.
- If a command fails, read the error and fix the cause — do not repeat the \
same command hoping for a different result.
- Before declaring the task done, verify the required end state yourself \
(run the binary, re-read the file, query the service).
- Call task_done exactly once, when the end state is verified."""


def _bound(text: str | None) -> str:
    text = text or ""
    if len(text) <= _MAX_TOOL_OUTPUT:
        return text
    half = _MAX_TOOL_OUTPUT // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n... [{omitted} chars omitted] ...\n{text[-half:]}"


def _make_tools(execute: Callable[..., dict], state: dict) -> list:
    """Build the two terminal tools around a caller-supplied ``execute``.

    ``execute(command, timeout_sec)`` must return a dict with ``stdout``,
    ``stderr`` and ``return_code`` (harbor's ExecResult shape).
    """

    @tool
    def terminal(command: str, timeout_sec: int = 180) -> str:
        """Run a shell command in the task environment and return its output.

        Args:
            command: The shell command to run (non-interactive).
            timeout_sec: Kill the command after this many seconds.
        """
        state["steps"] += 1
        try:
            result = execute(command, timeout_sec=timeout_sec)
        except Exception as exc:  # surface, never crash the loop
            return f"[execution error: {type(exc).__name__}: {exc}]"
        out = _bound(result.get("stdout"))
        err = _bound(result.get("stderr"))
        rc = result.get("return_code")
        reply = f"exit_code: {rc}\nstdout:\n{out}"
        if err:
            reply += f"\nstderr:\n{err}"
        return reply

    @tool
    def task_done(summary: str) -> str:
        """Declare the task complete. Call exactly once, AFTER verifying the
        required end state yourself.

        Args:
            summary: One or two sentences: what was done and how it was verified.
        """
        state["done"] = True
        state["summary"] = summary
        return "Recorded. Stop now."

    return [terminal, task_done]


def run_terminal_task(
    instruction: str,
    execute: Callable[..., dict],
    *,
    tier: str = "low",
    model: str | None = None,
    provider: str | None = None,
    max_steps: int = 40,
) -> dict:
    """Run one terminal task through Prax's agent loop.

    Returns a result dict: done/steps/summary plus token+cost accounting
    (``cost_usd`` is ``None`` — unknown, not zero — when the model has no
    known rate, same rule as trace costing).
    """
    from prax.agent.llm_factory import build_llm

    llm = build_llm(provider=provider, model=model, tier=tier)
    state: dict = {"steps": 0, "done": False, "summary": None}
    graph = build_agent_loop(llm, _make_tools(execute, state))

    started = time.monotonic()
    error: str | None = None
    messages: list = []
    try:
        result = graph.invoke(
            {
                "messages": [
                    ("system", _SYSTEM_PROMPT),
                    ("human", f"Task:\n{instruction}"),
                ]
            },
            # Each agent step is (AI message, tool message); +2 for the
            # opening exchange. Wall-clock budgeting belongs to harbor's own
            # per-trial timeout; this bound stops runaway loops within it.
            config={"recursion_limit": max_steps * 2 + 2},
        )
        messages = result.get("messages", [])
    except GraphRecursionError:
        error = f"step budget exhausted ({max_steps} steps)"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    tokens_in = 0
    tokens_out = 0
    model_seen: str | None = None
    for m in messages:
        usage = getattr(m, "usage_metadata", None)
        if usage:
            tokens_in += usage.get("input_tokens", 0) or 0
            tokens_out += usage.get("output_tokens", 0) or 0
        meta = getattr(m, "response_metadata", None) or {}
        model_seen = meta.get("model_name") or model_seen

    from prax.eval.pricing import estimate_cost

    cost = estimate_cost(model_seen or model or "", tokens_in, tokens_out)
    return {
        "done": bool(state["done"]),
        "summary": state["summary"],
        "steps": state["steps"],
        "elapsed_s": round(time.monotonic() - started, 1),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": model_seen or model,
        "cost_usd": cost,
        "error": error,
        "n_messages": len(messages),
    }


def _build_harbor_agent_class() -> type:
    """Define the harbor ``BaseAgent`` subclass (harbor import happens here)."""
    import asyncio
    import os

    from harbor.agents.base import BaseAgent
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext

    class PraxAgent(BaseAgent):
        """Prax's loop + model plumbing driving the harbor task container."""

        @staticmethod
        def name() -> str:
            return "prax"

        def version(self) -> str:
            try:
                from importlib.metadata import version as _pkg_version

                return _pkg_version("prax")
            except Exception:
                return "0.0.0"

        async def setup(self, environment: BaseEnvironment) -> None:
            # The task container is used as-is; Prax runs OUTSIDE it and only
            # sends commands in, so there is nothing to install.
            return None

        async def run(
            self,
            instruction: str,
            environment: BaseEnvironment,
            context: AgentContext,
        ) -> None:
            loop = asyncio.get_running_loop()

            def execute(command: str, timeout_sec: int = 180) -> dict:
                # Called from the worker thread running the sync agent loop —
                # bridge back onto the event loop that owns the environment.
                future = asyncio.run_coroutine_threadsafe(
                    environment.exec(command=command, timeout_sec=timeout_sec),
                    loop,
                )
                res = future.result(timeout=timeout_sec + 60)
                return {
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                    "return_code": res.return_code,
                }

            result = await loop.run_in_executor(
                None,
                lambda: run_terminal_task(
                    instruction,
                    execute,
                    model=self.model_name or os.environ.get("PRAX_TB_MODEL"),
                    provider=os.environ.get("PRAX_TB_PROVIDER"),
                    tier=os.environ.get("PRAX_TB_TIER", "low"),
                    max_steps=int(os.environ.get("PRAX_TB_MAX_STEPS", "40")),
                ),
            )

            context.n_input_tokens = result["tokens_in"]
            context.n_output_tokens = result["tokens_out"]
            context.cost_usd = result["cost_usd"]
            context.metadata = {
                # The honest label: what actually ran (see module docstring).
                "harness": "prax build_agent_loop + terminal tool",
                "steps": result["steps"],
                "done": result["done"],
                "summary": result["summary"],
                "error": result["error"],
                "model": result["model"],
                "elapsed_s": result["elapsed_s"],
            }

    return PraxAgent


def __getattr__(name: str):  # PEP 562 — lazy so prax never requires harbor
    if name == "PraxAgent":
        cls = _build_harbor_agent_class()
        globals()["PraxAgent"] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
