"""A restart that straddles a cron fire must not silently swallow it.

Found live 2026-08-30: a deploy restart at 22:59:52 UTC ate the 23:00:00 first
fire of a schedule created 30 minutes earlier. Jobs are re-added fresh at every
boot, so APScheduler computed the next fire from 23:00:14 and the 4 PM question
the user was waiting for never existed anywhere — no log, no error, nothing.

_missed_fire is the pure decision: the most recent scheduled time T within the
grace window counts as missed iff last_run < T. These tests drive it with real
CronTriggers and controlled clocks — no scheduler, no sleep, keyless.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from prax.services.scheduler_service import _missed_fire, _parse_cron

TZ = ZoneInfo("America/Los_Angeles")


def _trigger(cron: str) -> CronTrigger:
    return CronTrigger(timezone=TZ, **_parse_cron(cron))


def test_the_live_incident_is_caught():
    """Boot at 23:00:14 UTC (16:00:14 PT), fire was 16:00:00, never ran."""
    t = _trigger("0 9-20 * * *")
    now = datetime(2026, 8, 30, 16, 0, 14, tzinfo=TZ)
    missed = _missed_fire(t, None, now, grace_s=600)
    assert missed is not None
    assert missed.hour == 16 and missed.minute == 0


def test_no_catchup_when_the_fire_actually_ran():
    """If the process was up and fired, last_run >= T — never double-send."""
    t = _trigger("0 9-20 * * *")
    now = datetime(2026, 8, 30, 16, 0, 14, tzinfo=TZ)
    last_run = datetime(2026, 8, 30, 16, 0, 2, tzinfo=TZ).isoformat()
    assert _missed_fire(t, last_run, now, grace_s=600) is None


def test_no_catchup_when_nothing_fell_in_the_window():
    """Booting at 16:20 with a 10-minute grace: the 16:00 fire is too old.

    The window is deliberately bounded — catch-up exists for restart blips,
    not for replaying hours of downtime as a burst of messages.
    """
    t = _trigger("0 9-20 * * *")
    now = datetime(2026, 8, 30, 16, 20, 0, tzinfo=TZ)
    assert _missed_fire(t, None, now, grace_s=600) is None


def test_only_the_most_recent_missed_fire_is_returned():
    """A minutely cron missed 10 times returns ONE time (coalesce, not burst)."""
    t = _trigger("* * * * *")
    now = datetime(2026, 8, 30, 16, 10, 30, tzinfo=TZ)
    missed = _missed_fire(t, None, now, grace_s=600)
    assert missed is not None
    assert missed.minute == 10  # the latest one, not 16:01..16:09


def test_outside_the_schedules_hours_nothing_is_missed():
    """Restart at 3 AM: the 9-20 window has no fire near it."""
    t = _trigger("0 9-20 * * *")
    now = datetime(2026, 8, 30, 3, 5, 0, tzinfo=TZ)
    assert _missed_fire(t, None, now, grace_s=600) is None


def test_grace_zero_disables_catchup():
    t = _trigger("0 9-20 * * *")
    now = datetime(2026, 8, 30, 16, 0, 14, tzinfo=TZ)
    assert _missed_fire(t, None, now, grace_s=0) is None


def test_naive_last_run_is_compared_in_the_trigger_timezone():
    """last_run is written with the schedule's tz but must parse either way."""
    t = _trigger("0 9-20 * * *")
    now = datetime(2026, 8, 30, 16, 0, 14, tzinfo=TZ)
    naive_after = datetime(2026, 8, 30, 16, 0, 5).isoformat()  # no tzinfo
    assert _missed_fire(t, naive_after, now, grace_s=600) is None


def test_unparseable_last_run_prefers_firing():
    """A corrupt last_run must not silently suppress the catch-up."""
    t = _trigger("0 9-20 * * *")
    now = datetime(2026, 8, 30, 16, 0, 14, tzinfo=TZ)
    assert _missed_fire(t, "not-a-date", now, grace_s=600) is not None


def test_stale_last_run_from_the_previous_hour_does_not_block():
    """last_run at 15:00 (the previous fire) must not mask a missed 16:00."""
    t = _trigger("0 9-20 * * *")
    now = datetime(2026, 8, 30, 16, 0, 14, tzinfo=TZ)
    last_run = datetime(2026, 8, 30, 15, 0, 1, tzinfo=TZ).isoformat()
    missed = _missed_fire(t, last_run, now, grace_s=600)
    assert missed is not None and missed.hour == 16


def test_a_uuid_starting_with_a_digit_resolves_via_identity_not_twilio():
    """`90c2b48f-…` was classified as a PHONE because its first char is a digit.

    Scheduled deliveries for that user dialed Twilio with "+90c2b48f-…"
    (error 20404) and skipped Discord entirely, while both identity rows sat
    in the DB. Channel classification must be by LOOKUP, not string shape:
    only fully-numeric ids (or D/+ prefixes) are legacy.
    """
    from unittest.mock import patch

    from prax.services.scheduler_service import _resolve_cross_channel

    rows = [
        {"provider": "sms", "external_id": "+14155551234"},
        {"provider": "discord", "external_id": "1034618200000000000"},
    ]
    with patch("prax.services.identity_service.get_identities", return_value=rows):
        phone, discord = _resolve_cross_channel("90c2b48f-5744-52f5-a9e2-065f45306a2b")
    assert phone == "+14155551234"
    assert discord == "D1034618200000000000"


def test_fully_numeric_and_prefixed_ids_stay_on_the_legacy_path():
    from unittest.mock import patch

    from prax.services.scheduler_service import _resolve_cross_channel

    # If these hit the identity service the patch would return rows; they must not.
    with patch("prax.services.identity_service.get_identities",
               side_effect=AssertionError("legacy id must not hit identity")):
        p1, _ = _resolve_cross_channel("+15551234567")
        assert p1 == "+15551234567"
        _, d2 = _resolve_cross_channel("D123456789")
        assert d2 == "D123456789"
        p3, _ = _resolve_cross_channel("15551234567")
        assert p3 == "+15551234567"
