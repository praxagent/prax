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
from prax.services.workspace_service import append_trace, save_instructions
from prax.settings import settings

logger = logging.getLogger(__name__)

# Hardcoded fallback — used only if the prompt file is missing.
_FALLBACK_PROMPT = (
    "You are a warm, capable AI assistant. "
    "Hold casual conversations, answer questions accurately, and call tools when needed."
)


def _load_system_prompt() -> str:
    """Load the system prompt from the plugin prompts directory."""
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
    })
    return prompt or _FALLBACK_PROMPT


class ConversationAgent:
    """High-level orchestrator for conversational replies using tools."""

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        from prax.plugins.llm_config import get_component_config
        cfg = get_component_config("orchestrator")
        self.llm = build_llm(
            provider=provider or cfg.get("provider"),
            model=model or cfg.get("model"),
            temperature=temperature if temperature is not None else cfg.get("temperature"),
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

    def run(self, conversation: Iterable[BaseMessage], user_input: str, workspace_context: str = "") -> str:
        """Execute the agent graph and return the final string response."""
        # Register workspace plugins for the current user.
        uid = current_user_id.get()
        if uid:
            self._register_workspace_plugins(uid)

        # Rebuild graph if plugins changed since last invocation.
        self._rebuild_if_needed()

        history: list[BaseMessage] = list(conversation)
        logger.debug("Agent invoked with %d history messages", len(history))
        full_prompt = _load_system_prompt() + workspace_context

        # Persist instructions so the agent can re-read them mid-conversation.
        uid = current_user_id.get()
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
        config = self.checkpoint_mgr.graph_config(turn)

        try:
            result = self._invoke_with_retry(messages, config, turn.user_id)
        finally:
            self.checkpoint_mgr.end_turn(turn.user_id)

        # Write full agent trace to the user's workspace log.
        self._write_trace(uid, user_input, result.get("messages", []))

        # Extract the last AI message from the graph output.
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                return msg.content

        return ""

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
                    config = self.checkpoint_mgr.graph_config(turn)
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
                    config = self.checkpoint_mgr.graph_config(
                        self.checkpoint_mgr.get_turn(user_id),  # type: ignore[arg-type]
                    )
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
    def _write_trace(user_id: str | None, user_input: str, messages: list) -> None:
        """Append the full agent invocation trace to the workspace log."""
        if not user_id:
            return
        entries: list[dict] = [{"type": "user", "content": user_input}]
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
                        "type": "tool_call",
                        "content": f"{name}({args})",
                    })
                if msg.content:
                    entries.append({"type": "assistant", "content": msg.content})
            elif isinstance(msg, ToolMessage):
                entries.append({
                    "type": "tool_result",
                    "content": f"[{msg.name}] {msg.content}",
                })
        try:
            append_trace(user_id, entries)
        except Exception:
            logger.debug("Trace logging failed for %s", user_id, exc_info=True)
