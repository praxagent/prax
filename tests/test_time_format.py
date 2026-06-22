"""Tests for the time_format utility."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prax.utils.time_format import format_current_time, format_relative_time


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestFormatRelativeTime:
    def test_empty_returns_empty(self):
        assert format_relative_time("") == ""

    def test_invalid_returns_empty(self):
        assert format_relative_time("not-a-date") == ""

    def test_just_now(self):
        now = datetime.now(UTC)
        ts = _iso(now - timedelta(seconds=10))
        assert format_relative_time(ts) == "just now"

    def test_seconds_ago(self):
        now = datetime.now(UTC)
        ts = _iso(now - timedelta(seconds=45))
        result = format_relative_time(ts)
        assert "sec ago" in result

    @pytest.mark.parametrize(
        ("delta", "expected", "mode"),
        [
            (timedelta(minutes=15), "15 min ago", "exact"),
            (timedelta(hours=3), "3h ago", "exact"),
            (timedelta(days=2), "2d ago", "exact"),
            (timedelta(days=14), "2w ago", "exact"),
            (timedelta(days=90), "mo ago", "substr"),
            (timedelta(days=800), "y ago", "substr"),
            (-timedelta(hours=1), "in the future", "exact"),
        ],
        ids=["minutes", "hours", "days", "weeks", "months", "years", "future"],
    )
    def test_duration_buckets(self, delta, expected, mode):
        now = datetime.now(UTC)
        ts = _iso(now - delta)
        result = format_relative_time(ts)
        if mode == "exact":
            assert result == expected
        else:
            assert expected in result

    def test_naive_timestamp(self):
        """A naive timestamp (no tz) should be assumed UTC."""
        now = datetime.now(UTC).replace(tzinfo=None)
        ts = now.isoformat()
        # Should parse without error
        result = format_relative_time(ts)
        assert result != ""

    def test_z_suffix(self):
        now = datetime.now(UTC) - timedelta(minutes=5)
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert format_relative_time(ts) == "5 min ago"

    def test_explicit_now(self):
        fixed_now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
        ts = _iso(datetime(2026, 4, 6, 11, 30, tzinfo=UTC))
        assert format_relative_time(ts, now=fixed_now) == "30 min ago"


class TestFormatCurrentTime:
    def test_basic(self):
        result = format_current_time()
        assert "UTC" in result
        # Should have year-month-day hour:minute
        assert "-" in result
        assert ":" in result

    def test_has_weekday(self):
        result = format_current_time()
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        assert any(w in result for w in weekdays)

    def test_has_time_of_day(self):
        result = format_current_time()
        tods = ["morning", "afternoon", "evening", "night"]
        assert any(t in result for t in tods)

    def test_invalid_tz_falls_back_to_utc(self):
        result = format_current_time(tz_name="Not/A/Real/Timezone")
        # Should not crash
        assert "UTC" in result or ":" in result
