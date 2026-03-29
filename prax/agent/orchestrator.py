"""Conversation agent built on LangGraph."""
from __future__ import annotations

import logging
from collections.abc import Iterable

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from prax.agent.checkpoint import CheckpointManager
from prax.agent.llm_factory import build_llm
from prax.agent.tool_registry import get_registered_tools
from prax.agent.user_context import current_user_id
from prax.plugins.prompt_manager import get_prompt_manager
from prax.services.workspace_service import append_trace, read_plan, save_instructions
from prax.settings import settings
from prax.trace_events import TraceEvent

# Maximum number of continuation rounds when a plan has incomplete steps.
_MAX_PLAN_CONTINUATIONS = 3

# Keywords/patterns that suggest a request is complex enough to benefit from
# a plan.  Checked case-insensitively against the user input.
_COMPLEXITY_SIGNALS = [
    "deep dive", "deep-dive", "create a note", "make a note", "write a note",
    "create a course", "make a course", "teach me",
    "compare", "summarize and", "research",
    "step by step", "step-by-step",
    "build me", "set up", "configure",
    "analyze", "investigate",
]

logger = logging.getLogger(__name__)

# Hardcoded fallback — used only if the prompt file is missing.
_FALLBACK_PROMPT = (
    "You are a warm, capable AI assistant. "
    "Hold casual conversations, answer questions accurately, and call tools when needed."
)


def _load_system_prompt() -> str:
    """Load the system prompt from the plugin prompts directory."""
    from prax.agent.model_tiers import tier_for_system_prompt

    mgr = get_prompt_manager()
    runtime_env = "Docker (persistent sandbox)" if settings.running_in_docker else "local"
    if settings.sandbox_persistent:
        sandbox_guidance = (
            "- The sandbox is always running — no need to start it. You can install "
            "system packages with sandbox_install (e.g. sandbox_install('poppler-utils')). "
            "Installed packages persist until the container restarts. For permanent "
            "additions, update the sandbox Dockerfile and rebuild.\n"
            "- Plugin tools that need system packages (pdflatex, ffmpeg, pdftoppm, etc.) "
            "automatically route commands to the sandbox container. Just call the plugin "
            "tool normally — it works in both local and Docker mode."
        )
    else:
        sandbox_guidance = (
            "- You are running locally. The sandbox creates ephemeral Docker containers "
            "(Docker Desktop required). Plugin tools run commands on the host directly, "
            "so the user needs system packages installed locally (e.g. 'brew install "
            "poppler ffmpeg'). If a plugin tool reports missing deps, guide the user "
            "to install them."
        )
    prompt = mgr.load("system_prompt.md", {
        "AGENT_NAME": settings.agent_name,
        "RUNTIME_ENV": runtime_env,
        "SANDBOX_GUIDANCE": sandbox_guidance,
        "MODEL_TIERS": tier_for_system_prompt(),
    })
    return prompt or _FALLBACK_PROMPT


class ConversationAgent:
    """High-level orchestrator for conversational replies using tools."""

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        tier: str | None = None,
    ) -> None:
        from prax.plugins.llm_config import get_component_config
        cfg = get_component_config("orchestrator")
        self.llm = build_llm(
            provider=provider or cfg.get("provider"),
            model=model or cfg.get("model"),
            temperature=temperature if temperature is not None else cfg.get("temperature"),
            tier=tier or cfg.get("tier") or "low",
        )
        self.checkpoint_mgr = CheckpointManager()
        self.tools = get_registered_tools()
        self.graph = create_react_agent(
            self.llm, self.tools, checkpointer=self.checkpoint_mgr.saver,
        )
        self._plugin_version: int = self._current_plugin_version()

    @staticmethod
    def _current_plugin_version() -> int:
        from prax.plugins.loader import get_plugin_loader
        return get_plugin_loader().version

    def _register_workspace_plugins(self, user_id: str) -> None:
        """Tell the plugin loader about this user's workspace plugins directory."""
        try:
            from prax.plugins.loader import get_plugin_loader
            from prax.services.workspace_service import get_workspace_plugins_dir
            plugins_dir = get_workspace_plugins_dir(user_id)
            if plugins_dir:
                logger.info("Registering workspace plugins from %s", plugins_dir)
                loader = get_plugin_loader()
                loader.add_workspace_plugins_dir(plugins_dir)
                loader.load_all()
        except Exception:
            logger.warning("Could not register workspace plugins for %s", user_id, exc_info=True)

    def _rebuild_if_needed(self) -> None:
        """Rebuild the agent graph if plugins have been hot-swapped."""
        v = self._current_plugin_version()
        if v != self._plugin_version:
            logger.info("Plugin version changed (%d -> %d), rebuilding agent graph", self._plugin_version, v)
            self.tools = get_registered_tools()
            self.graph = create_react_agent(
                self.llm, self.tools, checkpointer=self.checkpoint_mgr.saver,
            )
            self._plugin_version = v

    @staticmethod
    def _classify_complexity(user_input: str) -> bool:
        """Return True if the user input looks complex enough to warrant a plan.

        Uses a simple keyword heuristic — fast and deterministic.  The system
        prompt tells the agent to plan when it sees ≥2 tool calls ahead, so
        this is a belt-and-suspenders nudge, not the sole trigger.
        """
        lower = user_input.lower()
        return any(signal in lower for signal in _COMPLEXITY_SIGNALS)

    @staticmethod
    def _has_incomplete_plan(uid: str | None) -> bool:
        """Check whether the user has an active plan with incomplete steps."""
        if not uid:
            return False
        try:
            plan = read_plan(uid)
            if not plan:
                return False
            steps = plan.get("steps", [])
            return any(not s.get("done") for s in steps)
        except Exception:
            return False

    @staticmethod
    def _plan_status_summary(uid: str) -> str:
        """Return a short summary of incomplete plan steps for continuation."""
        try:
            plan = read_plan(uid)
            if not plan:
                return ""
            incomplete = [
                s for s in plan.get("steps", []) if not s.get("done")
            ]
            if not incomplete:
                return ""
            step_list = ", ".join(
                f"step {s['step']}: {s.get('description', '')[:60]}"
                for s in incomplete[:5]
            )
            return (
                f"Your plan for \"{plan.get('goal', '')}\" has {len(incomplete)} "
                f"incomplete step(s): {step_list}. "
                "If you already completed any of these steps (via delegation, "
                "tool calls, or direct work), call agent_step_done(step_number) "
                "for each one NOW. Do NOT re-delegate or repeat work that "
                "already returned results. If all steps are actually done, "
                "call agent_plan_clear() and respond to the user."
            )
        except Exception:
            return ""

    # Tools whose successful return means substantive work was done.
    _DELEGATION_TOOLS = frozenset({
        "delegate_task", "delegate_parallel", "delegate_research",
        "delegate_browser", "delegate_sandbox", "delegate_sysadmin",
        "delegate_finetune", "delegate_content_editor", "delegate_knowledge",
        "workspace_save", "workspace_patch", "note_create", "note_update",
    })

    @staticmethod
    def _auto_complete_plan_steps(uid: str, messages: list) -> None:
        """Auto-mark plan steps done when delegation/work tools returned successfully.

        Scans messages for completed delegation tool calls. If the plan has
        incomplete steps and work was clearly done (delegation returned, files
        saved), marks ALL incomplete steps as done. This prevents the plan
        enforcement loop from re-delegating work that already completed.
        """
        plan = read_plan(uid)
        if not plan:
            return

        incomplete_steps = [s for s in plan.get("steps", []) if not s.get("done")]
        if not incomplete_steps:
            return

        # Check if delegation/work tools were called AND returned non-error results
        from prax.services.workspace_service import complete_plan_step

        work_done = False
        for msg in messages:
            if isinstance(msg, ToolMessage) and msg.name in ConversationAgent._DELEGATION_TOOLS:
                content = (msg.content or "").lower()
                # Only count as done if the result doesn't look like an error
                if not any(err in content for err in ("failed", "error:", "sub-agent failed")):
                    work_done = True
                    break

        if not work_done:
            return

        # Auto-mark all incomplete steps as done — the work was completed
        # but the agent forgot to call agent_step_done().
        for step in incomplete_steps:
            try:
                complete_plan_step(uid, step["step"])
                logger.info(
                    "Auto-marked plan step %d done (agent forgot agent_step_done): %s",
                    step["step"], step.get("description", "")[:60],
                )
            except Exception:
                pass

    def run(self, conversation: Iterable[BaseMessage], user_input: str, workspace_context: str = "") -> str:
        """Execute the agent graph and return the final string response."""
        import time as _time

        from prax.agent.trace import start_span

        _run_deadline = _time.monotonic() + settings.agent_run_timeout

        # Start a root span that wraps the entire orchestrator invocation.
        # This sets last_root_trace_id so callers can attach it to responses.
        root_span = start_span("orchestrator", "orchestrator")

        # Reset per-plugin call counters for the new message.
        from prax.plugins.monitored_tool import reset_plugin_call_counts
        reset_plugin_call_counts()

        # Register workspace plugins for the current user.
        uid = current_user_id.get()
        if uid:
            self._register_workspace_plugins(uid)

        # Rebuild graph if plugins changed since last invocation.
        self._rebuild_if_needed()

        history: list[BaseMessage] = list(conversation)
        logger.debug("Agent invoked with %d history messages", len(history))

        # Complexity hint: if the request looks complex and there's no active
        # plan, nudge the agent to create one.
        complexity_hint = ""
        if uid and self._classify_complexity(user_input) and not read_plan(uid):
            from prax.services.teamwork_hooks import set_role_status
            set_role_status("Planner", "working")
            complexity_hint = (
                "\n\n[SYSTEM HINT: This request looks like it will require "
                "multiple steps. Create an agent_plan BEFORE doing any work.]"
            )

        full_prompt = _load_system_prompt() + workspace_context + complexity_hint

        # Persist instructions so the agent can re-read them mid-conversation.
        if uid:
            try:
                save_instructions(uid, full_prompt)
            except Exception:
                pass  # Best-effort — don't block the conversation.

        # Build the full message list for the graph.
        messages: list[BaseMessage] = (
            [SystemMessage(content=full_prompt)]
            + history
            + [HumanMessage(content=user_input)]
        )

        # Start a checkpointed turn for this user.
        turn = self.checkpoint_mgr.start_turn(uid or "anonymous")
        config = {
            **self.checkpoint_mgr.graph_config(turn),
            "recursion_limit": settings.agent_max_tool_calls,
        }

        try:
            result = self._invoke_with_retry(messages, config, turn.user_id)

            # Plan enforcement: if the agent responded but has an incomplete
            # plan, push it back into the loop to finish the work.
            continuations = 0
            while (
                uid
                and self._has_incomplete_plan(uid)
                and continuations < _MAX_PLAN_CONTINUATIONS
                and _time.monotonic() < _run_deadline
            ):
                # Auto-mark plan steps done if substantial work was completed
                # (delegation tools returned results) but steps weren't marked.
                self._auto_complete_plan_steps(uid, result.get("messages", []))
                if not self._has_incomplete_plan(uid):
                    break

                continuations += 1
                nudge = self._plan_status_summary(uid)
                logger.info(
                    "Plan enforcement: continuation %d/%d (user=%s)",
                    continuations, _MAX_PLAN_CONTINUATIONS, uid,
                )

                # Inject a system nudge as a new human message to continue.
                continuation_messages = result.get("messages", []) + [
                    HumanMessage(content=f"[SYSTEM] {nudge}"),
                ]
                result = self.graph.invoke(
                    {"messages": continuation_messages}, config=config,
                )

            if _time.monotonic() >= _run_deadline:
                logger.warning(
                    "Agent run hit wall-clock timeout (%ds) for user %s",
                    settings.agent_run_timeout, uid,
                )

            self._rebuild_if_needed()
        finally:
            self.checkpoint_mgr.end_turn(turn.user_id)
            # Shut down idle plugin subprocesses after each turn.
            try:
                from prax.plugins.bridge import shutdown_all_bridges
                shutdown_all_bridges()
            except Exception:
                pass

        # Reset all TeamWork role agents to idle now that the turn is done.
        from prax.services.teamwork_hooks import reset_all_idle
        reset_all_idle()

        # Write full agent trace to the user's workspace log.
        self._write_trace(uid, user_input, result.get("messages", []))

        # Extract the last AI message from the graph output.
        response = ""
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                response = msg.content
                break

        # Deterministic claim audit: check for ungrounded numeric claims.
        if response:
            response = self._audit_claims(response, result.get("messages", []), uid)

        root_span.end(status="completed", summary=response[:200] if response else "")
        return response

    @staticmethod
    def _is_invalid_checkpoint_error(exc: Exception) -> bool:
        """Detect errors caused by resuming from a checkpoint with dangling tool_calls.

        When a tool raises an exception (vs returning an error string), the
        checkpoint has an assistant message with tool_calls but no matching
        ToolMessage responses.  OpenAI rejects this with a 400 about
        "tool_call_ids" not having response messages.
        """
        msg = str(exc).lower()
        return "tool_call_id" in msg or "tool_calls" in msg and "400" in msg

    def _invoke_with_retry(
        self,
        messages: list[BaseMessage],
        config: dict,
        user_id: str,
    ) -> dict:
        """Invoke the agent graph, retrying from the last checkpoint on failure.

        Before each retry, the graph is rebuilt if plugins changed mid-turn
        (e.g. the agent activated a fix plugin during the failed attempt).
        This ensures the retry uses the freshly activated tool implementation
        rather than the stale one that was bound at the start of the turn.

        If a rollback lands on an invalid checkpoint state (dangling tool_calls
        without ToolMessage responses), we fall back to a fresh start instead
        of consuming another retry attempt.
        """
        fresh_restarts = 0  # Guard against infinite fresh-start loops.

        while True:
            try:
                return self.graph.invoke({"messages": messages}, config=config)
            except Exception as exc:
                logger.warning(
                    "Agent graph failed (user=%s): %s", user_id, exc,
                )

                # If this error is from an invalid checkpoint state (dangling
                # tool_calls), don't count it as a retry — just start fresh.
                if self._is_invalid_checkpoint_error(exc) and fresh_restarts < 1:
                    fresh_restarts += 1
                    logger.info(
                        "Invalid checkpoint state detected (user=%s), restarting from scratch",
                        user_id,
                    )
                    turn = self.checkpoint_mgr.get_turn(user_id)
                    if turn is None:
                        raise
                    # Reset to a fresh thread so the bad checkpoint is abandoned.
                    import uuid
                    turn.thread_id = f"{user_id}:{uuid.uuid4().hex[:12]}"
                    config = {
                        **self.checkpoint_mgr.graph_config(turn),
                        "recursion_limit": settings.agent_max_tool_calls,
                    }
                    continue

                if not self.checkpoint_mgr.can_retry(user_id):
                    logger.error(
                        "No retries left for user %s, raising", user_id,
                    )
                    raise

                # Rebuild the graph if the agent activated a new plugin during
                # the failed attempt.  Without this, the retry would still use
                # the old tool implementation even though the fix was activated.
                self._rebuild_if_needed()

                self.checkpoint_mgr.record_retry(user_id)
                rollback_cfg = self.checkpoint_mgr.get_rollback_config(user_id)

                if rollback_cfg is None:
                    # Not enough checkpoints to roll back — re-run from scratch.
                    logger.info("No rollback target, retrying from scratch (user=%s)", user_id)
                    config = {
                        **self.checkpoint_mgr.graph_config(
                            self.checkpoint_mgr.get_turn(user_id),  # type: ignore[arg-type]
                        ),
                        "recursion_limit": settings.agent_max_tool_calls,
                    }
                    continue

                logger.info(
                    "Rolling back to checkpoint and retrying (user=%s, attempt=%d)",
                    user_id,
                    self.checkpoint_mgr.get_turn(user_id).retries_used,  # type: ignore[union-attr]
                )
                # Resume from the rollback checkpoint — LangGraph will continue
                # from the saved state, so we pass None for messages.
                config = rollback_cfg

    @staticmethod
    def _audit_claims(response: str, messages: list, user_id: str | None) -> str:
        """Run deterministic claim audit and log findings.

        If ungrounded claims are found, appends a trace audit entry.
        Does NOT block the response — the epistemic tags and system prompt
        are the primary defense.  This is a post-hoc detection layer for
        monitoring and debugging.
        """
        try:
            from prax.agent.claim_audit import audit_claims, format_audit_warning
            from prax.services.teamwork_hooks import post_to_channel, set_role_status

            set_role_status("Skeptic", "working")

            # Collect all tool results from this turn.
            tool_results: list[str] = []
            for msg in messages:
                if isinstance(msg, ToolMessage) and msg.content:
                    tool_results.append(msg.content)

            findings = audit_claims(response, tool_results)

            if findings:
                warning = format_audit_warning(findings)
                logger.warning("Claim audit flagged response (user=%s): %s", user_id, warning)
                post_to_channel("general", f"[Claim Audit] {warning}", agent_name="Skeptic")

                # Persist to trace as an audit event.
                if user_id:
                    try:
                        append_trace(user_id, [{
                            "type": TraceEvent.AUDIT,
                            "content": f"[CLAIM-AUDIT] {warning}",
                        }])
                    except Exception:
                        pass
        except Exception:
            logger.debug("Claim audit failed", exc_info=True)

        return response

    @staticmethod
    def _write_trace(user_id: str | None, user_input: str, messages: list) -> None:
        """Append the full agent invocation trace to the workspace log."""
        if not user_id:
            return

        # Flush the governance audit log into the trace.
        from prax.agent.governed_tool import drain_audit_log
        audit_entries = drain_audit_log()
        for entry in audit_entries:
            risk_tag = f"[{entry['risk'].upper()}]" if entry.get("risk") else ""
            logger.debug(
                "Audit: %s %s args=%s result=%s",
                entry.get("tool_name"), risk_tag,
                entry.get("args", ""), entry.get("result", ""),
            )

        entries: list[dict] = [{"type": TraceEvent.USER, "content": user_input}]
        for msg in messages:
            if isinstance(msg, SystemMessage):
                continue  # skip — already persisted as instructions.md
            if isinstance(msg, HumanMessage):
                continue  # already logged above as user_input
            if isinstance(msg, AIMessage):
                # Log tool calls if present.
                for tc in getattr(msg, "tool_calls", []) or []:
                    name = tc.get("name", "unknown")
                    args = tc.get("args", {})
                    entries.append({
                        "type": TraceEvent.TOOL_CALL,
                        "content": f"{name}({args})",
                    })
                if msg.content:
                    entries.append({"type": TraceEvent.ASSISTANT, "content": msg.content})
            elif isinstance(msg, ToolMessage):
                entries.append({
                    "type": TraceEvent.TOOL_RESULT,
                    "content": f"[{msg.name}] {msg.content}",
                })

        # Append governance audit entries to the trace.
        for audit in audit_entries:
            entries.append({
                "type": TraceEvent.AUDIT,
                "content": (
                    f"[{audit.get('risk', '?').upper()}] {audit.get('tool_name', '?')} "
                    f"args={audit.get('args', '')} result={audit.get('result', '')}"
                ),
            })

        # Flush tier choice log into the trace for A/B analysis.
        from prax.agent.llm_factory import drain_tier_choices
        tier_entries = drain_tier_choices()
        for tc in tier_entries:
            entries.append({
                "type": TraceEvent.TIER_CHOICE,
                "content": (
                    f"tier={tc.get('tier_requested', '?')} "
                    f"model={tc.get('model', '?')} "
                    f"provider={tc.get('provider', '?')} "
                    f"span={tc.get('span_name', '?')}"
                ),
            })

        try:
            append_trace(user_id, entries)
        except Exception:
            logger.debug("Trace logging failed for %s", user_id, exc_info=True)
