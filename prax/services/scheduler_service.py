"""Scheduled task service — cron-based recurring messages via YAML + APScheduler.

Each user has a ``schedules.yaml`` in their git-backed workspace that both the
agent and the user can edit.  APScheduler reads these definitions and fires SMS
messages on the configured cron cadence, always respecting the user's timezone.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from prax.settings import settings
from prax.sms import send_sms

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()
# {user_id: {schedule_id: apscheduler_job_id}}
_user_jobs: dict[str, dict[str, str]] = {}


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------

def _schedules_path(user_id: str) -> Path:
    from prax.services.workspace_service import workspace_root
    return Path(workspace_root(user_id)) / "schedules.yaml"


def _read_schedules(user_id: str) -> dict:
    path = _schedules_path(user_id)
    if not path.exists():
        return {"timezone": "UTC", "schedules": [], "reminders": []}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("timezone", "UTC")
    data.setdefault("schedules", [])
    data.setdefault("reminders", [])
    return data


def _write_schedules(user_id: str, data: dict, *, commit: bool = True) -> None:
    path = _schedules_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    if commit:
        _git_commit(user_id, "Update schedules.yaml")


def _git_commit(user_id: str, message: str) -> None:
    from prax.services.workspace_service import workspace_root
    repo_dir = Path(workspace_root(user_id))
    if not (repo_dir / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "add", "schedules.yaml"],
            cwd=repo_dir, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_dir, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_dir, capture_output=True, timeout=10,
        )
    except Exception:
        logger.warning("Failed to git-commit schedules for %s", user_id)


# ---------------------------------------------------------------------------
# Cron helpers
# ---------------------------------------------------------------------------

def _parse_cron(cron_expr: str) -> dict:
    """Parse a 5-field cron expression into APScheduler CronTrigger kwargs."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Expected 5-field cron expression (minute hour day month weekday), got {len(parts)}: '{cron_expr}'"
        )
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def _validate_timezone(tz_name: str) -> ZoneInfo:
    """Return a ZoneInfo or raise ValueError."""
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError) as exc:
        raise ValueError(f"Invalid timezone: {tz_name}") from exc


# ---------------------------------------------------------------------------
# Message delivery (SMS or Discord)
# ---------------------------------------------------------------------------

def _infer_channel(user_id: str) -> str:
    """Infer the delivery channel from the user_id format."""
    if user_id.startswith("D"):
        return "discord"
    if user_id.startswith("+") or user_id[0:1].isdigit():
        return "sms"
    # UUID — check linked identities
    from prax.services.identity_service import get_identities
    providers = {i["provider"] for i in get_identities(user_id)}
    if "discord" in providers:
        return "discord"
    return "sms"


def _resolve_cross_channel(user_id: str) -> tuple[str | None, str | None]:
    """Return (phone, discord_user_id) for a user, resolving across channels.

    Supports UUID user_ids via identity service, and legacy ``D{id}``/phone
    formats via the discord_to_phone mapping.
    """
    phone: str | None = None
    discord_id: str | None = None

    # UUID — resolve via identity service
    if not user_id.startswith("D") and not user_id.startswith("+") and not user_id[0:1].isdigit():
        from prax.services.identity_service import get_identities
        for identity in get_identities(user_id):
            if identity["provider"] == "sms":
                phone = identity["external_id"]
            elif identity["provider"] == "discord":
                discord_id = f"D{identity['external_id']}"
        return phone, discord_id

    if user_id.startswith("D"):
        discord_id = user_id
        try:
            from prax.services.discord_service import _discord_to_phone
            raw_id = user_id[1:]
            phone = _discord_to_phone.get(raw_id)
        except Exception:
            pass
    elif user_id.startswith("+") or user_id[0:1].isdigit():
        phone = user_id if user_id.startswith("+") else f"+{user_id}"
        try:
            from prax.services.discord_service import _discord_to_phone
            for d_id, p in _discord_to_phone.items():
                if p == phone or p == user_id:
                    discord_id = f"D{d_id}"
                    break
        except Exception:
            pass

    return phone, discord_id


def _deliver_message(user_id: str, message: str, channel: str | None = None) -> None:
    """Route a message to SMS, Discord, or both.

    Args:
        user_id: The user identifier (phone number or Discord ID).
        message: The message to send.
        channel: Delivery channel — "sms", "discord", or "all".
            If None, infers from user_id prefix.
    """
    channel = channel or _infer_channel(user_id)

    phone, discord_id = _resolve_cross_channel(user_id)

    if channel in ("discord", "all"):
        if discord_id:
            try:
                from prax.services.discord_service import send_message
                send_message(discord_id, message)
            except Exception:
                logger.exception("Failed to deliver via Discord to %s", discord_id)
        elif channel == "all":
            logger.warning("Cannot deliver via Discord — no Discord ID for user %s", user_id)

    if channel in ("sms", "all"):
        if phone:
            try:
                send_sms(message, phone)
            except Exception:
                logger.exception("Failed to deliver via SMS to %s", phone)
        elif channel == "all":
            logger.warning("Cannot deliver via SMS — no phone number for user %s", user_id)

    if channel in ("teamwork", "all"):
        try:
            from prax.services.teamwork_hooks import post_to_channel
            from prax.settings import settings
            post_to_channel("general", message, agent_name=settings.agent_name)
        except Exception:
            logger.exception("Failed to deliver via TeamWork")


# ---------------------------------------------------------------------------
# Schedule / reminder firing
# ---------------------------------------------------------------------------

def _on_fire(user_id: str, schedule_id: str, prompt: str, channel: str | None = None) -> None:
    """Called when a cron triggers.  Generate content via the agent, deliver.

    The prompt is processed through the agent so it can use tools (search,
    fetch, summarize, etc.).  The [SCHEDULED_TASK] prefix tells the agent
    not to ask follow-up questions and not to use scheduling tools.
    """
    logger.info("Schedule fired: user=%s id=%s channel=%s", user_id, schedule_id, channel)
    try:
        from prax.services.conversation_service import conversation_service

        response = conversation_service.reply(
            user_id,
            f"[SCHEDULED_TASK — CRITICAL RULES: "
            f"1) Do NOT ask follow-up questions — the user is not present. "
            f"2) Do NOT use schedule_create, schedule_reminder, or any scheduling tools. "
            f"3) Do NOT ask for confirmation or clarification. "
            f"4) Just execute the task using your best judgment and respond with the result. "
            f"5) If the task is ambiguous, take the most reasonable interpretation and do it. "
            f"6) Keep your response concise — it will be delivered as a notification.] "
            f"{prompt}",
        )
        _deliver_message(user_id, response, channel=channel)

        # Persist last_run (no git commit — this is housekeeping only).
        with _lock:
            data = _read_schedules(user_id)
            for s in data["schedules"]:
                if s["id"] == schedule_id:
                    tz_name = s.get("timezone", data.get("timezone", "UTC"))
                    s["last_run"] = datetime.now(ZoneInfo(tz_name)).isoformat()
                    break
            _write_schedules(user_id, data, commit=False)
    except Exception:
        logger.exception("Schedule fire failed: user=%s id=%s", user_id, schedule_id)


def _on_reminder_fire(
    user_id: str,
    reminder_id: str,
    prompt: str,
    channel: str | None = None,
) -> None:
    """Called when a one-time reminder fires.  Deliver and auto-delete.

    Unlike cron jobs (which run through the agent for complex tasks),
    reminders deliver the prompt directly — no agent processing.
    """
    logger.info("Reminder fired: user=%s id=%s channel=%s", user_id, reminder_id, channel)
    try:
        _deliver_message(user_id, f"\u23f0 Reminder: {prompt}", channel=channel)

        # Auto-delete the reminder from YAML.
        with _lock:
            data = _read_schedules(user_id)
            data["reminders"] = [r for r in data["reminders"] if r["id"] != reminder_id]
            _write_schedules(user_id, data)
    except Exception:
        logger.exception("Reminder fire failed: user=%s id=%s", user_id, reminder_id)


# ---------------------------------------------------------------------------
# APScheduler job management
# ---------------------------------------------------------------------------

def _register_job(user_id: str, schedule: dict, default_tz: str) -> str | None:
    """Register a single schedule with APScheduler.  Returns job_id or None."""
    if not schedule.get("enabled", True):
        return None
    if _scheduler is None:
        return None

    sched_id = schedule["id"]
    tz_name = schedule.get("timezone", default_tz)

    try:
        tz = _validate_timezone(tz_name)
        cron_kwargs = _parse_cron(schedule["cron"])
        trigger = CronTrigger(timezone=tz, **cron_kwargs)
    except (ValueError, Exception):
        logger.warning("Invalid schedule %s for user %s", sched_id, user_id)
        return None

    job = _scheduler.add_job(
        _on_fire,
        trigger=trigger,
        args=[user_id, sched_id, schedule["prompt"], schedule.get("channel")],
        id=f"{user_id}:{sched_id}",
        replace_existing=True,
        name=f"{user_id}:{schedule.get('description', sched_id)}",
    )
    return job.id


def _register_reminder_job(user_id: str, reminder: dict, default_tz: str) -> str | None:
    """Register a one-time reminder with APScheduler using DateTrigger."""
    if _scheduler is None:
        return None

    reminder_id = reminder["id"]
    tz_name = reminder.get("timezone", default_tz)

    try:
        tz = _validate_timezone(tz_name)
        fire_dt = datetime.fromisoformat(reminder["fire_at"])
        if fire_dt.tzinfo is None:
            fire_dt = fire_dt.replace(tzinfo=tz)
        trigger = DateTrigger(run_date=fire_dt)
    except (ValueError, Exception):
        logger.warning("Invalid reminder %s for user %s", reminder_id, user_id)
        return None

    channel = reminder.get("channel")  # None = infer from user_id

    job = _scheduler.add_job(
        _on_reminder_fire,
        trigger=trigger,
        args=[user_id, reminder_id, reminder["prompt"]],
        kwargs={"channel": channel},
        id=f"{user_id}:reminder:{reminder_id}",
        replace_existing=True,
        name=f"{user_id}:reminder:{reminder.get('description', reminder_id)}",
    )
    return job.id


def _sync_user_jobs(user_id: str) -> None:
    """Reconcile APScheduler jobs with the user's YAML file."""
    data = _read_schedules(user_id)
    default_tz = data.get("timezone", "UTC")

    # --- Recurring schedules ---
    old_jobs = _user_jobs.get(user_id, {})
    new_jobs: dict[str, str] = {}

    for schedule in data["schedules"]:
        sched_id = schedule["id"]
        job_id = _register_job(user_id, schedule, default_tz)
        if job_id:
            new_jobs[sched_id] = job_id

    # Remove jobs that are no longer in YAML.
    for sched_id, job_id in old_jobs.items():
        if sched_id not in new_jobs:
            try:
                _scheduler.remove_job(job_id)
            except Exception:
                pass

    _user_jobs[user_id] = new_jobs

    # --- One-time reminders ---
    for reminder in data["reminders"]:
        _register_reminder_job(user_id, reminder, default_tz)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _load_all_users() -> None:
    """Scan the workspaces dir and register jobs for every user with a schedules.yaml.

    Deduplicates by resolved path: if multiple directories (e.g., legacy
    phone-numbered dir + identity-migration symlinks pointing to it) resolve
    to the same canonical workspace, only the first one is processed. This
    prevents the same schedule from being registered N times — historically
    this caused N-fold duplicate SMS deliveries when migrate_legacy_users()
    accumulated stale symlinks across container rebuilds.
    """
    ws = Path(settings.workspace_dir)
    if not ws.exists():
        return
    seen_resolved: set[Path] = set()
    for user_dir in sorted(ws.iterdir()):
        if not user_dir.is_dir():
            continue
        if not (user_dir / "schedules.yaml").exists():
            continue
        try:
            resolved = user_dir.resolve()
        except OSError:
            continue
        if resolved in seen_resolved:
            logger.debug(
                "Skipping duplicate workspace path for scheduler: %s -> %s",
                user_dir.name, resolved,
            )
            continue
        seen_resolved.add(resolved)
        with _lock:
            _sync_user_jobs(user_dir.name)


def init_scheduler() -> None:
    """Initialize and start the background scheduler.  Call once at app startup."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.start()
    _load_all_users()
    logger.info("Scheduler started")


def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        _user_jobs.clear()


# ---------------------------------------------------------------------------
# Public CRUD API (called by agent tools)
# ---------------------------------------------------------------------------

def create_schedule(
    user_id: str,
    description: str,
    prompt: str,
    cron_expr: str,
    timezone: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """Create a new scheduled task."""
    try:
        _parse_cron(cron_expr)
    except ValueError as e:
        return {"error": str(e)}

    with _lock:
        data = _read_schedules(user_id)
        default_tz = data.get("timezone", "UTC")

        tz_name = timezone or default_tz
        try:
            _validate_timezone(tz_name)
        except ValueError:
            return {"error": f"Invalid timezone: {tz_name}"}

        # If caller provides a timezone and the file still has UTC default, adopt it.
        if timezone and data["timezone"] == "UTC":
            data["timezone"] = timezone

        # Deduplicate: if an enabled schedule with the same description and
        # cron already exists, return it instead of creating a duplicate.
        # Prax has historically created multiple "Daily morning briefing"
        # entries when users re-asked, causing duplicate SMS deliveries.
        desc_lower = description.strip().lower()
        for existing in data["schedules"]:
            if (
                existing.get("enabled", True)
                and existing.get("description", "").strip().lower() == desc_lower
                and existing.get("cron") == cron_expr
            ):
                return {
                    "status": "exists",
                    "schedule": existing,
                    "note": "A matching schedule already exists; not creating a duplicate.",
                }

        slug = description.lower().replace(" ", "-")[:20]
        sched_id = f"{slug}-{uuid.uuid4().hex[:6]}"

        entry = {
            "id": sched_id,
            "description": description,
            "prompt": prompt,
            "cron": cron_expr,
            "timezone": tz_name,
            "channel": channel or "all",
            "enabled": True,
            "created_at": datetime.now(ZoneInfo(tz_name)).isoformat(),
            "last_run": None,
        }
        data["schedules"].append(entry)
        _write_schedules(user_id, data)
        _sync_user_jobs(user_id)

    return {"status": "created", "schedule": entry}


def list_schedules(user_id: str) -> list[dict]:
    """List all schedules for a user, with next_run from APScheduler."""
    data = _read_schedules(user_id)
    default_tz = data.get("timezone", "UTC")
    result = []

    for s in data["schedules"]:
        tz_name = s.get("timezone", default_tz)
        entry = {**s, "timezone": tz_name}

        if _scheduler:
            job_id = f"{user_id}:{s['id']}"
            job = _scheduler.get_job(job_id)
            if job and job.next_run_time:
                entry["next_run"] = job.next_run_time.isoformat()
            else:
                entry["next_run"] = None
        result.append(entry)

    return result


def update_schedule(user_id: str, schedule_id: str, **kwargs) -> dict[str, Any]:
    """Update fields on an existing schedule."""
    allowed = {"description", "prompt", "cron", "timezone", "enabled"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return {"error": "No valid fields to update"}

    if "cron" in updates:
        try:
            _parse_cron(updates["cron"])
        except ValueError as e:
            return {"error": str(e)}

    if "timezone" in updates:
        try:
            _validate_timezone(updates["timezone"])
        except ValueError:
            return {"error": f"Invalid timezone: {updates['timezone']}"}

    with _lock:
        data = _read_schedules(user_id)
        found = False
        for s in data["schedules"]:
            if s["id"] == schedule_id:
                s.update(updates)
                found = True
                break
        if not found:
            return {"error": f"Schedule '{schedule_id}' not found"}

        _write_schedules(user_id, data)
        _sync_user_jobs(user_id)

    return {"status": "updated", "schedule_id": schedule_id, "updates": updates}


def delete_schedule(user_id: str, schedule_id: str) -> dict[str, Any]:
    """Delete a schedule."""
    with _lock:
        data = _read_schedules(user_id)
        original_len = len(data["schedules"])
        data["schedules"] = [s for s in data["schedules"] if s["id"] != schedule_id]
        if len(data["schedules"]) == original_len:
            return {"error": f"Schedule '{schedule_id}' not found"}

        _write_schedules(user_id, data)
        _sync_user_jobs(user_id)

    return {"status": "deleted", "schedule_id": schedule_id}


def reload_schedules(user_id: str) -> dict[str, Any]:
    """Reload schedules from YAML (e.g. after a manual edit)."""
    with _lock:
        _sync_user_jobs(user_id)
    data = _read_schedules(user_id)
    return {
        "status": "reloaded",
        "count": len(data["schedules"]),
        "timezone": data.get("timezone", "UTC"),
    }


def set_user_timezone(user_id: str, tz_name: str) -> dict[str, Any]:
    """Set the default timezone for a user's schedules."""
    try:
        _validate_timezone(tz_name)
    except ValueError:
        return {"error": f"Invalid timezone: {tz_name}"}

    with _lock:
        data = _read_schedules(user_id)
        data["timezone"] = tz_name
        _write_schedules(user_id, data)
        _sync_user_jobs(user_id)

    return {"status": "updated", "timezone": tz_name}


# ---------------------------------------------------------------------------
# Reminders (one-time)
# ---------------------------------------------------------------------------

def create_reminder(
    user_id: str,
    description: str,
    prompt: str,
    fire_at: str,
    timezone: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """Create a one-time reminder that fires at a specific datetime.

    Args:
        channel: Delivery channel — "sms", "discord", or "all".
            If None, defaults to the channel inferred from the user_id.
    """
    # Validate channel if provided.
    if channel and channel not in ("sms", "discord", "teamwork", "all"):
        return {"error": f"Invalid channel: {channel}. Use 'sms', 'discord', 'teamwork', or 'all'."}

    with _lock:
        data = _read_schedules(user_id)
        default_tz = data.get("timezone", "UTC")
        tz_name = timezone or default_tz

        try:
            tz = _validate_timezone(tz_name)
        except ValueError:
            return {"error": f"Invalid timezone: {tz_name}"}

        # If caller provides a timezone and the file still has UTC default, adopt it.
        if timezone and data["timezone"] == "UTC":
            data["timezone"] = timezone

        try:
            fire_dt = datetime.fromisoformat(fire_at)
            if fire_dt.tzinfo is None:
                fire_dt = fire_dt.replace(tzinfo=tz)
        except ValueError:
            return {"error": f"Invalid datetime format: {fire_at}"}

        if fire_dt <= datetime.now(tz):
            return {"error": "Reminder time must be in the future"}

        slug = description.lower().replace(" ", "-")[:20]
        reminder_id = f"rem-{slug}-{uuid.uuid4().hex[:6]}"

        # Default channel: infer from user_id (sms for phone, discord for D-prefix).
        effective_channel = channel or _infer_channel(user_id)

        entry: dict[str, Any] = {
            "id": reminder_id,
            "description": description,
            "prompt": prompt,
            "fire_at": fire_dt.isoformat(),
            "timezone": tz_name,
            "channel": effective_channel,
            "created_at": datetime.now(tz).isoformat(),
        }
        data["reminders"].append(entry)
        _write_schedules(user_id, data)
        _register_reminder_job(user_id, entry, tz_name)

    return {"status": "created", "reminder": entry}


def list_reminders(user_id: str) -> list[dict]:
    """List all pending one-time reminders for a user."""
    data = _read_schedules(user_id)
    reminders = data.get("reminders", [])

    # Enrich with next_run from APScheduler.
    if _scheduler:
        for r in reminders:
            job_id = f"{user_id}:reminder:{r['id']}"
            job = _scheduler.get_job(job_id)
            if job and job.next_run_time:
                r["next_run"] = job.next_run_time.isoformat()
            else:
                r["next_run"] = None

    return reminders


def update_reminder(user_id: str, reminder_id: str, **kwargs) -> dict[str, Any]:
    """Update fields on a pending one-time reminder.

    Accepts: description, prompt, fire_at, timezone, channel.
    Re-registers the APScheduler job if fire_at or timezone changed.
    """
    allowed = {"description", "prompt", "fire_at", "timezone", "channel"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return {"error": "No valid fields to update"}

    if "channel" in updates and updates["channel"] not in ("sms", "discord", "teamwork", "all"):
        return {"error": f"Invalid channel: {updates['channel']}"}

    if "timezone" in updates:
        try:
            _validate_timezone(updates["timezone"])
        except ValueError:
            return {"error": f"Invalid timezone: {updates['timezone']}"}

    with _lock:
        data = _read_schedules(user_id)
        default_tz = data.get("timezone", "UTC")

        reminder = None
        for r in data["reminders"]:
            if r["id"] == reminder_id:
                reminder = r
                break
        if reminder is None:
            return {"error": f"Reminder '{reminder_id}' not found"}

        # Validate fire_at if provided; normalize to isoformat with tz.
        if "fire_at" in updates:
            tz_name = updates.get("timezone", reminder.get("timezone", default_tz))
            try:
                tz = _validate_timezone(tz_name)
                fire_dt = datetime.fromisoformat(updates["fire_at"])
                if fire_dt.tzinfo is None:
                    fire_dt = fire_dt.replace(tzinfo=tz)
            except ValueError:
                return {"error": f"Invalid datetime format: {updates['fire_at']}"}
            if fire_dt <= datetime.now(tz):
                return {"error": "Reminder time must be in the future"}
            updates["fire_at"] = fire_dt.isoformat()

        reminder.update(updates)
        _write_schedules(user_id, data)

        # Re-register the APScheduler job so trigger/args pick up the changes.
        if _scheduler:
            try:
                _scheduler.remove_job(f"{user_id}:reminder:{reminder_id}")
            except Exception:
                pass
            _register_reminder_job(user_id, reminder, reminder.get("timezone", default_tz))

    return {"status": "updated", "reminder": reminder}


def delete_reminder(user_id: str, reminder_id: str) -> dict[str, Any]:
    """Delete a pending one-time reminder."""
    with _lock:
        data = _read_schedules(user_id)
        original_len = len(data["reminders"])
        data["reminders"] = [r for r in data["reminders"] if r["id"] != reminder_id]
        if len(data["reminders"]) == original_len:
            return {"error": f"Reminder '{reminder_id}' not found"}

        _write_schedules(user_id, data)

        # Remove APScheduler job (may already be gone if it fired).
        if _scheduler:
            try:
                _scheduler.remove_job(f"{user_id}:reminder:{reminder_id}")
            except Exception:
                pass

    return {"status": "deleted", "reminder_id": reminder_id}
