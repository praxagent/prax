"""Human-friendly time formatting utilities.

Used to inject temporal context into the agent's system prompt so it can
distinguish fresh context from stale context.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO8601 timestamp, returning a timezone-aware datetime or None."""
    if not ts:
        return None
    try:
        # Handle trailing Z and missing timezone gracefully.
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def format_relative_time(ts: str, now: datetime | None = None) -> str:
    """Return a short relative-time string like 'just now', '5 min ago', '2 days ago'.

    Returns an empty string if the timestamp cannot be parsed.
    """
    dt = _parse_iso(ts)
    if dt is None:
        return ""

    now = now or datetime.now(UTC)
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 0:
        return "in the future"
    if seconds < 30:
        return "just now"
    if seconds < 60:
        return f"{seconds} sec ago"
    if seconds < 3600:
        mins = seconds // 60
        return f"{mins} min ago"
    if seconds < 86_400:
        hours = seconds // 3600
        return f"{hours}h ago"
    if seconds < 86_400 * 7:
        days = seconds // 86_400
        return f"{days}d ago"
    if seconds < 86_400 * 30:
        weeks = seconds // (86_400 * 7)
        return f"{weeks}w ago"
    if seconds < 86_400 * 365:
        months = seconds // (86_400 * 30)
        return f"{months}mo ago"
    years = seconds // (86_400 * 365)
    return f"{years}y ago"


def format_current_time(tz_name: str | None = None) -> str:
    """Return a human-friendly current-time string suitable for system prompts.

    Example: ``2026-04-06 22:54 UTC (Sunday afternoon)``
    """
    now = datetime.now(UTC)
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            now = now.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass

    weekday = now.strftime("%A")
    tod = _time_of_day(now.hour)
    tz_label = now.strftime("%Z") or "UTC"
    return f"{now.strftime('%Y-%m-%d %H:%M')} {tz_label} ({weekday} {tod})"


def _time_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"
