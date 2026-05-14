"""Tests for all-day events and recurring events (read + write)."""

from __future__ import annotations

from datetime import datetime, timezone

from clerk.providers import gmail, graph
from clerk.providers.base import EventPatch, OutgoingEvent


# ---------- all-day: Graph ----------


def test_graph_create_payload_all_day():
    out = OutgoingEvent(
        title="Vacation",
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 15, tzinfo=timezone.utc),
        is_all_day=True,
    )
    payload = graph.build_event_create_payload(out)
    assert payload["isAllDay"] is True
    # All-day events use midnight UTC for start/end.
    assert payload["start"]["dateTime"].endswith("T00:00:00")
    assert payload["end"]["dateTime"].endswith("T00:00:00")


def test_graph_patch_payload_all_day():
    patch = EventPatch(is_all_day=True)
    body = graph.build_event_patch_payload(patch)
    assert body["isAllDay"] is True


# ---------- all-day: Google ----------


def test_gcal_create_payload_all_day_uses_date_field():
    out = OutgoingEvent(
        title="Vacation",
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 15, tzinfo=timezone.utc),
        is_all_day=True,
    )
    payload = gmail.build_event_create_payload(out)
    assert payload["start"] == {"date": "2026-05-13"}
    assert payload["end"] == {"date": "2026-05-15"}
    assert "dateTime" not in payload["start"]


def test_gcal_create_payload_timed_uses_datetime_field():
    out = OutgoingEvent(
        title="Standup",
        start=datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 13, 15, 30, tzinfo=timezone.utc),
        is_all_day=False,
    )
    payload = gmail.build_event_create_payload(out)
    assert "dateTime" in payload["start"]
    assert "date" not in payload["start"]
    # Google requires timeZone for recurring events; we include it always.
    assert payload["start"]["timeZone"] == "UTC"
    assert payload["end"]["timeZone"] == "UTC"


# ---------- recurrence: Google (RRULE pass-through) ----------


def test_gcal_create_payload_with_recurrence():
    out = OutgoingEvent(
        title="Weekly review",
        start=datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 13, 15, 30, tzinfo=timezone.utc),
        recurrence_rule="FREQ=WEEKLY;BYDAY=MO;COUNT=10",
    )
    payload = gmail.build_event_create_payload(out)
    assert payload["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=10"]


def test_gcal_patch_clears_recurrence_with_empty_string():
    body = gmail.build_event_patch_payload(EventPatch(recurrence_rule=""))
    assert body["recurrence"] == []


# ---------- recurrence: Graph RRULE → structured ----------


def test_rrule_to_graph_weekly_with_byday_and_count():
    start = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
    result = graph.rrule_to_graph("FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=10", start)
    assert result["pattern"]["type"] == "weekly"
    assert result["pattern"]["interval"] == 1
    assert result["pattern"]["daysOfWeek"] == ["monday", "wednesday", "friday"]
    assert result["range"] == {
        "type": "numbered",
        "startDate": "2026-05-13",
        "numberOfOccurrences": 10,
    }


def test_rrule_to_graph_daily_no_end():
    start = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
    result = graph.rrule_to_graph("FREQ=DAILY", start)
    assert result["pattern"]["type"] == "daily"
    assert result["range"]["type"] == "noEnd"


def test_rrule_to_graph_monthly_uses_start_day_when_no_bymonthday():
    start = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
    result = graph.rrule_to_graph("FREQ=MONTHLY", start)
    assert result["pattern"]["type"] == "monthly"
    assert result["pattern"]["dayOfMonth"] == 13


def test_rrule_to_graph_yearly_uses_start_month_and_day():
    start = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
    result = graph.rrule_to_graph("FREQ=YEARLY", start)
    assert result["pattern"]["type"] == "yearly"
    assert result["pattern"]["month"] == 5
    assert result["pattern"]["dayOfMonth"] == 13


def test_rrule_to_graph_until_translates_to_enddate():
    start = datetime(2026, 5, 13, tzinfo=timezone.utc)
    result = graph.rrule_to_graph("FREQ=WEEKLY;UNTIL=20261231T235959Z", start)
    assert result["range"] == {
        "type": "endDate",
        "startDate": "2026-05-13",
        "endDate": "2026-12-31",
    }


def test_rrule_to_graph_interval_passed_through():
    start = datetime(2026, 5, 13, tzinfo=timezone.utc)
    result = graph.rrule_to_graph("FREQ=DAILY;INTERVAL=3", start)
    assert result["pattern"]["interval"] == 3


def test_graph_create_payload_with_recurrence():
    out = OutgoingEvent(
        title="Weekly",
        start=datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 13, 15, 30, tzinfo=timezone.utc),
        recurrence_rule="FREQ=WEEKLY;BYDAY=WE;COUNT=4",
    )
    payload = graph.build_event_create_payload(out)
    assert "recurrence" in payload
    assert payload["recurrence"]["pattern"]["type"] == "weekly"


# ---------- recurring read: master id ----------


def test_graph_parse_event_summary_extracts_seriesmasterid():
    payload = {
        "id": "occurrence-1",
        "subject": "Standup",
        "start": {"dateTime": "2026-05-13T15:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-05-13T15:30:00", "timeZone": "UTC"},
        "seriesMasterId": "master-123",
    }
    event = graph.parse_event_summary("microsoft:me@example.com", payload)
    assert event.recurring_master_id == "master-123"


def test_graph_parse_event_summary_no_master_when_standalone():
    payload = {
        "id": "ev-1",
        "subject": "One-off",
        "start": {"dateTime": "2026-05-13T15:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-05-13T15:30:00", "timeZone": "UTC"},
    }
    event = graph.parse_event_summary("microsoft:me@example.com", payload)
    assert event.recurring_master_id is None


def test_gcal_parse_event_summary_extracts_recurring_event_id():
    payload = {
        "id": "instance-1",
        "summary": "Standup",
        "start": {"dateTime": "2026-05-13T15:00:00+00:00"},
        "end": {"dateTime": "2026-05-13T15:30:00+00:00"},
        "recurringEventId": "master-google-456",
    }
    event = gmail.parse_event_summary("google:me@example.com", payload)
    assert event.recurring_master_id == "master-google-456"
