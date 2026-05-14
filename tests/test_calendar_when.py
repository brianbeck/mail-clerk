"""Tests for the calendar.parse_when() helper."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clerk.commands.calendar import parse_when

NOW = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def test_now():
    assert parse_when("now", now=NOW) == NOW


def test_today():
    assert parse_when("today", now=NOW) == datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc)


def test_tomorrow():
    assert parse_when("tomorrow", now=NOW) == datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)


def test_relative_days():
    assert parse_when("+7d", now=NOW) == NOW + timedelta(days=7)
    assert parse_when("-3d", now=NOW) == NOW - timedelta(days=3)


def test_relative_other_units():
    assert parse_when("+8h", now=NOW) == NOW + timedelta(hours=8)
    assert parse_when("+30m", now=NOW) == NOW + timedelta(minutes=30)
    assert parse_when("+2w", now=NOW) == NOW + timedelta(weeks=2)


def test_iso_date():
    assert parse_when("2026-06-01", now=NOW) == datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_iso_datetime_with_offset():
    result = parse_when("2026-06-01T09:00:00-04:00", now=NOW)
    assert result.utcoffset().total_seconds() == -4 * 3600
    assert result.hour == 9


def test_iso_datetime_without_offset_becomes_utc():
    result = parse_when("2026-06-01T09:00:00", now=NOW)
    assert result.tzinfo is not None
    assert result.utcoffset().total_seconds() == 0


def test_invalid():
    with pytest.raises(ValueError, match="Cannot parse"):
        parse_when("nope")
