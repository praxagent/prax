"""Scheduler spoke agent — cron jobs, reminders, timezone management."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

SYSTEM_PROMPT = """\
You are the Scheduler Agent for {agent_name}. You manage recurring cron
jobs and one-time reminders.

## Rules
- ALWAYS confirm the user's timezone before creating a schedule (check
  user notes or ask).
- Use standard 5-field cron expressions: minute hour day month weekday.
- For reminders, use ISO 8601 datetime format.
- Channel options: "all" (default), "sms", "discord", "teamwork".
- Don't ask follow-up questions — use your best judgment.
- Keep descriptions short and clear.

## Tools
- schedule_create / schedule_list / schedule_update / schedule_delete
- schedule_set_timezone / schedule_reload
- schedule_reminder / reminder_list / reminder_delete

Execute the task and report back concisely.
"""


def build_tools() -> list:
    """Return all tools available to the scheduler spoke."""
    from prax.agent.scheduler_tools import build_scheduler_tools

    return build_scheduler_tools()


@tool
def delegate_scheduler(task: str) -> str:
    """Delegate a scheduling task to the Scheduler Agent.

    The Scheduler Agent manages recurring cron jobs and one-time reminders.
    Use this for:
    - "Remind me at 3pm to call the dentist"
    - "Schedule a daily briefing at 9am on weekdays"
    - "List my schedules"
    - "Delete the morning briefing schedule"
    - "Set my timezone to America/New_York"
    - "Pause the news digest job"

    Args:
        task: Description of the scheduling task.
    """
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_scheduler",
        role_name=None,
        channel=None,
        recursion_limit=10,
    )


def build_spoke_tools() -> list:
    return [delegate_scheduler]
