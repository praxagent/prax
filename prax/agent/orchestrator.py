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

# ---------------------------------------------------------------------------
# Runtime model override — set via the /teamwork/model API
# ---------------------------------------------------------------------------

_model_override: str | None = None


def set_model_override(model: str | None) -> None:
    """Set a runtime override for the orchestrator model.

    Pass ``None`` or ``"auto"`` to clear the override and revert to
    the config default.
    """
    global _model_override
    if model and model.lower() == "auto":
        model = None
    _model_override = model
    logger.info("Model override set to: %s", _model_override or "(auto)")


def get_model_override() -> str | None:
    """Return the current runtime model override, or None if using config default."""
    return _model_override


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
        # Runtime model override takes precedence over config and constructor args
        effective_model = _model_override or model or cfg.get("model")
        # The orchestrator is the ONLY place where "should I use a tool?"
        # decisions are made.  The low tier (nano) is too weak for reliable
        # tool selection — it skips tools and answers from training data
        # (confirmed by GAIA eval: 0 tool calls on low, correct tool use
        # on medium; and the 2026-04-09 daily briefing failure where the
        # low-tier orchestrator hallucinated with 0 tool calls).
        #
        # Default to medium.  Text-only pipelines (memory compaction,
        # session classification, entity extraction, note quality) stay
        # on low — they don't call tools.
        self.llm = build_llm(
            provider=provider or cfg.get("provider"),
            model=effective_model,
            temperature=temperature if temperature is not None else cfg.get("temperature"),
            tier=tier or cfg.get("tier") or "medium",
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

    # Cache which users' plugin dirs have been registered this process
    # lifetime so we don't re-scan + re-load 40+ seconds of plugins on
    # every single message.
    _registered_plugin_dirs: set[str] = set()

    def _register_workspace_plugins(self, user_id: str) -> None:
        """Tell the plugin loader about this user's workspace plugins directory.

        Only runs load_all() the FIRST time a user's plugins dir is
        seen.  Subsequent calls for the same user skip the expensive
        re-scan.  Hot-reload still works via ``_rebuild_if_needed``
        which checks the version counter.
        """
        try:
            from prax.plugins.loader import get_plugin_loader
            from prax.services.workspace_service import get_workspace_plugins_dir
            plugins_dir = get_workspace_plugins_dir(user_id)
            if not plugins_dir:
                return
            if plugins_dir in ConversationAgent._registered_plugin_dirs:
                return  # already loaded this process lifetime
            logger.info("Registering workspace plugins from %s (first time)", plugins_dir)
            loader = get_plugin_loader()
            loader.add_workspace_plugins_dir(plugins_dir)
            loader.load_all()
            ConversationAgent._registered_plugin_dirs.add(plugins_dir)
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

    # Substrings that indicate a delegation response contains a caveat /
    # partial-completion signal.  When any of these appear in a sub-agent's
    # reply, the auto-completer MUST NOT silently mark steps done — the
    # delegated agent is explicitly saying the work is not fully done or
    # there are qualifications the main agent needs to surface to the user.
    # Matching is lowercase, whole-string contains.
    _CAVEAT_MARKERS = frozenset({
        "one caveat",
        "however,",
        "but it does not guarantee",
        "does not guarantee",
        "if you want, i can",
        "if you'd like, i can",
        "if you want me to",
        "do you want me to",
        "want me to",
        "should i",
        "partial",
        "could not",
        "couldn't",
        "unable to",
        "not fully",
        "not complete",
        "does not include",
        "doesn't include",
        "missing",
        "placeholder",
        "skipped",
        "didn't actually",
        "did not actually",
    })

    @classmethod
    def _response_has_caveat(cls, content: str) -> str | None:
        """Return the first caveat marker found in ``content``, else None.

        Used by :meth:`_auto_complete_plan_steps` to refuse auto-completion
        when a sub-agent's reply explicitly flagged partial work.
        """
        if not content:
            return None
        lowered = content.lower()
        for marker in cls._CAVEAT_MARKERS:
            if marker in lowered:
                return marker
        return None

    @staticmethod
    def _auto_complete_plan_steps(uid: str, messages: list) -> None:
        """Auto-mark plan steps done when delegation/work tools returned successfully.

        Scans messages for completed delegation tool calls. If the plan has
        incomplete steps and work was clearly done (delegation returned, files
        saved), marks ALL incomplete steps as done. This prevents the plan
        enforcement loop from re-delegating work that already completed.

        Refuses to auto-complete if any delegation response contained a
        caveat marker (see :attr:`_CAVEAT_MARKERS`) — in that case the
        sub-agent is explicitly saying the work is partial, and silently
        marking the plan done would let Prax lie "Done" to the user.
        Prax must explicitly call ``agent_step_done`` for each step he's
        actually completed when caveats are present.
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
        caveat_found: str | None = None
        for msg in messages:
            if isinstance(msg, ToolMessage) and msg.name in ConversationAgent._DELEGATION_TOOLS:
                content = msg.content or ""
                content_lower = content.lower()
                # Only count as done if the result doesn't look like an error
                if any(err in content_lower for err in ("failed", "error:", "sub-agent failed")):
                    continue
                work_done = True
                # Check for partial-completion caveats that should block
                # silent auto-completion.
                marker = ConversationAgent._response_has_caveat(content)
                if marker:
                    caveat_found = marker
                    break

        if not work_done:
            return

        if caveat_found:
            logger.warning(
                "Refusing to auto-complete plan steps: delegation response "
                "contained caveat marker %r. Prax must explicitly call "
                "agent_step_done for completed steps and surface the caveat "
                "to the user.",
                caveat_found,
            )
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

    def run(self, conversation: Iterable[BaseMessage], user_input: str, workspace_context: str = "", trigger: str = "") -> str:
        """Execute the agent graph and return the final string response."""
        import time as _time

        from prax.agent.trace import GraphCallbackHandler, start_span

        _run_start = _time.monotonic()
        _run_deadline = _run_start + settings.agent_run_timeout

        # Start a root span that wraps the entire orchestrator invocation.
        # This sets last_root_trace_id so callers can attach it to responses.
        root_span = start_span("orchestrator", "orchestrator")

        # Store the user's raw input as the trace trigger so the execution
        # graph shows what started it — without system prefixes or tool guidance.
        root_span.ctx.graph.trigger = trigger or user_input

        # Classify session — groups related traces together.
        try:
            from prax.services.session_service import classify_session
            session_id = classify_session(current_user_id.get() or "anonymous", user_input)
            root_span.ctx.graph.session_id = session_id
        except Exception:
            pass

        # Callback handler that adds individual tool calls as child nodes
        # in the execution graph — gives TeamWork full depth visibility.
        _graph_cb = GraphCallbackHandler(
            parent_span_id=root_span.span_id,
            graph=root_span.ctx.graph,
            trace_id=root_span.trace_id,
            live_agent_name=settings.agent_name,
        )

        # Reset per-plugin call counters for the new message.
        from prax.plugins.monitored_tool import reset_plugin_call_counts
        reset_plugin_call_counts()

        # Set user message context for smart confirmation gate.
        from prax.agent.user_context import current_component, current_user_message
        current_user_message.set(user_input)
        current_component.set("orchestrator")

        # Initialize tool-call budget for this turn.
        from prax.agent.autonomy import get_recursion_limit
        from prax.agent.governed_tool import init_turn_budget
        effective_limit = get_recursion_limit(settings.agent_max_tool_calls)
        init_turn_budget(effective_limit)

        # Reset Active Inference prediction tracker for the new turn.
        try:
            from prax.agent.prediction_tracker import get_prediction_tracker
            get_prediction_tracker().reset()
        except Exception:
            pass

        # Register workspace plugins for the current user.
        uid = current_user_id.get()
        if uid:
            self._register_workspace_plugins(uid)

        # Rebuild graph if plugins changed since last invocation.
        self._rebuild_if_needed()

        history: list[BaseMessage] = list(conversation)
        logger.debug("Agent invoked with %d history messages", len(history))

        # Difficulty-driven routing: estimate task difficulty and inject
        # context so the agent knows how the system classified the request.
        from prax.agent.difficulty import difficulty_context_for_prompt, estimate_difficulty
        estimate_difficulty(user_input)

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

        # Metacognitive injection: if the orchestrator has known failure
        # patterns from past runs, inject warnings into the prompt.
        metacognitive_hint = ""
        try:
            from prax.agent.metacognitive import get_metacognitive_store
            metacognitive_hint = get_metacognitive_store().get_prompt_injection("orchestrator")
        except Exception:
            pass

        # Active Inference injection: if prediction errors are accumulating,
        # inject a warning to steer the agent toward read-only tools.
        prediction_hint = ""
        try:
            from prax.agent.prediction_tracker import get_prediction_tracker
            prediction_hint = get_prediction_tracker().prompt_injection()
        except Exception:
            pass

        difficulty_hint = "\n\n" + difficulty_context_for_prompt(user_input)

        # Memory injection: retrieve relevant memories and STM scratchpad.
        memory_context = ""
        if uid:
            try:
                from prax.services.memory_service import get_memory_service
                memory_context = get_memory_service().build_memory_context(uid, user_input)
            except Exception:
                pass  # Graceful degradation — memory is optional.

        # Health monitor: inject advisory from last check if anomalies detected.
        health_hint = ""
        try:
            from prax.agent.health_monitor import get_last_check
            last_check = get_last_check()
            if last_check and last_check.overall != "healthy" and last_check.alerts:
                health_hint = (
                    "\n\n## Health Advisory\n"
                    + "\n".join(f"- {a}" for a in last_check.alerts)
                )
        except Exception:
            pass

        # Temporal + channel context — gives the model a clear "now" so it
        # can distinguish fresh requests from stale STM/LTM context.
        temporal_context = ""
        try:
            from prax.agent.user_context import current_channel_name
            from prax.utils.time_format import format_current_time

            # Resolve user timezone if available.
            tz_name: str | None = None
            try:
                from prax.services.memory.stm import stm_read
                if uid:
                    stm_entries = stm_read(uid)
                    for entry in stm_entries:
                        if "timezone" in entry.key.lower() or "timezone" in entry.content.lower():
                            # Best-effort — content may be "timezone: America/Los_Angeles"
                            content = entry.content
                            for marker in ("America/", "Europe/", "Asia/", "Africa/", "Australia/", "Pacific/"):
                                if marker in content:
                                    start = content.index(marker)
                                    end = start
                                    while end < len(content) and (content[end].isalnum() or content[end] in "/_-"):
                                        end += 1
                                    tz_name = content[start:end]
                                    break
                            break
            except Exception:
                pass

            now_str = format_current_time(tz_name)
            channel_label = current_channel_name.get("")
            channel_line = (
                f"Channel: #{channel_label}" if channel_label and channel_label != "DM"
                else ("Channel: Direct Message" if channel_label == "DM" else "")
            )
            temporal_context = (
                f"\n\n## Current Context\n"
                f"- **Now:** {now_str}\n"
                + (f"- **{channel_line}**\n" if channel_line else "")
                + "- The current user message (below) is the source of truth "
                  "for the task. Any older STM/memory context is for reference only."
            )
        except Exception:
            pass

        full_prompt = (
            _load_system_prompt()
            + temporal_context
            + workspace_context
            + memory_context
            + complexity_hint
            + difficulty_hint
            + metacognitive_hint
            + prediction_hint
            + health_hint
        )

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

        # Context window management — budget, clear old tool results, compact.
        try:
            from prax.agent.context_manager import prepare_context
            from prax.plugins.llm_config import get_component_config
            cfg = get_component_config("orchestrator")
            orch_tier = cfg.get("tier") or "low"
            orch_model = cfg.get("model") or ""
            messages, ctx_budget = prepare_context(messages, tier=orch_tier, model=orch_model)
            logger.info(
                "Context budget: %d/%d tokens (system=%d, history=%d)%s",
                ctx_budget.total, ctx_budget.limit,
                ctx_budget.system_prompt, ctx_budget.history,
                " [OVERFLOW]" if ctx_budget.overflow else "",
            )
        except Exception:
            logger.debug("Context management failed, proceeding without", exc_info=True)

        # Start a checkpointed turn for this user.
        turn = self.checkpoint_mgr.start_turn(uid or "anonymous")
        config = {
            **self.checkpoint_mgr.graph_config(turn),
            "recursion_limit": effective_limit,
            "callbacks": [_graph_cb],
        }

        # Set Prax to working status so the UI shows him active.
        from prax.services.teamwork_hooks import log_activity, push_live_output, set_role_status
        set_role_status(settings.agent_name, "working")
        push_live_output(settings.agent_name, "Processing request...\n", status="running", append=False)
        log_activity(settings.agent_name, "task_started", f"Processing: {user_input[:150]}")

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
                try:
                    from prax.services.health_telemetry import EventCategory, Severity, record_event
                    record_event(
                        EventCategory.TURN_TIMEOUT, Severity.WARNING,
                        component="orchestrator",
                        details=f"Timeout after {settings.agent_run_timeout}s for user {uid}",
                    )
                except Exception:
                    pass

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
        # Mark Prax's live output as completed so the UI shows the session log.
        push_live_output(
            settings.agent_name,
            f"\nCompleted ({_graph_cb._tool_count} tool calls)\n",
            status="completed",
        )
        log_activity(
            settings.agent_name, "task_completed",
            f"Completed with {_graph_cb._tool_count} tool calls",
        )

        # Write full agent trace to the user's workspace log.
        self._write_trace(uid, user_input, result.get("messages", []))

        # Extract the last AI message from the graph output.
        response = ""
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                response = msg.content
                break

        # Deterministic claim audit: check for ungrounded numeric claims
        # AND narrative (news/weather) claims without grounding tool calls.
        # Scheduled tasks get a blocking check — hallucinated notifications
        # sent to an absent user are especially damaging.
        if response:
            is_scheduled = user_input.lstrip().startswith("[SCHEDULED_TASK")
            response = self._audit_claims(
                response,
                result.get("messages", []),
                uid,
                scheduled=is_scheduled,
            )

        # Update session summary for next turn's classification.
        try:
            from prax.services.session_service import update_session_summary
            update_session_summary(uid or "anonymous", user_input, response)
        except Exception:
            pass

        # Export trajectory for fine-tuning (fire-and-forget).
        try:
            from prax.services.trajectory_service import export_trajectory
            export_trajectory(
                uid, user_input, response, result.get("messages", []),
                session_id=root_span.ctx.graph.session_id,
            )
        except Exception:
            pass

        # Health telemetry: record turn completion.
        try:
            from prax.services.health_telemetry import EventCategory, record_event
            record_event(
                EventCategory.TURN_COMPLETED,
                component="orchestrator",
                details=f"user={uid or 'anonymous'}, tools={_graph_cb._tool_count}",
                latency_ms=((_time.monotonic() - _run_start) * 1000),
            )
        except Exception:
            pass

        # Pipeline coverage instrumentation (Phase 0) — capture which spoke
        # matched, the request, and the outcome so we can build a Pareto chart
        # of coverage gaps.
        #
        # Canonical short names for each spoke. Keeps the coverage log,
        # harness scenarios, and Pareto reports consistent regardless of
        # the underlying delegate_* tool name.
        _SPOKE_NAME_MAP = {
            "content_editor": "content",
            # All other spokes already use their short name as the delegate
            # tool suffix (delegate_browser, delegate_knowledge, etc.).
        }
        try:
            from prax.services import pipeline_coverage
            delegations = []
            for msg in result.get("messages", []):
                if isinstance(msg, AIMessage):
                    for tc in getattr(msg, "tool_calls", []) or []:
                        name = tc.get("name", "")
                        if name.startswith("delegate_"):
                            raw = name.removeprefix("delegate_")
                            delegations.append(_SPOKE_NAME_MAP.get(raw, raw))
            # Determine the matched spoke. Heuristic:
            # - Single delegation → that spoke
            # - Multiple delegations → first one (the primary)
            # - Generic delegate_task → "fallback"
            # - No delegation → "direct"
            if not delegations:
                matched_spoke = "direct"
            elif "task" in delegations:
                matched_spoke = "fallback"
            else:
                matched_spoke = delegations[0]

            # Determine the outcome status. The orchestrator's status is set
            # via root_span.end below — at this point we know it's at least
            # "completed" by virtue of reaching this code path.
            outcome_status = "completed"
            if _time.monotonic() >= _run_deadline:
                outcome_status = "timeout"
            elif not response:
                outcome_status = "failed"

            embedding = pipeline_coverage._embed_request(user_input)
            pipeline_coverage.record_turn(
                user_id=uid or "anonymous",
                request=user_input,
                matched_spoke=matched_spoke,
                delegations=delegations,
                outcome_status=outcome_status,
                tool_call_count=_graph_cb._tool_count,
                duration_ms=((_time.monotonic() - _run_start) * 1000),
                embedding=embedding,
            )
            # Periodic auto-prune so the on-disk file stays bounded
            # without a separate scheduler. No-op most turns; rewrites
            # the file every 100 turns to drop entries older than 30 days.
            pipeline_coverage.maybe_prune()
        except Exception:
            logger.debug("Pipeline coverage recording failed", exc_info=True)

        # Auto-consolidate memory every N turns. Without this hook the
        # memory consolidation pipeline is dead code — Prax never calls
        # stm_write himself, so STM/LTM stay empty even though the
        # infrastructure is in place.
        try:
            from prax.services.memory_service import maybe_consolidate
            maybe_consolidate(uid or "")
        except Exception:
            logger.debug("Auto-consolidation failed", exc_info=True)

        # Health monitor: check for anomalies every N turns.
        try:
            from prax.agent.health_monitor import on_turn_end
            on_turn_end()
        except Exception:
            pass

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
        # Preserve callbacks through config rebuilds so tool call graph nodes
        # continue to be emitted on retries.
        _callbacks = config.get("callbacks", [])
        fresh_restarts = 0  # Guard against infinite fresh-start loops.

        _context_retries = 0
        _MAX_CONTEXT_RETRIES = 3

        while True:
            try:
                return self.graph.invoke({"messages": messages}, config=config)
            except Exception as exc:
                # --- Context overflow recovery ---
                # If the LLM rejects the payload for being too large,
                # compact 20% more aggressively and retry (up to 3 times).
                from langchain_core.exceptions import ContextOverflowError
                if isinstance(exc, ContextOverflowError) and _context_retries < _MAX_CONTEXT_RETRIES:
                    _context_retries += 1
                    logger.warning(
                        "Context overflow (attempt %d/%d) — compacting and retrying",
                        _context_retries, _MAX_CONTEXT_RETRIES,
                    )
                    try:
                        from prax.services.health_telemetry import EventCategory, Severity, record_event
                        record_event(
                            EventCategory.CONTEXT_OVERFLOW, Severity.WARNING,
                            component="orchestrator",
                            details=f"Attempt {_context_retries}/{_MAX_CONTEXT_RETRIES}",
                        )
                    except Exception:
                        pass
                    try:
                        from prax.plugins.llm_config import get_component_config
                        cfg = get_component_config("orchestrator")
                        tier = cfg.get("tier") or "low"
                        model = cfg.get("model") or ""
                        # Reduce the budget by 20% each retry
                        from prax.agent.context_manager import get_context_limit
                        shrunk_limit = int(get_context_limit(tier, model) * (0.8 ** _context_retries))
                        from prax.agent.context_manager import (
                            clear_old_tool_results,
                            compact_history,
                            count_message_tokens,
                            truncate_history,
                        )
                        messages = clear_old_tool_results(messages, keep_last_n=3)
                        messages = compact_history(messages, shrunk_limit, tier=tier)
                        messages = truncate_history(messages, shrunk_limit)
                        new_count = count_message_tokens(messages)
                        logger.info(
                            "Context compacted to %d tokens (limit=%d, attempt=%d)",
                            new_count, shrunk_limit, _context_retries,
                        )
                        continue  # retry with compacted messages
                    except Exception as compact_err:
                        logger.warning("Context compaction failed: %s", compact_err)
                        # Fall through to normal error handling

                logger.warning(
                    "Agent graph failed (user=%s): %s", user_id, exc,
                )

                # Multi-perspective error analysis for structured recovery
                try:
                    from prax.agent.error_recovery import build_recovery_context
                    turn = self.checkpoint_mgr.get_turn(user_id)
                    attempt = turn.retries_used + 1 if turn else 1
                    recovery_ctx = build_recovery_context(
                        tool_name="orchestrator_graph",
                        error_message=str(exc),
                        attempt=attempt,
                    )
                    logger.info("Recovery context: %s", recovery_ctx[:200])
                except Exception:
                    pass

                # Record failure for metacognitive learning
                try:
                    from prax.agent.metacognitive import get_metacognitive_store
                    error_type = type(exc).__name__
                    get_metacognitive_store().record_failure(
                        component="orchestrator",
                        pattern_id=f"graph_{error_type}",
                        description=f"Graph invocation failed: {error_type}: {str(exc)[:80]}",
                        compensating_instruction=f"Previous runs hit {error_type} — verify tool args before calling.",
                    )
                except Exception:
                    pass

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
                        "callbacks": _callbacks,
                    }
                    continue

                # Record the retry event for health monitoring.
                try:
                    from prax.services.health_telemetry import EventCategory, Severity, record_event
                    record_event(
                        EventCategory.RETRY, Severity.WARNING,
                        component="orchestrator",
                        details=f"Graph failed for user {user_id}: {type(exc).__name__}",
                    )
                except Exception:
                    pass

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
                        "callbacks": _callbacks,
                    }
                    continue

                logger.info(
                    "Rolling back to checkpoint and retrying (user=%s, attempt=%d)",
                    user_id,
                    self.checkpoint_mgr.get_turn(user_id).retries_used,  # type: ignore[union-attr]
                )
                # Resume from the rollback checkpoint — LangGraph will continue
                # from the saved state, so we pass None for messages.
                config = {**rollback_cfg, "callbacks": _callbacks}

    @staticmethod
    def _audit_claims(
        response: str,
        messages: list,
        user_id: str | None,
        *,
        scheduled: bool = False,
    ) -> str:
        """Run deterministic claim audit and log findings.

        Two independent checks run:

        1. Numeric claim audit — verbatim-match dollar/percent/rank claims
           against tool results (catches fabricated numbers like the
           PHL→SNA $83 incident).
        2. Narrative grounding audit — detect "news/weather/headlines"
           language in the response and verify that at least one
           research/web/news/browser tool was actually called this turn.

        For scheduled tasks, narrative-grounding failures are promoted to
        a BLOCKING substitution: the fabricated response is replaced with
        a safe fallback because the user is not present to push back.
        For interactive turns, both checks remain advisory — the epistemic
        tags and system prompt are the primary defense, and the human can
        always correct in the next turn.
        """
        from prax.agent.trace import start_span
        audit_span = start_span("claim_audit", "auditor")

        try:
            from prax.agent.claim_audit import (
                audit_claims,
                audit_narrative_grounding,
                audit_plan_completion,
                format_audit_warning,
            )
            from prax.services.teamwork_hooks import (
                log_activity,
                post_to_channel,
                push_live_output,
                set_role_status,
            )

            set_role_status("Auditor", "working")
            push_live_output("Auditor", "Running claim audit...\n", status="running", append=False)

            # Collect all tool results from this turn.
            tool_results: list[str] = []
            for msg in messages:
                if isinstance(msg, ToolMessage) and msg.content:
                    tool_results.append(msg.content)

            findings = audit_claims(response, tool_results)
            narrative = audit_narrative_grounding(response, messages)
            plan_mismatch = audit_plan_completion(response, messages)

            flagged_parts: list[str] = []
            if findings:
                flagged_parts.append(format_audit_warning(findings))
            if narrative:
                called = ", ".join(narrative["called_tools"]) or "(none)"
                phrases = ", ".join(f"'{p}'" for p in narrative["phrases"])
                flagged_parts.append(
                    f"UNGROUNDED NARRATIVE: claims phrases {phrases} but no "
                    f"research/web/news/browser tool was called (called: {called})"
                )
            if plan_mismatch:
                flagged_parts.append(
                    f"PLAN-COMPLETION MISMATCH: response claims "
                    f"{plan_mismatch['completion_claim']!r} but "
                    f"{plan_mismatch['caveat_tool']} reply contained caveat "
                    f"{plan_mismatch['caveat_marker']!r} — the sub-agent "
                    f"said the work is partial and the response ignored it"
                )

            if flagged_parts:
                warning = "; ".join(flagged_parts)
                logger.warning("Claim audit flagged response (user=%s scheduled=%s): %s",
                               user_id, scheduled, warning)
                post_to_channel("general", f"[Claim Audit] {warning}", agent_name="Auditor")
                push_live_output("Auditor", f"Flagged: {warning}\n", status="completed")
                log_activity("Auditor", "audit", f"Claim audit flagged: {warning}")
                audit_span.end(status="completed", summary=f"Flagged: {warning[:200]}")

                # Persist to trace as an audit event.
                if user_id:
                    try:
                        append_trace(user_id, [{
                            "type": TraceEvent.AUDIT,
                            "content": f"[CLAIM-AUDIT] {warning}",
                        }])
                    except Exception:
                        pass

                # BLOCKING substitution for scheduled tasks with narrative
                # hallucinations. The user is not present to correct — better
                # to deliver nothing substantive than fabricated news.
                if scheduled and narrative:
                    logger.error(
                        "BLOCKING scheduled-task response due to ungrounded "
                        "narrative claims (user=%s): %s",
                        user_id, warning,
                    )
                    return (
                        "[Auto-suppressed scheduled briefing]\n\n"
                        "I couldn't fetch fresh news or weather data for "
                        "today's briefing — rather than send you made-up "
                        "content, I'm holding this one. Ask me for a "
                        "briefing when you have a moment and I'll try again "
                        "with live research."
                    )
            else:
                push_live_output("Auditor", "No claims flagged.\n", status="completed")
                log_activity("Auditor", "audit", "Claim audit passed — no issues found")
                audit_span.end(status="completed", summary="No claims flagged")
        except Exception:
            logger.debug("Claim audit failed", exc_info=True)
            audit_span.end(status="failed", summary="Audit error")
        finally:
            set_role_status("Auditor", "idle")

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

        # Flush Active Inference prediction records into the trace.
        try:
            from prax.agent.prediction_tracker import get_prediction_tracker
            for pr in get_prediction_tracker().drain_records():
                entries.append({
                    "type": TraceEvent.PREDICTION_ERROR,
                    "content": (
                        f"tool={pr['tool']} error={pr['error']} "
                        f"expected={pr['expected']!r} "
                        f"actual={pr['actual']!r}"
                    ),
                })
        except Exception:
            pass

        # Flush logprob entropy data into the trace.
        try:
            from prax.agent.logprob_analyzer import drain_entropy_buffer
            for ent in drain_entropy_buffer():
                entries.append({
                    "type": TraceEvent.LOGPROB_ENTROPY,
                    "content": (
                        f"tool={ent.tool_name} entropy={ent.entropy_score} "
                        f"mean_lp={ent.mean_logprob} min_lp={ent.min_logprob} "
                        f"uncertain_tokens={ent.high_entropy_tokens}"
                    ),
                })
        except Exception:
            pass

        # Flush semantic entropy data into the trace (Phase 4).
        try:
            from prax.agent.semantic_entropy import drain_semantic_entropy_buffer
            for se in drain_semantic_entropy_buffer():
                entries.append({
                    "type": TraceEvent.SEMANTIC_ENTROPY,
                    "content": (
                        f"tool={se.proposed_tool} "
                        f"samples={se.sampled_tools} "
                        f"agreement={se.agreement_ratio} "
                        f"blocked={se.blocked}"
                    ),
                })
        except Exception:
            pass

        try:
            append_trace(user_id, entries)
        except Exception:
            logger.debug("Trace logging failed for %s", user_id, exc_info=True)
