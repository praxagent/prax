# Scheduler

[← Guides](README.md)

Prax has a built-in scheduler for recurring cron jobs and one-time reminders. Jobs are stored in a YAML file in the user's workspace and executed by APScheduler.

## How it works

```
schedules.yaml (durable store)
       ↕
scheduler_service.py (CRUD + validation)
       ↕
APScheduler (in-memory runtime)
       ↓ fires
conversation_service.reply() → _deliver_message()
       ↓
SMS / Discord / TeamWork (based on channel setting)
```

**Single source of truth:** `{workspace}/{user_id}/schedules.yaml`. This file is git-tracked — every change is committed automatically.

**Two ways to manage schedules:**

1. **Via chat** — Ask Prax: "Remind me every weekday at 9am to check my email." He uses `schedule_create`, `schedule_update`, `schedule_delete`, `schedule_reminder` tools.
2. **Via TeamWork UI** — Click the Timer icon in the left rail to open the Scheduler panel. Create, edit, pause, resume, and delete jobs directly.

Both paths call the same `scheduler_service` functions. The YAML file and APScheduler stay in sync regardless of how the change was made.

## schedules.yaml format

```yaml
timezone: America/New_York
schedules:
  - id: morning-briefing-a1b2c3
    description: Morning briefing
    prompt: Check my email and calendar, summarize what needs attention today.
    cron: 0 9 * * 1-5
    timezone: America/New_York
    channel: all
    enabled: true
    created_at: '2026-04-03T09:00:00-04:00'
    last_run: '2026-04-03T09:00:12-04:00'
reminders:
  - id: rem-dentist-d4e5f6
    description: Call dentist
    prompt: Remind me to call the dentist to reschedule my appointment.
    fire_at: '2026-04-05T14:00:00-04:00'
    timezone: America/New_York
    channel: all
```

## Cron expressions

Standard 5-field format: `minute hour day month weekday`

| Expression | Meaning |
|-----------|---------|
| `0 9 * * 1-5` | Weekdays at 9:00 AM |
| `30 8 * * *` | Daily at 8:30 AM |
| `0 */3 * * *` | Every 3 hours |
| `0 9,17 * * 1-5` | Weekdays at 9 AM and 5 PM |
| `0 10 * * 1` | Every Monday at 10 AM |

The TeamWork Scheduler panel has a friendly builder with dropdowns — no cron knowledge needed.

## Channels

Each job specifies where the response is delivered:

| Channel | Behavior |
|---------|----------|
| `all` | Delivers to SMS + Discord + TeamWork #general (default) |
| `sms` | SMS only |
| `discord` | Discord only |
| `teamwork` | Posts to TeamWork #general only |

If a channel isn't configured (e.g., no Discord bot token), delivery silently skips that channel with a log warning.

## Timezone

- **Default timezone** is stored at the top of `schedules.yaml` and in Settings → Timezone.
- Individual jobs can override the default with their own `timezone` field.
- Use IANA names: `America/New_York`, `Europe/London`, `Asia/Tokyo`, etc.
- The TeamWork UI provides a timezone picker with common options grouped by region.

## Validation

All inputs are validated before writing to YAML:

- **Cron expression**: parsed and validated — rejects invalid syntax
- **Timezone**: validated via `zoneinfo.ZoneInfo()` — rejects unknown names
- **Channel**: must be one of `sms`, `discord`, `teamwork`, `all`
- **Operations are thread-safe**: all reads/writes happen under a threading lock

If validation fails, the function returns `{"error": "..."}` — nothing is written.

## What happens when a job fires

1. APScheduler triggers the callback
2. The prompt is sent through `conversation_service.reply()` — Prax processes it like a regular user message (can use tools, delegate, etc.)
3. The response is delivered via `_deliver_message()` to the configured channel(s)
4. `last_run` is updated in the YAML (without a git commit — housekeeping only)

## Reminders

One-time reminders work the same way but use `DateTrigger` instead of `CronTrigger`. After firing, they auto-delete from the YAML file.

## Manual editing

You can edit `schedules.yaml` directly in the workspace (via the file browser or terminal). After editing, either:
- Restart the app (`docker compose restart app`)
- Or ask Prax to reload: "reload my schedules"

Prax calls `schedule_reload` which re-syncs APScheduler with the YAML.

## Agent tools

| Tool | Description |
|------|-------------|
| `schedule_create` | Create a recurring cron job |
| `schedule_list` | List all schedules with next/last run times |
| `schedule_update` | Update fields (description, prompt, cron, timezone, enabled) |
| `schedule_delete` | Delete a schedule permanently |
| `schedule_set_timezone` | Set the user's default timezone |
| `schedule_reload` | Re-sync APScheduler after manual YAML edits |
| `schedule_reminder` | Create a one-time reminder |
| `reminder_list` | List pending reminders |
| `reminder_delete` | Delete a pending reminder |

## TeamWork API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/teamwork/schedules` | List all schedules and reminders |
| POST | `/teamwork/schedules` | Create a cron schedule |
| PATCH | `/teamwork/schedules/{id}` | Update a schedule |
| DELETE | `/teamwork/schedules/{id}` | Delete a schedule |
| POST | `/teamwork/reminders` | Create a one-time reminder |
| DELETE | `/teamwork/reminders/{id}` | Delete a reminder |
| GET | `/teamwork/timezone` | Get user's default timezone |
| PUT | `/teamwork/timezone` | Set user's default timezone |
