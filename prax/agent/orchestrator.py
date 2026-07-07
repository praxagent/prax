"""Conversation agent built on LangGraph."""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from prax.agent.agent_loop import build_agent_loop
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

# Maximum number of continuation rounds when the agent tries to stop after a
# recoverable URL/content-fetch failure.  Keep this low so bad sites don't trap
# the turn forever.
_MAX_RECOVERY_CONTINUATIONS = 2

# A plan-housekeeping acknowledgement that must never be surfaced as the user-
# facing reply (e.g. "Done — the plan is cleared.", "Plan cleared").
_PLAN_ACK_RE = re.compile(
    r"^\s*(done[.!,—\-\s]*)?(the\s+)?plan\s+(is\s+)?(now\s+)?(been\s+)?clear(ed)?\.?\s*$",
    re.IGNORECASE,
)

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


_EPISTEMIC_VIGILANCE_HINT = (
    "\n\n## Epistemic vigilance\n"
    "Before accepting a user's factual, health, legal, or safety PREMISE as true, "
    "pause and check it — silently ask \"wait a minute, is this premise actually "
    "correct?\". If a premise is false or unsafe, say so plainly and correct it "
    "before answering the rest; do NOT accommodate a wrong premise just because the "
    "user asserted it. Weight scrutiny by source reliability, and do not "
    "over-challenge premises that are correct or harmless — only push back when a "
    "premise is genuinely wrong or risky."
)


def _load_system_prompt() -> str:
    """Load the system prompt from the plugin prompts directory."""
    from prax.agent.model_tiers import tier_for_system_prompt

    mgr = get_prompt_manager()
    runtime_env = "Docker (persistent sandbox)" if settings.running_in_docker else "local"
    if settings.sandbox_available:
        sandbox_guidance = (
            "- The sandbox is always running — no need to start it. You can install "
            "system packages with sandbox_install (e.g. sandbox_install('poppler-utils')). "
            "Installed packages persist until the container restarts. For permanent "
            "additions, update the sandbox Dockerfile and rebuild.\n"
            "- Plugin tools that need system packages (pdflatex, ffmpeg, pdftoppm, etc.) "
            "automatically route commands to the sandbox container. Just call the plugin "
            "tool normally."
        )
    else:
        sandbox_guidance = (
            "- The sandbox is disabled in this deployment. Sandbox coding sessions, "
            "the in-browser terminal, the desktop, and run_python are unavailable, and "
            "there is no delegate_sandbox/delegate_desktop. Plugin tools that need system "
            "packages (pdflatex, ffmpeg, etc.) require those packages on the host; if a "
            "plugin reports missing deps, guide the user to install them."
        )
    try:
        from prax.services.deployment_info import summary_line
        deployment = summary_line()
    except Exception:
        deployment = "unknown"
    prompt = mgr.load("system_prompt.md", {
        "AGENT_NAME": settings.agent_name,
        "RUNTIME_ENV": runtime_env,
        "DEPLOYMENT": deployment,
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
        self._orchestrator_tier = tier or cfg.get("tier") or "medium"
        self.llm = build_llm(
            provider=provider or cfg.get("provider"),
            model=effective_model,
            temperature=temperature if temperature is not None else cfg.get("temperature"),
            tier=self._orchestrator_tier,
        )
        # Cross-provider failover state (engaged only when
        # settings.llm_fallback_enabled).  Tracks the provider currently
        # bound to self.llm and which providers have already been tried this
        # turn so we step through the chain without looping.
        self._primary_provider: str = (
            provider or cfg.get("provider") or settings.default_llm_provider
        ).lower()
        self._active_provider: str = self._primary_provider
        self._tried_providers: set[str] = {self._primary_provider}
        # Providers denylisted this process after a *terminal* failure
        # (auth/billing/access/decommissioned) — see _maybe_failover. Maps
        # provider -> {"kind", "detail", "ts"}; auto-re-probed after a cooldown.
        self._denylisted: dict[str, dict] = {}
        # User-facing notices queued when a provider is denylisted, drained into
        # the turn's response so the user learns *why* (e.g. a late bill).
        self._pending_denylist_notices: list[str] = []
        self.checkpoint_mgr = CheckpointManager()
        self.tools = get_registered_tools()
        self.graph = build_agent_loop(
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
            self.graph = build_agent_loop(
                self.llm, self.tools, checkpointer=self.checkpoint_mgr.saver,
            )
            self._plugin_version = v

    # ------------------------------------------------------------------
    # Cross-provider failover (engaged only when settings.llm_fallback_enabled)
    # ------------------------------------------------------------------

    def _bind_provider(self, provider: str, model: str | None) -> None:
        """Rebuild self.llm + self.graph against *provider* for the rest of the turn."""
        self.llm = build_llm(
            provider=provider,
            model=model,
            tier=self._orchestrator_tier if not model else None,
        )
        self.graph = build_agent_loop(
            self.llm, self.tools, checkpointer=self.checkpoint_mgr.saver,
        )
        self._active_provider = provider.lower()

    def _is_denylisted(self, provider: str) -> bool:
        """True if *provider* is currently denylisted (auto-expires after the cooldown)."""
        prov = (provider or "").lower()
        entry = self._denylisted.get(prov)
        if not entry:
            return False
        cooldown = settings.llm_provider_denylist_cooldown_seconds
        if cooldown and cooldown > 0:
            import time
            if time.time() - entry["ts"] >= cooldown:
                self._denylisted.pop(prov, None)
                logger.info("Denylist cooldown elapsed for provider '%s' — re-probing.", prov)
                return False
        return True

    def _denylist_provider(self, provider: str, kind: str, detail: str) -> None:
        """Remove *provider* from the live pool after a terminal failure."""
        import time
        prov = (provider or "").lower()
        self._denylisted[prov] = {"kind": kind, "detail": detail, "ts": time.time()}
        logger.warning("Denylisted provider '%s' (%s: %s) from the model pool.", prov, kind, detail)
        try:
            from prax.services.health_telemetry import EventCategory, Severity, record_event
            record_event(
                EventCategory.RETRY, Severity.ERROR, component="orchestrator",
                details=f"LLM provider denylisted: {prov} ({kind}: {detail})",
            )
        except Exception:
            pass

    def _drain_denylist_notice(self) -> str:
        """Pop queued denylist notices as a response suffix (deduped, order-preserving)."""
        if not self._pending_denylist_notices:
            return ""
        notices, self._pending_denylist_notices = self._pending_denylist_notices, []
        seen: set[str] = set()
        uniq = [n for n in notices if not (n in seen or seen.add(n))]
        return "\n\n" + "\n\n".join(uniq)

    def _reset_to_primary_provider(self) -> None:
        """At turn start, fail back to the primary provider if we drifted off it.

        If the primary is currently denylisted (a terminal failure that hasn't
        cooled down yet), start the turn on the first healthy fallback instead,
        so we don't immediately re-hit the dead provider.
        """
        primary = self._primary_provider
        self._tried_providers = {primary}
        if self._is_denylisted(primary):
            try:
                from prax.agent.llm_fallback import get_fallback_providers
                for fb in get_fallback_providers(primary):
                    if self._is_denylisted(fb["provider"]):
                        continue
                    self._bind_provider(fb["provider"], fb.get("model"))
                    self._tried_providers.add(fb["provider"])
                    logger.info("Primary '%s' denylisted — starting turn on '%s'.", primary, fb["provider"])
                    return
            except Exception:
                logger.warning("Could not select a healthy provider for denylisted primary '%s'", primary, exc_info=True)
        if self._active_provider != primary:
            try:
                self._bind_provider(primary, None)
                logger.info("Reset orchestrator LLM back to primary provider '%s'", primary)
            except Exception:
                logger.warning("Could not reset to primary provider '%s'", primary, exc_info=True)

    def _maybe_failover(self, exc: BaseException, user_id: str) -> bool:
        """Fail over to the next healthy provider if *exc* is provider-side.

        Transient errors (rate limit / overload / 5xx / connection) fail over
        and reset to primary next turn.  *Terminal* errors (auth / billing /
        access / decommissioned — which a retry won't fix) additionally DENYLIST
        the failing provider from the pool and queue a user-facing notice
        explaining the likely cause, so the user can fix the root problem (a late
        bill, a revoked key, lost access) instead of Prax silently re-hitting a
        dead provider every turn.

        Returns True when a fallback provider was activated (the caller should
        retry), False otherwise.  No-op unless ``settings.llm_fallback_enabled``.
        """
        if not settings.llm_fallback_enabled:
            return False
        try:
            from prax.agent.llm_fallback import (
                classify_provider_error,
                get_fallback_providers,
                terminal_user_notice,
            )
            kind = classify_provider_error(exc)
            if kind is None:
                return False
            terminal = kind != "transient" and settings.llm_provider_denylist_enabled
            denied = self._active_provider
            detail = type(exc).__name__  # type name only — the message can echo the API key
            cooldown = settings.llm_provider_denylist_cooldown_seconds
            if terminal:
                self._denylist_provider(denied, kind, detail)
            for fb in get_fallback_providers(self._primary_provider):
                prov = fb["provider"]
                if prov in self._tried_providers or self._is_denylisted(prov):
                    continue
                self._tried_providers.add(prov)
                logger.warning(
                    "Provider '%s' failed (%s/%s) — failing over to '%s'",
                    denied, kind, type(exc).__name__, prov,
                )
                self._bind_provider(prov, fb.get("model"))
                try:
                    from prax.services.health_telemetry import EventCategory, Severity, record_event
                    record_event(
                        EventCategory.RETRY, Severity.WARNING, component="orchestrator",
                        details=f"LLM failover {denied} -> {prov} ({kind}/{type(exc).__name__})",
                    )
                except Exception:
                    pass
                if terminal:
                    self._pending_denylist_notices.append(
                        terminal_user_notice(denied, kind, detail, continuing=prov, cooldown_seconds=cooldown)
                    )
                return True
            # No healthy fallback left — still tell the user why the pool shrank.
            if terminal:
                self._pending_denylist_notices.append(
                    terminal_user_notice(denied, kind, detail, continuing=None, cooldown_seconds=cooldown)
                )
        except Exception:
            logger.debug("Failover evaluation failed", exc_info=True)
        return False

    @staticmethod
    def _maybe_clarify(user_input: str) -> str | None:
        """Return one clarifying question if the request is ambiguous AND risky.

        Opt-in via ``settings.intent_clarification_enabled``.  Uses a cheap
        LOW-tier model and is biased strongly toward proceeding; returns None
        (proceed) on any uncertainty, system/scheduled inputs, or error.
        """
        if not settings.intent_clarification_enabled:
            return None
        stripped = user_input.lstrip()
        if stripped.startswith("[SCHEDULED_TASK") or stripped.startswith("[SYSTEM"):
            return None
        try:
            from langchain_core.messages import HumanMessage

            from prax.agent.llm_factory import build_llm
            llm = build_llm(default_tier="low", config_key="intent_clarifier")
            prompt = (
                "You are a pre-flight intent checker for an autonomous assistant. "
                "Decide whether the user's request is BOTH genuinely ambiguous AND "
                "potentially irreversible or costly to get wrong — such that asking "
                "exactly ONE clarifying question first is clearly better than guessing. "
                "Bias STRONGLY toward proceeding: only ask when a wrong guess would "
                "waste real effort or do something hard to undo. "
                "If the assistant should proceed, reply with exactly 'PROCEED'. "
                "Otherwise reply with the single clarifying question and nothing else.\n\n"
                f"Request: {user_input}"
            )
            resp = llm.invoke([HumanMessage(content=prompt)])
            text = (getattr(resp, "content", "") or "").strip()
            if not text or text.upper().startswith("PROCEED"):
                return None
            # Keep it to a single question.
            return text.split("\n")[0].strip()
        except Exception:
            logger.debug("Intent clarification check failed; proceeding", exc_info=True)
            return None

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
        "delegate_plugins", "workspace_save", "workspace_patch", "note_create",
        "note_update",
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

    _URL_FAILURE_MARKERS = frozenset({
        "reader returned http",
        "err_name_not_resolved",
        "navigation failed",
        "could not read the url",
        "couldn't access the page",
        "could not access the page",
        "could not fetch",
        "error fetching",
        "domain did not resolve",
        "server ip address could not be found",
    })

    _ASK_USER_TO_CONTINUE_MARKERS = frozenset({
        "if you want, i can",
        "if you'd like, i can",
        "if you want me to",
        "send me",
        "paste the article",
        "paste the content",
        "provide a working link",
        "permission to try",
        "try again later",
        "i can still do one of",
        "do you want me to",
    })

    _URL_RECOVERY_TOOL_FAMILIES = {
        "fetch_url_content": "reader",
        "note_from_url": "reader",
        "delegate_knowledge": "reader",
        "delegate_browser": "browser",
        "browser_navigate": "browser",
        "background_search_tool": "search",
        "delegate_research": "search",
        "web_summary_tool": "summary",
    }

    # Phrases where the agent OFFERS to take an obvious next step rather than
    # taking it ("I saved a screenshot, I can take the next step and inspect it").
    _OFFER_NEXT_STEP_MARKERS = frozenset({
        "i can take the next step",
        "take the next step and",
        "i can inspect",
        "i can describe",
        "i can analyze",
        "want me to inspect",
        "want me to describe",
        "want me to analyze",
        "shall i inspect",
        "if you want, i can",
        "if you'd like, i can",
        "i can go ahead and",
    })

    # Tool-output signs that an intermediate artifact was just produced — the
    # thing the offer above is about using.
    _ARTIFACT_PRODUCED_MARKERS = frozenset({
        "screenshot saved", "saved to", "saved:", "downloaded", "/tmp/", "captured",
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
    def _last_ai_content(messages: list) -> str:
        for msg in reversed(messages or []):
            if isinstance(msg, AIMessage) and msg.content:
                return str(msg.content)
        return ""

    @classmethod
    def _should_continue_after_url_failure(cls, user_input: str, messages: list) -> bool:
        """Detect recoverable URL failures where the agent stopped too early.

        This catches the "reader failed, ask the user whether to try browser /
        archive / search" pattern.  Those fallback attempts are part of the
        job; the user should not have to push Prax through the next obvious
        recovery path.
        """
        response = cls._last_ai_content(messages).lower()
        if not response:
            return False
        if not any(marker in response for marker in cls._ASK_USER_TO_CONTINUE_MARKERS):
            return False

        saw_url_failure = False
        recovery_families: set[str] = set()
        for msg in messages or []:
            if not isinstance(msg, ToolMessage):
                continue
            name = str(getattr(msg, "name", "") or "")
            content = (msg.content or "").lower()
            family = cls._URL_RECOVERY_TOOL_FAMILIES.get(name)
            if family:
                recovery_families.add(family)
            if any(marker in content for marker in cls._URL_FAILURE_MARKERS):
                saw_url_failure = True

        if not saw_url_failure:
            return False

        # If the agent already tried reader, browser, and search/summary, it
        # can stop and report the exact failed attempts.
        tried_enough = (
            "reader" in recovery_families
            and "browser" in recovery_families
            and bool(recovery_families & {"search", "summary"})
        )
        if tried_enough:
            return False

        # Be conservative outside URL/content tasks.
        looks_like_url_task = (
            "http://" in user_input
            or "https://" in user_input
            or any(word in user_input.lower() for word in (
                "url", "article", "page", "link", "note", "summarize",
                "what went wrong",
            ))
        )
        return looks_like_url_task or bool(recovery_families)

    @staticmethod
    def _url_recovery_nudge() -> str:
        return (
            "[SYSTEM] You hit a URL/content fetch failure and your draft "
            "response asks the user whether to try another path. Do not ask "
            "yet. Try a new recovery route now: search the web for the title, "
            "domain, or URL slug; try a canonical URL variant if the hostname "
            "looks wrong; use delegate_browser if you have not already; or "
            "fetch a discovered working URL. If all distinct recovery paths "
            "fail, then report the exact attempts and stop."
        )

    @classmethod
    def _should_continue_after_offer(cls, user_input: str, messages: list) -> bool:
        """Detect a 'produced an artifact, then OFFERED to use it' stall.

        Generalizes the transcript failure ("I saved a screenshot... I can take
        the next step and inspect it") to any chain where the agent created an
        intermediate (screenshot/download/file) and then offered to take the
        obvious next step instead of taking it. Default-on
        (``AUTONOMY_FOLLOWTHROUGH_ENABLED``) — the user shouldn't have to nudge.
        """
        if not settings.autonomy_followthrough_enabled:
            return False
        response = cls._last_ai_content(messages).lower()
        if not response or not any(m in response for m in cls._OFFER_NEXT_STEP_MARKERS):
            return False
        for msg in messages or []:
            if isinstance(msg, ToolMessage):
                content = (msg.content or "").lower()
                if any(k in content for k in cls._ARTIFACT_PRODUCED_MARKERS):
                    return True
        return False

    @staticmethod
    def _offer_followthrough_nudge() -> str:
        return (
            "[SYSTEM] You produced an intermediate result (e.g. a saved "
            "screenshot/download/file) and your draft only OFFERS to take the "
            "next step ('I can take the next step and inspect it'). Don't offer — "
            "take it now: feed the artifact to the right tool (e.g. analyze_image "
            "on the saved path), then give the user the actual result. Only stop "
            "if the next step is expensive, irreversible, or genuinely ambiguous."
        )

    @staticmethod
    def _plan_ack_recovery_nudge(user_input: str) -> str:
        return (
            "[SYSTEM] Your last message was only internal plan housekeeping "
            "(e.g. 'the plan is cleared'), which is NOT an answer to the user. "
            f"The user's request was: {user_input!r}. Carry it out now and reply "
            "with the actual result. Never surface plan-clear or other bookkeeping "
            "as your response."
        )

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

    def run(self, conversation: Iterable[BaseMessage], user_input: str, workspace_context: str = "", trigger: str = "", source: str = "") -> str:
        """Execute the agent graph and return the final string response.

        *source* is the origin channel (discord | sms | voice | teamwork |
        scheduler | task_runner) — recorded on the execution graph so a trace
        shows where the request came from.
        """
        import time as _time

        from prax.agent.trace import GraphCallbackHandler, get_trace_heartbeat, start_span

        _run_start = _time.monotonic()
        _run_max_timeout = max(settings.agent_run_timeout, settings.agent_run_max_timeout)
        _run_max_deadline = _run_start + _run_max_timeout

        # Start a root span that wraps the entire orchestrator invocation.
        # This sets last_root_trace_id so callers can attach it to responses.
        root_span = start_span("orchestrator", "orchestrator")
        heartbeat = get_trace_heartbeat(root_span.trace_id)
        heartbeat.touch("orchestrator", "agent run started")

        # Store the user's raw input as the trace trigger so the execution
        # graph shows what started it — without system prefixes or tool guidance.
        root_span.ctx.graph.trigger = trigger or user_input
        # Origin channel: explicit arg wins; else fall back to the channel
        # ContextVar that discord/sms/teamwork entry points already set.
        try:
            from prax.agent.user_context import current_channel_name
            _src = source or current_channel_name.get("")
        except Exception:
            _src = source
        if _src:
            root_span.ctx.graph.source = _src

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
            heartbeat=heartbeat,
        )

        # Reset per-plugin call counters for the new message.
        from prax.plugins.monitored_tool import reset_plugin_call_counts
        reset_plugin_call_counts()

        # Set user message context for smart confirmation gate.
        from prax.agent.user_context import current_component, current_user_message
        current_user_message.set(user_input)
        current_component.set("orchestrator")

        # Intent clarification pre-flight (opt-in): on an ambiguous AND
        # potentially irreversible request, ask ONE question instead of
        # burning the full agent loop on a guess.
        clarification = self._maybe_clarify(user_input)
        if clarification:
            root_span.end(status="completed", summary="Asked a clarifying question")
            return clarification

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
        # Tool bodies may execute in a fresh contextvars context inside
        # LangGraph. Rebuild the graph once the request user/message context is
        # set so governance wrappers bind the correct user into every tool.
        self.tools = get_registered_tools()
        self.graph = build_agent_loop(
            self.llm, self.tools, checkpointer=self.checkpoint_mgr.saver,
        )
        self._plugin_version = self._current_plugin_version()

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
            if channel_label == "discord":
                channel_line = "Channel: Discord — user is OFF-NETWORK, cannot reach localhost/Tailscale URLs. Default to public sharing for any artifact."
            elif channel_label == "sms":
                channel_line = "Channel: SMS — user is OFF-NETWORK, cannot reach localhost/Tailscale URLs. Default to public sharing for any artifact."
            elif channel_label == "DM":
                channel_line = "Channel: Direct Message (TeamWork) — user is on-network."
            elif channel_label:
                channel_line = f"Channel: #{channel_label} (TeamWork) — user is on-network."
            else:
                channel_line = ""
            temporal_context = (
                f"\n\n## Current Context\n"
                f"- **Now:** {now_str}\n"
                + (f"- **{channel_line}**\n" if channel_line else "")
                + "- The current user message (below) is the source of truth "
                  "for the task. Any older STM/memory context is for reference only."
            )
        except Exception:
            pass

        base_system_prompt = _load_system_prompt()
        if settings.prompt_selectivity_enabled:
            try:
                from prax.agent.prompt_selection import select_sections
                base_system_prompt = select_sections(base_system_prompt, user_input)
            except Exception:
                logger.debug("Prompt selectivity failed; using full prompt", exc_info=True)

        # Epistemic vigilance (flag-gated, default off) — a lightweight anti-sycophancy
        # principle: verify a user's factual/health/safety PREMISE before accepting it,
        # correct false/unsafe ones, but don't over-challenge correct premises.
        # (arXiv 2601.04435 — the "wait a minute" pragmatic intervention.)
        vigilance_hint = ""
        try:
            if settings.epistemic_vigilance_enabled:
                vigilance_hint = _EPISTEMIC_VIGILANCE_HINT
        except Exception:
            pass

        full_prompt = (
            base_system_prompt
            + temporal_context
            + workspace_context
            + memory_context
            + complexity_hint
            + difficulty_hint
            + metacognitive_hint
            + prediction_hint
            + health_hint
            + vigilance_hint
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
        # If a prior turn failed over to a fallback provider, give the primary
        # another chance this turn (its breaker fails fast if still unhealthy,
        # and we'll fail over again if needed).
        self._reset_to_primary_provider()
        # OTel/Prometheus callbacks: attach at the invocation level (not just
        # on the LLM instance) so both LLM and tool events dispatch through
        # LangChain's CallbackManager for the full chain.  Attaching them
        # only via ``ChatModel(callbacks=...)`` at LLM construction misses
        # tool-level events and can be skipped by LangGraph's runnable.
        _cbs: list = [_graph_cb]
        try:
            from prax.observability.callbacks import get_otel_callbacks
            _cbs.extend(get_otel_callbacks())
        except Exception:
            pass
        config = {
            **self.checkpoint_mgr.graph_config(turn),
            "recursion_limit": effective_limit,
            "callbacks": _cbs,
        }

        # Set Prax to working status so the UI shows him active.
        from prax.services.teamwork_hooks import log_activity, push_live_output, set_role_status
        set_role_status(settings.agent_name, "working")
        push_live_output(settings.agent_name, "Processing request...\n", status="running", append=False)
        log_activity(settings.agent_name, "task_started", f"Processing: {user_input[:150]}")

        run_status = "completed"
        run_error_summary = ""
        try:
            result = self._invoke_with_retry(messages, config, turn.user_id, heartbeat)

            # Plan enforcement: if the agent responded but has an incomplete
            # plan, push it back into the loop to finish the work.
            continuations = 0
            while (
                uid
                and self._has_incomplete_plan(uid)
                and continuations < _MAX_PLAN_CONTINUATIONS
                and _time.monotonic() < _run_max_deadline
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
                result = self._invoke_graph_once(
                    continuation_messages, config, turn.user_id, heartbeat,
                )

            recovery_continuations = 0
            while (
                self._should_continue_after_url_failure(user_input, result.get("messages", []))
                and recovery_continuations < _MAX_RECOVERY_CONTINUATIONS
                and _time.monotonic() < _run_max_deadline
            ):
                recovery_continuations += 1
                logger.info(
                    "URL recovery enforcement: continuation %d/%d (user=%s)",
                    recovery_continuations, _MAX_RECOVERY_CONTINUATIONS, uid,
                )
                recovery_messages = result.get("messages", []) + [
                    HumanMessage(content=self._url_recovery_nudge()),
                ]
                result = self._invoke_graph_once(
                    recovery_messages, config, turn.user_id, heartbeat,
                )

            # Follow-through enforcement (default-on): if the agent produced an
            # artifact and only OFFERED to use it, make it take the step.
            offer_continuations = 0
            while (
                self._should_continue_after_offer(user_input, result.get("messages", []))
                and offer_continuations < _MAX_RECOVERY_CONTINUATIONS
                and _time.monotonic() < _run_max_deadline
            ):
                offer_continuations += 1
                logger.info(
                    "Follow-through enforcement: continuation %d/%d (user=%s)",
                    offer_continuations, _MAX_RECOVERY_CONTINUATIONS, uid,
                )
                offer_messages = result.get("messages", []) + [
                    HumanMessage(content=self._offer_followthrough_nudge()),
                ]
                result = self._invoke_graph_once(
                    offer_messages, config, turn.user_id, heartbeat,
                )

            # Plan-ack guard (default-on): a plan-housekeeping ack must never be
            # the user-facing reply. Re-prompt once to answer the real request.
            if (
                settings.autonomy_followthrough_enabled
                and _PLAN_ACK_RE.match(self._last_ai_content(result.get("messages", [])).strip())
                and _time.monotonic() < _run_max_deadline
            ):
                logger.info(
                    "Plan-ack guard: housekeeping ack surfaced as reply; re-prompting (user=%s)", uid,
                )
                ack_messages = result.get("messages", []) + [
                    HumanMessage(content=self._plan_ack_recovery_nudge(user_input)),
                ]
                result = self._invoke_graph_once(
                    ack_messages, config, turn.user_id, heartbeat,
                )

            if _time.monotonic() >= _run_max_deadline:
                run_status = "timed_out"
                run_error_summary = f"Agent run exceeded {_run_max_timeout}s maximum wall-clock timeout."
                logger.warning(
                    "Agent run hit maximum wall-clock timeout (%ds) for user %s",
                    _run_max_timeout, uid,
                )
                try:
                    from prax.services.health_telemetry import EventCategory, Severity, record_event
                    record_event(
                        EventCategory.TURN_TIMEOUT, Severity.WARNING,
                        component="orchestrator",
                        details=f"Maximum timeout after {_run_max_timeout}s for user {uid}",
                    )
                except Exception:
                    pass

            self._rebuild_if_needed()
        except TimeoutError as exc:
            run_status = "timed_out"
            run_error_summary = str(exc)
            logger.error("Agent run timed out for user %s: %s", uid, exc)
            result = {
                "messages": messages + [
                    AIMessage(content=(
                        f"I hit a turn timeout while working on that request: {exc}. "
                        "I stopped waiting so the session "
                        "doesn't stay stuck. Please retry or ask me to continue from "
                        "the saved work."
                    )),
                ],
            }
        except Exception as exc:
            run_status = "failed"
            run_error_summary = f"{type(exc).__name__}: {str(exc)[:180]}"
            logger.exception("Agent run failed for user %s", uid)
            result = {
                "messages": messages + [
                    AIMessage(content=(
                        "I hit an internal error while working on that request. "
                        f"Error: {type(exc).__name__}: {str(exc)[:160]}"
                    )),
                ],
            }
        finally:
            # Keep a failed/timed-out turn resumable (opt-in) so the user can
            # continue from the failure point instead of restarting from scratch.
            keep = (
                settings.checkpoint_resume_enabled
                and run_status in ("failed", "timed_out")
            )
            self.checkpoint_mgr.end_turn(turn.user_id, keep_for_resume=keep)
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
            f"\n{run_status.replace('_', ' ').title()} ({_graph_cb._tool_count} tool calls)\n",
            status="completed" if run_status == "completed" else run_status,
        )
        log_activity(
            settings.agent_name,
            "task_completed" if run_status == "completed" else f"task_{run_status}",
            f"{run_status.replace('_', ' ').title()} with {_graph_cb._tool_count} tool calls",
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
                task_input=user_input,
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
            if _time.monotonic() >= _run_max_deadline:
                outcome_status = "timeout"
            elif run_status != "completed":
                outcome_status = run_status
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

        root_span.end(
            status=run_status,
            summary=(run_error_summary or response)[:200] if (run_error_summary or response) else "",
            tool_calls=_graph_cb._tool_count,
        )
        # Surface any "provider denylisted" heads-up queued during failover so the
        # user learns *why* the pool shrank (late bill, revoked key, lost access).
        return response + self._drain_denylist_notice()

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

    def has_resumable_turn(self, user_id: str) -> bool:
        """True if *user_id* has a failed/timed-out turn that can be resumed."""
        return self.checkpoint_mgr.has_resumable(user_id)

    def resume_last_turn(
        self, user_id: str, nudge: str = "Please continue from where you left off.",
    ) -> str | None:
        """Resume a previously failed/timed-out turn from its saved checkpoints.

        Continues the saved LangGraph thread (skipping completed steps) instead
        of restarting the turn.  Returns the agent's response, or None when
        there is nothing to resume.  Requires ``CHECKPOINT_RESUME_ENABLED``
        (otherwise failed turns aren't retained).
        """
        self._rebuild_if_needed()
        self._reset_to_primary_provider()
        turn = self.checkpoint_mgr.resume_turn(user_id)
        if turn is None:
            return None

        _cbs: list = []
        try:
            from prax.observability.callbacks import get_otel_callbacks
            _cbs.extend(get_otel_callbacks())
        except Exception:
            pass
        config = {
            **self.checkpoint_mgr.graph_config(turn),
            "recursion_limit": settings.agent_max_tool_calls,
            "callbacks": _cbs,
        }
        try:
            result = self._invoke_with_retry(
                [HumanMessage(content=f"[SYSTEM] {nudge}")], config, user_id,
            )
        finally:
            self.checkpoint_mgr.end_turn(user_id)

        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                return str(msg.content) + self._drain_denylist_notice()
        return self._drain_denylist_notice().lstrip("\n")

    def _invoke_with_retry(
        self,
        messages: list[BaseMessage],
        config: dict,
        user_id: str,
        heartbeat=None,
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
                return self._invoke_graph_once(messages, config, user_id, heartbeat)
            except Exception as exc:
                if isinstance(exc, TimeoutError):
                    raise

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

                # Multi-perspective error analysis for structured recovery.
                # The diagnosis is injected back into the message stream (when
                # enabled) so the model re-plans the current trajectory WITH the
                # diagnosis in context, instead of blindly re-running the step.
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
                    if settings.recovery_context_injection_enabled and recovery_ctx:
                        messages = list(messages) + [
                            HumanMessage(content=f"[SYSTEM — recovery guidance]\n{recovery_ctx}")
                        ]
                except Exception:
                    pass

                # Cross-provider failover: if this looks like a provider-side
                # failure and a fallback provider is configured, rebind the
                # graph to it and retry on a clean thread (opt-in).
                if self._maybe_failover(exc, user_id):
                    turn = self.checkpoint_mgr.get_turn(user_id)
                    if turn is not None:
                        import uuid
                        turn.thread_id = f"{user_id}:{uuid.uuid4().hex[:12]}"
                        config = {
                            **self.checkpoint_mgr.graph_config(turn),
                            "recursion_limit": settings.agent_max_tool_calls,
                            "callbacks": _callbacks,
                        }
                    continue

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

    def _invoke_graph_once(
        self,
        messages: list[BaseMessage],
        config: dict,
        user_id: str,
        heartbeat=None,
    ) -> dict:
        """Invoke LangGraph once with heartbeat-aware await timeouts.

        The earlier implementation used ``ThreadPoolExecutor`` as a context
        manager.  On timeout that still blocks in ``shutdown(wait=True)``, so a
        stuck provider/tool can leave the root orchestrator span ``running``
        forever.  This uses a daemon thread instead, but waits on an idle
        heartbeat rather than a pure wall-clock cutoff: healthy work can keep
        going until the maximum runtime cap.
        """
        import queue
        import threading
        import time as _time

        from prax.agent.trace import TraceHeartbeat

        result_q: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        heartbeat = heartbeat or TraceHeartbeat(trace_id=f"invoke-{str(user_id)[:12]}")
        heartbeat.touch("orchestrator", "graph invoke started")

        def _worker() -> None:
            try:
                # Bind the heartbeat for the in-loop LoopHeartbeat middleware.
                # ContextVars don't cross thread boundaries, so the binding
                # must happen here, inside the worker.  Inert unless
                # AGENT_MIDDLEWARE_ENABLED installed the middleware.
                from prax.agent.loop_middleware import use_heartbeat
                with use_heartbeat(heartbeat):
                    result_q.put(("ok", self.graph.invoke({"messages": messages}, config=config)))
            except Exception as exc:
                result_q.put(("error", exc))

        worker = threading.Thread(
            target=_worker,
            name=f"prax-graph-invoke-{str(user_id)[:12]}",
            daemon=True,
        )
        worker.start()
        idle_timeout = max(0.01, float(settings.agent_run_timeout))
        max_timeout = max(idle_timeout, float(settings.agent_run_max_timeout))
        poll_interval = min(1.0, max(0.01, idle_timeout / 5.0))
        notice_interval = max(60.0, idle_timeout / 2.0)
        last_notice_at = 0.0

        while worker.is_alive():
            worker.join(poll_interval)
            if not worker.is_alive():
                break

            snapshot = heartbeat.snapshot()
            elapsed = float(snapshot["elapsed_s"])
            idle_for = float(snapshot["idle_s"])

            if elapsed >= max_timeout:
                reason = (
                    f"agent run exceeded {int(max_timeout)}s maximum runtime "
                    f"(last activity {idle_for:.1f}s ago from "
                    f"{snapshot['last_source']}: {snapshot['last_message']})"
                )
                self._record_graph_timeout(user_id, reason, severity="ERROR")
                raise TimeoutError(reason)

            if idle_for >= idle_timeout:
                reason = (
                    f"agent run idle for {idle_for:.1f}s, exceeding "
                    f"{int(idle_timeout)}s idle timeout (last activity from "
                    f"{snapshot['last_source']}: {snapshot['last_message']})"
                )
                self._record_graph_timeout(user_id, reason, severity="ERROR")
                raise TimeoutError(reason)

            now = _time.monotonic()
            if elapsed >= idle_timeout and now - last_notice_at >= notice_interval:
                last_notice_at = now
                self._emit_still_working_notice(user_id, snapshot, idle_timeout, max_timeout)

        try:
            status, payload = result_q.get_nowait()
        except queue.Empty as exc:
            raise RuntimeError("graph.invoke worker exited without returning a result") from exc
        if status == "error":
            raise payload  # type: ignore[misc]
        return payload  # type: ignore[return-value]

    @staticmethod
    def _record_graph_timeout(user_id: str, reason: str, *, severity: str = "ERROR") -> None:
        logger.error("graph.invoke timeout for user %s: %s", user_id, reason)
        try:
            from prax.services.health_telemetry import (
                EventCategory,
                Severity,
                record_event,
            )
            level = Severity.ERROR if severity.upper() == "ERROR" else Severity.WARNING
            record_event(
                EventCategory.TURN_TIMEOUT,
                level,
                component="orchestrator",
                details=f"{reason} for user {user_id}",
            )
        except Exception:
            pass

    @staticmethod
    def _emit_still_working_notice(
        user_id: str,
        snapshot: dict,
        idle_timeout: float,
        max_timeout: float,
    ) -> None:
        """Post a sparse liveness note while a long turn keeps making progress."""
        try:
            from prax.services.teamwork_hooks import log_activity, push_live_output

            elapsed = float(snapshot["elapsed_s"])
            idle_for = float(snapshot["idle_s"])
            msg = (
                "Still working; recent activity is healthy. "
                f"Elapsed {elapsed:.0f}s, idle {idle_for:.0f}s/"
                f"{idle_timeout:.0f}s, max {max_timeout:.0f}s. "
                f"Last activity: {snapshot['last_source']} - "
                f"{snapshot['last_message']}"
            )
            push_live_output(settings.agent_name, msg + "\n", status="running")
            log_activity(settings.agent_name, "heartbeat", f"user={user_id}: {msg}")
        except Exception:
            pass

    @staticmethod
    def _audit_claims(
        response: str,
        messages: list,
        user_id: str | None,
        *,
        scheduled: bool = False,
        task_input: str = "",
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
                audit_artifact_location,
                audit_claims,
                audit_fabricated_links,
                audit_narrative_grounding,
                audit_plan_completion,
                audit_scheduled_task_grounding,
                audit_tool_failures,
                decide_scheduled_briefing_action,
                format_audit_warning,
            )
            from prax.agent.trajectory_audit import audit_trajectory_messages
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
            artifact_location = audit_artifact_location(task_input, response, messages)
            plan_mismatch = audit_plan_completion(response, messages)
            tool_failures = audit_tool_failures(response, tool_results)
            fabricated_links = audit_fabricated_links(response, tool_results)
            trifecta_trail = audit_trajectory_messages(messages)
            scheduled_grounding = (
                audit_scheduled_task_grounding(task_input, response, messages)
                if scheduled else None
            )

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
            if artifact_location:
                called = ", ".join(artifact_location["called_tools"]) or "(none)"
                flagged_parts.append(
                    "ARTIFACT-LOCATION WEAKNESS: user asked where a generated "
                    "artifact/link is, but artifact_locator was not called "
                    f"and no locator output was returned (called: {called})"
                )
            if plan_mismatch:
                flagged_parts.append(
                    f"PLAN-COMPLETION MISMATCH: response claims "
                    f"{plan_mismatch['completion_claim']!r} but "
                    f"{plan_mismatch['caveat_tool']} reply contained caveat "
                    f"{plan_mismatch['caveat_marker']!r} — the sub-agent "
                    f"said the work is partial and the response ignored it"
                )
            if tool_failures:
                fails = tool_failures["failures"]
                flagged_parts.append(
                    f"UNACKNOWLEDGED TOOL FAILURE: {len(fails)} tool/spoke call(s) "
                    f"CRASHED this turn (e.g. {fails[0]!r}) but the response claims "
                    f"success and discloses no technical failure — the spoke error "
                    f"was swallowed and must be surfaced to the user"
                )
            if fabricated_links:
                urls = fabricated_links["urls"]
                flagged_parts.append(
                    f"FABRICATED ARTIFACT LINK: response asserts {len(urls)} "
                    f"Prax link(s) that no tool produced (e.g. {urls[0]!r}) — the "
                    f"agent likely invented where it saved something; verify the "
                    f"link before presenting it"
                )
            if trifecta_trail:
                flagged_parts.append(
                    f"COMPLETED LETHAL TRIFECTA: an external sink "
                    f"({trifecta_trail['sink']}) fired after untrusted ingest "
                    f"({trifecta_trail['untrusted_source']}) + private read "
                    f"({trifecta_trail['private_data']}) — confirm this was "
                    f"user-intended, not injection-driven exfiltration"
                )
            if scheduled_grounding:
                called = ", ".join(scheduled_grounding["called_tools"]) or "(none)"
                requirements = "; ".join(scheduled_grounding["requirements"])
                flagged_parts.append(
                    f"SCHEDULED EVIDENCE FLOOR: {requirements} "
                    f"(called: {called})"
                )

            if flagged_parts:
                warning = "; ".join(flagged_parts)
                logger.warning("Claim audit flagged response (user=%s scheduled=%s): %s",
                               user_id, scheduled, warning)
                # Trend the guard firings so the inline checks become an
                # alertable production signal, not just a per-turn correction.
                _guard_types = []
                if findings:
                    _guard_types.append("numeric_claim")
                if narrative:
                    _guard_types.append("narrative")
                if artifact_location:
                    _guard_types.append("artifact_location")
                if plan_mismatch:
                    _guard_types.append("plan_completion")
                if scheduled_grounding:
                    _guard_types.append("scheduled_evidence_floor")
                try:
                    from prax.observability.metrics import HALLUCINATION_GUARD
                    for _gt in _guard_types:
                        HALLUCINATION_GUARD.labels(type=_gt).inc()
                except Exception:
                    pass
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

                # Scheduled-task policy: classify the failure rather than
                # blanket-suppressing.  Verified news + a "weather
                # unavailable" line is strictly more useful than throwing
                # away a real briefing because we couldn't resolve a city.
                if scheduled:
                    action = decide_scheduled_briefing_action(
                        narrative, scheduled_grounding,
                    )
                    if action == "suppress":
                        logger.error(
                            "BLOCKING scheduled-task response due to "
                            "unverified substantive content "
                            "(user=%s): %s", user_id, warning,
                        )
                        return (
                            "[Auto-suppressed scheduled briefing]\n\n"
                            "I couldn't verify the source data for today's "
                            "briefing — rather than send you made-up "
                            "content, I'm holding this one. Ask me for a "
                            "briefing when you have a moment and I'll try "
                            "again with live research."
                        )
                    if action == "weather_disclaimer":
                        logger.warning(
                            "Scheduled briefing missing weather only; "
                            "appending disclaimer (user=%s)", user_id,
                        )
                        return response.rstrip() + (
                            "\n\n_Weather is unavailable today — I "
                            "couldn't resolve a location for a live "
                            "forecast._"
                        )
                elif settings.claim_audit_attended_quarantine:
                    # Attended turn: surface the uncertainty to the user
                    # instead of only posting it to the internal Auditor channel.
                    _labels = {
                        "numeric_claim": "a figure I couldn't verify against tool output",
                        "narrative": "news/weather I couldn't confirm with a live source",
                        "artifact_location": "a file/link location I couldn't confirm",
                        "plan_completion": "a completion claim that may be only partial",
                        "scheduled_evidence_floor": "insufficiently-sourced content",
                    }
                    reasons = "; ".join(_labels.get(t, t) for t in _guard_types) or "unverified content"
                    response = response.rstrip() + (
                        f"\n\n_Heads up: a self-check flagged {reasons}. "
                        "Please double-check before relying on it._"
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
