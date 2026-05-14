"""Tests for the local-time display in mail/calendar table output."""

from __future__ import annotations

from datetime import datetime, timezone

from clerk.commands import calendar as cal_cmd
from clerk.commands import mail as mail_cmd
from clerk.models import Event


def test_mail_format_dt_utc_mode():
    dt = datetime(2026, 5, 13, 22, 26, tzinfo=timezone.utc)
    assert mail_cmd._format_dt(dt, utc=True) == "2026-05-13 22:26"


def test_mail_format_dt_local_mode_converts(monkeypatch):
    # Pretend local TZ is UTC-4 (EDT-ish).
    import time

    monkeypatch.setattr(time, "tzset", lambda: None, raising=False)
    monkeypatch.setenv("TZ", "America/New_York")
    try:
        import time as _t

        _t.tzset()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    dt = datetime(2026, 5, 13, 22, 26, tzinfo=timezone.utc)  # 22:26 UTC = 18:26 EDT
    result = mail_cmd._format_dt(dt, utc=False)
    # 22:26 UTC on 2026-05-13 in America/New_York (EDT, UTC-4) → 18:26 local
    assert result == "2026-05-13 18:26"


def test_mail_format_dt_handles_none():
    assert mail_cmd._format_dt(None, utc=False) == "????-??-?? ??:??"


def test_calendar_format_when_all_day_shows_date_only():
    event = Event(
        account_id="x",
        provider="microsoft",
        id="ev1",
        title="Vacation",
        start=datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc),
        is_all_day=True,
    )
    assert cal_cmd._format_event_when(event, utc=True) == "2026-05-13 (all day)"


def test_calendar_format_when_normal_event_utc():
    event = Event(
        account_id="x",
        provider="microsoft",
        id="ev1",
        title="Standup",
        start=datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 13, 15, 30, tzinfo=timezone.utc),
    )
    assert cal_cmd._format_event_when(event, utc=True) == "2026-05-13 15:00"
