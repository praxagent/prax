"""LangChain tool wrappers for scheduled recurring messages."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.agent.user_context import current_user_id
from prax.services import scheduler_service


def _get_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        return "unknown"
    return uid


@risk_tool(risk=RiskLevel.HIGH)
def schedule_create(
    description: str,
    prompt: str,
    cron: str,
    timezone: str | None = None,
) -> str:
    """Create a recurring scheduled message sent via SMS.

    Args:
        description: Short name for the schedule (e.g. "French vocabulary").
        prompt: The instruction that will be sent to the AI agent each time the
            schedule fires.  The agent processes the prompt and sends the
            response as an SMS, so you can ask for creative or varied content.
        cron: Standard 5-field cron expression (minute hour day month weekday).
            Examples:
              "0 9,11,13,15,17 * * 1-5"  = 9am,11am,1pm,3pm,5pm weekdays
              "30 8 * * *"               = daily at 8:30 am
              "0 */3 * * 1-5"            = every 3 hours on weekdays
        timezone: IANA timezone like "America/New_York" or "Europe/London".
            If omitted, uses the user's default (initially UTC — set it with
            schedule_set_timezone).  IMPORTANT: always confirm the user's
            timezone before creating a schedule.
    """
    result = scheduler_service.create_schedule(
        _get_user_id(), description, prompt, cron, timezone=timezone,
    )
    if "error" in result:
        return f"Failed to create schedule: {result['error']}"
    s = result["schedule"]
    return (
        f"Schedule created: '{s['description']}' (id: {s['id']})\n"
        f"  Cron: {s['cron']}\n"
        f"  Timezone: {s['timezone']}\n"
        f"  Prompt: {s['prompt'][:80]}{'...' if len(s['prompt']) > 80 else ''}"
    )


@tool
def schedule_list() -> str:
    """List all recurring scheduled messages for the current user.

    Shows each schedule's id, description, cron pattern, timezone, enabled
    status, and the next/last fire times.
    """
    schedules = scheduler_service.list_schedules(_get_user_id())
    if not schedules:
        return "No schedules configured. Use schedule_create to set one up."

    lines = []
    for s in schedules:
        status = "enabled" if s.get("enabled", True) else "PAUSED"
        next_run = s.get("next_run", "unknown")
        last_run = s.get("last_run", "never")
        lines.append(
            f"**{s['id']}** [{status}]\n"
            f"  {s['description']}\n"
            f"  Cron: {s['cron']} ({s['timezone']})\n"
            f"  Prompt: {s['prompt'][:60]}{'...' if len(s['prompt']) > 60 else ''}\n"
            f"  Next: {next_run} | Last: {last_run}"
        )
    return "\n\n".join(lines)


@risk_tool(risk=RiskLevel.HIGH)
def schedule_update(
    schedule_id: str,
    description: str | None = None,
    prompt: str | None = None,
    cron: str | None = None,
    timezone: str | None = None,
    enabled: bool | None = None,
) -> str:
    """Update an existing schedule.  Only provided fields are changed.

    Use enabled=false to pause a schedule without deleting it.
    """
    result = scheduler_service.update_schedule(
        _get_user_id(), schedule_id,
        description=description, prompt=prompt, cron=cron,
        timezone=timezone, enabled=enabled,
    )
    if "error" in result:
        return f"Failed to update schedule: {result['error']}"
    return f"Schedule '{schedule_id}' updated: {result['updates']}"


@tool
def schedule_delete(schedule_id: str) -> str:
    """Delete a scheduled recurring message permanently."""
    result = scheduler_service.delete_schedule(_get_user_id(), schedule_id)
    if "error" in result:
        return f"Failed to delete: {result['error']}"
    return f"Schedule '{schedule_id}' deleted."


@tool
def schedule_set_timezone(timezone: str) -> str:
    """Set the default timezone for all of the user's schedules.

    Use IANA timezone names: "America/New_York", "America/Chicago",
    "America/Denver", "America/Los_Angeles", "Europe/London", "Asia/Tokyo", etc.
    IMPORTANT: Always confirm the user's timezone before setting it.
    """
    result = scheduler_service.set_user_timezone(_get_user_id(), timezone)
    if "error" in result:
        return f"Failed to set timezone: {result['error']}"
    return (
        f"Default timezone set to {result['timezone']}. "
        f"All new schedules will use this timezone unless overridden."
    )


@tool
def schedule_reload() -> str:
    """Reload schedules from the YAML file after a manual edit.

    If the user has edited schedules.yaml directly in their workspace,
    call this to pick up the changes without restarting the app.
    """
    result = scheduler_service.reload_schedules(_get_user_id())
    return f"Reloaded {result['count']} schedule(s) (timezone: {result['timezone']})"


@risk_tool(risk=RiskLevel.HIGH)
def schedule_reminder(
    description: str,
    prompt: str,
    fire_at: str,
    timezone: str | None = None,
) -> str:
    """Create a one-time reminder that fires at a specific date and time.

    After it fires, the reminder is automatically deleted.

    Args:
        description: Short name for the reminder (e.g. "Take medicine").
        prompt: The message or instruction the agent will process and send
            when the reminder fires.  For a simple reminder just repeat the
            user's request (e.g. "Remind the user to take their medicine").
        fire_at: ISO 8601 datetime string for when to fire, e.g.
            "2026-03-21T10:00:00".  If the user says "remind me tomorrow"
            without a specific time, pick a reasonable time (e.g. 10:00 AM
            in their timezone).  The datetime is interpreted in the user's
            timezone.
        timezone: IANA timezone like "America/New_York".  If omitted, uses the
            user's default.  Check user_notes for their timezone before creating
            a reminder — ask if unknown.
    """
    result = scheduler_service.create_reminder(
        _get_user_id(), description, prompt, fire_at, timezone=timezone,
    )
    if "error" in result:
        return f"Failed to create reminder: {result['error']}"
    r = result["reminder"]
    return (
        f"Reminder set: '{r['description']}' (id: {r['id']})\n"
        f"  Fire at: {r['fire_at']}\n"
        f"  Timezone: {r['timezone']}\n"
        f"  Prompt: {r['prompt'][:80]}{'...' if len(r['prompt']) > 80 else ''}"
    )


@tool
def reminder_list() -> str:
    """List all pending one-time reminders for the current user.

    Shows each reminder's id, description, fire time, and timezone.
    """
    reminders = scheduler_service.list_reminders(_get_user_id())
    if not reminders:
        return "No pending reminders. Use schedule_reminder to create one."

    lines = []
    for r in reminders:
        lines.append(
            f"**{r['id']}**\n"
            f"  {r['description']}\n"
            f"  Fire at: {r['fire_at']} ({r.get('timezone', 'UTC')})\n"
            f"  Prompt: {r['prompt'][:60]}{'...' if len(r['prompt']) > 60 else ''}"
        )
    return "\n\n".join(lines)


@tool
def reminder_delete(reminder_id: str) -> str:
    """Delete a pending one-time reminder before it fires."""
    result = scheduler_service.delete_reminder(_get_user_id(), reminder_id)
    if "error" in result:
        return f"Failed to delete: {result['error']}"
    return f"Reminder '{reminder_id}' deleted."


def build_scheduler_tools() -> list:
    return [
        schedule_create, schedule_list, schedule_update,
        schedule_delete, schedule_set_timezone, schedule_reload,
        schedule_reminder, reminder_list, reminder_delete,
    ]
