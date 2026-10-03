"""The TeamWork channels Prax posts to, and the role agents it registers.

Prax posts to channels by *name*, and a post to a name the TeamWork project
does not have is dropped before it is sent.  TeamWork's project defaults cover
general, engineering, research, discord and sms, but the browser and content
spokes posted to ``#browser`` and ``#content``, which nothing ever created — so
every one of those posts was dropped behind an "Unknown channel" warning.

Startup ensures every channel listed here (``teamwork_hooks.ensure_prax_channels``),
and ``tests/test_channel_registry.py`` fails CI when code posts to a channel
name that is not listed.

The role agents are deliberately NOT every role Prax reports under.
``TeamWorkClient`` skips activity-log, live-output and status calls for an
agent that is not registered, so registering a role switches those sinks on
for it: a spoke role would stream its tool output — ``browser_login`` results
included — into TeamWork's persistent activity log, and ``reset_all_idle``
would send one more synchronous PATCH on every turn's critical path.  Spoke
roles (Browser Agent, Content Editor, …) therefore stay unregistered; their
channel posts still land, unattributed.  The drift guard pins this list.

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


# The internal role agents registered at startup, besides the orchestrator
# itself (registered from settings.agent_name).  Do not add spoke roles here:
# see the module docstring for what registering one switches on.
PRAX_ROLE_AGENTS: tuple[RoleAgent, ...] = (
    RoleAgent("Planner", "planner", "Breaks complex requests into structured plans"),
    RoleAgent("Researcher", "researcher", "Investigates questions via web search and document analysis"),
    RoleAgent("Executor", "executor", "Executes tool calls and workspace operations"),
    RoleAgent("Auditor", "auditor", "Reviews claims for accuracy and audits governance logs"),
)


def role_agent_names() -> list[str]:
    """Registered role-agent names, in order, without duplicates."""
    return list(dict.fromkeys(agent.name for agent in PRAX_ROLE_AGENTS))
