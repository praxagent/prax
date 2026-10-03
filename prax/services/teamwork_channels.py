"""The TeamWork channels and role agents Prax posts as — one registry.

Prax posts to channels by *name*, and a post to a name the TeamWork project
does not have is dropped before it is sent.  TeamWork's project defaults cover
general, engineering, research, discord and sms, but the browser and content
spokes posted to ``#browser`` and ``#content``, which nothing ever created — so
every one of those posts was dropped behind an "Unknown channel" warning.
Likewise the spoke role agents were never registered, so their posts were
unattributed and their status updates were no-ops.

Startup ensures everything listed here (``teamwork_hooks.ensure_prax_channels``
and ``register_role_agents``), and ``tests/test_channel_registry.py`` fails CI
when code posts to a channel name or as a role name that is not listed.

Deliberately import-free so ``teamwork_service`` and ``teamwork_hooks`` can
both depend on it without a cycle.  Not listed: the coding-agent channels,
created lazily on first use (``teamwork_hooks._AGENT_DISPLAY_NAMES``), and the
generated ``branch-*`` channels.
"""
from __future__ import annotations

from typing import NamedTuple

# Channel name -> description.  The first five mirror TeamWork's project
# defaults (same descriptions), so ensuring them is a no-op on any project
# TeamWork created; they are listed because Prax posts to them too.
PRAX_CHANNELS: dict[str, str] = {
    "general": "Main conversation with the external agent",
    "engineering": "Agent-to-agent work conversations",
    "research": "Research and investigation",
    "discord": "Mirrored conversations from Discord",
    "sms": "Mirrored conversations from SMS/Twilio",
    "browser": "Results from the Browser Agent's web tasks",
    "content": "Content Editor drafting, review and publishing progress",
}


class RoleAgent(NamedTuple):
    name: str
    role: str
    soul: str


# Every role Prax posts as or reports status for, besides the orchestrator
# itself (registered from settings.agent_name).
PRAX_ROLE_AGENTS: tuple[RoleAgent, ...] = (
    RoleAgent("Planner", "planner", "Breaks complex requests into structured plans"),
    RoleAgent("Researcher", "researcher", "Investigates questions via web search and document analysis"),
    RoleAgent("Executor", "executor", "Executes tool calls and workspace operations"),
    RoleAgent("Auditor", "auditor", "Reviews claims for accuracy and audits governance logs"),
    # Spoke roles — the role_name each spoke passes to run_spoke, plus the
    # procedural pipelines that report under their own name.
    RoleAgent("Browser Agent", "browser", "Navigates the web and handles login flows in the sandbox browser"),
    RoleAgent("Content Editor", "content", "Runs the research, write, review and revise pipeline for long-form content"),
    RoleAgent("Desktop Agent", "desktop", "Operates GUI applications on the sandbox desktop"),
    RoleAgent("Environment", "environment", "Answers weather, local hazard and situational questions"),
    RoleAgent("Finetune Agent", "finetune", "Manages the LoRA fine-tuning pipeline"),
    RoleAgent("Note Editor", "notes", "Writes and reviews deep-dive notes"),
    RoleAgent("Plugin Agent", "plugins", "Runs end-user plugin tools"),
    RoleAgent("Sandbox Agent", "sandbox", "Writes and runs code in the sandbox container"),
    RoleAgent("Sysadmin", "sysadmin", "Manages plugins, configuration and self-maintenance"),
    RoleAgent("Tasks", "tasks", "Manages the todo list and the background task runner"),
    RoleAgent("Health Monitor", "monitor", "Reports health alerts to the activity log"),
)


def role_agent_names() -> list[str]:
    """Registered role-agent names, in order, without duplicates."""
    return list(dict.fromkeys(agent.name for agent in PRAX_ROLE_AGENTS))
