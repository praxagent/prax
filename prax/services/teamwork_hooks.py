"""Fire-and-forget TeamWork hooks for role agent status and channel routing.

Every function here is a no-op when TeamWork is disabled or not connected.
Exceptions are caught and logged at DEBUG level — these hooks must never
break the core agent loop.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _tw():
    """Return the TeamWork client, or None if unavailable."""
    try:
        from prax.services.teamwork_service import get_teamwork_client
        tw = get_teamwork_client()
        if tw.enabled and tw.project_id:
            return tw
    except Exception:
        pass
    return None


def set_role_status(role_name: str, status: str) -> None:
    """Set a role agent's status (idle/working/offline)."""
    try:
        tw = _tw()
        if tw:
            tw.set_agent_status(role_name, status)
    except Exception:
        logger.debug("TeamWork hook: set_role_status(%s, %s) failed", role_name, status, exc_info=True)


def reset_all_idle() -> None:
    """Set all role agents to idle. Call at end of each agent turn."""
    from prax.settings import settings
    for role in (settings.agent_name, "Planner", "Researcher", "Executor", "Auditor"):
        set_role_status(role, "idle")


def post_to_channel(channel: str, content: str, agent_name: str | None = None) -> None:
    """Post a message to a TeamWork channel (by name, e.g. 'engineering').

    Spoke/sub-agent results are posted to their designated channels — NOT
    redirected to the DM.  Only the orchestrator's curated final response
    (sent directly via tw.send_message with an explicit channel_id) goes
    to the DM.  This prevents raw spoke dumps from cluttering the user's
    conversation and stealing message headers.
    """
    try:
        tw = _tw()
        if tw:
            tw.send_message(content=content, channel=channel, agent_name=agent_name)
    except Exception:
        logger.debug("TeamWork hook: post_to_channel(%s) failed", channel, exc_info=True)


def log_activity(
    agent_name: str,
    activity_type: str,
    description: str,
    extra_data: dict | None = None,
) -> None:
    """Create a persistent activity log entry in TeamWork.

    Unlike push_live_output (in-memory, ephemeral), activity logs are
    stored in the database and visible in the agent's Work Logs panel.
    """
    try:
        tw = _tw()
        if tw:
            tw.create_activity_log(agent_name, activity_type, description, extra_data)
    except Exception:
        logger.debug("TeamWork hook: log_activity(%s, %s) failed", agent_name, activity_type, exc_info=True)


def push_live_output(
    agent_name: str,
    output: str,
    status: str = "running",
    append: bool = True,
    error: str | None = None,
) -> None:
    """Push live execution output for an agent to the TeamWork frontend."""
    try:
        tw = _tw()
        if tw:
            tw.update_live_output(agent_name, output, status=status, append=append, error=error)
    except Exception:
        logger.debug("TeamWork hook: push_live_output(%s) failed", agent_name, exc_info=True)


def forward_to_channel(
    channel_name: str,
    sender_label: str,
    content: str,
    agent_name: str | None = None,
) -> None:
    """Forward an external-channel message (Discord, SMS) to a TeamWork channel.

    Posts to #discord or #sms so the user can see cross-channel conversations.
    """
    try:
        tw = _tw()
        if tw:
            tw.forward_external_message(channel_name, sender_label, content, agent_name=agent_name)
    except Exception:
        logger.debug("TeamWork hook: forward_to_channel(%s) failed", channel_name, exc_info=True)


def ensure_mirror_channels() -> None:
    """Ensure #discord and #sms channels exist in TeamWork.

    Called during startup / project initialization to backfill channels
    for projects created before mirroring was added.
    """
    try:
        tw = _tw()
        if tw:
            tw.ensure_channels([
                {"name": "discord", "description": "Mirrored conversations from Discord"},
                {"name": "sms", "description": "Mirrored conversations from SMS/Twilio"},
            ])
    except Exception:
        logger.debug("TeamWork hook: ensure_mirror_channels failed", exc_info=True)


def sync_conversation_history() -> None:
    """Sync historical SMS/Discord conversations to TeamWork.

    Called after ensure_mirror_channels on startup. Only imports into
    channels that have zero messages (safe to call repeatedly).
    """
    try:
        tw = _tw()
        if tw:
            result = tw.sync_conversation_history()
            if result:
                logger.info("Synced conversation history to TeamWork: %s", result)
    except Exception:
        logger.debug("TeamWork hook: sync_conversation_history failed", exc_info=True)


_AGENT_DISPLAY_NAMES = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
}

_AGENT_CHANNEL_DESCRIPTIONS = {
    "claude-code": "Live transcript of Prax ↔ Claude Code collaboration sessions",
    "codex": "Live transcript of Prax ↔ Codex collaboration sessions",
    "opencode": "Live transcript of Prax ↔ OpenCode collaboration sessions",
}

# Tracks which agent channels have been lazily created this process lifetime.
_ensured_agent_channels: set[str] = set()


def _ensure_agent_channel(tw, channel_name: str) -> None:
    """Lazily create a coding agent channel and bot identity on first use."""
    if channel_name in _ensured_agent_channels:
        return
    try:
        display = _AGENT_DISPLAY_NAMES.get(channel_name, channel_name)
        desc = _AGENT_CHANNEL_DESCRIPTIONS.get(channel_name, f"Prax ↔ {display} sessions")
        tw.ensure_channels([{"name": channel_name, "description": desc}])
        tw.create_agent(name=display, role="developer", soul=f"{display} — coding agent in sandbox")
        _ensured_agent_channels.add(channel_name)
    except Exception:
        logger.debug("TeamWork hook: _ensure_agent_channel(%s) failed", channel_name, exc_info=True)


def mirror_coding_agent_turn(
    channel: str,
    prax_message: str | None,
    agent_response: str | None,
    meta: str = "",
) -> None:
    """Mirror a Prax ↔ coding agent exchange to the agent's TeamWork channel.

    The channel (#claude-code, #codex, or #opencode) is created lazily
    on first call — it only appears in the sidebar when the tool is used.

    Args:
        channel: Channel name matching the agent (e.g. "claude-code").
        prax_message: What Prax sent (None to skip).
        agent_response: What the agent replied (None to skip).
        meta: Optional context line (e.g. "Session started: abc123").
    """
    try:
        tw = _tw()
        if not tw:
            return

        _ensure_agent_channel(tw, channel)
        display = _AGENT_DISPLAY_NAMES.get(channel, channel)

        if meta:
            tw.send_message(content=meta, channel=channel, agent_name="Prax")

        if prax_message:
            tw.send_message(content=prax_message, channel=channel, agent_name="Prax")

        if agent_response:
            tw.send_message(content=agent_response, channel=channel, agent_name=display)
    except Exception:
        logger.debug("TeamWork hook: mirror_coding_agent_turn(%s) failed", channel, exc_info=True)


def mirror_plan_to_tasks(goal: str, steps: list[dict]) -> list[str]:
    """Create TeamWork tasks mirroring an agent plan. Returns task IDs."""
    task_ids: list[str] = []
    try:
        tw = _tw()
        if not tw:
            return task_ids
        for s in steps:
            tid = tw.create_task(
                title=s.get("description", f"Step {s.get('step', '?')}"),
                assigned_to="Executor",
                status="pending",
            )
            if tid:
                task_ids.append(tid)
    except Exception:
        logger.debug("TeamWork hook: mirror_plan_to_tasks failed", exc_info=True)
    return task_ids
