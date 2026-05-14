"""Tests for the calendar write path (create, update, cancel)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import respx

from clerk.providers import gmail, graph
from clerk.providers.base import EventPatch, OutgoingEvent


# ---------- Graph payload construction ----------


def test_graph_build_event_create_payload():
    out = OutgoingEvent(
        title="Standup",
        start=datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc),
        body="agenda",
        location="Room 1",
        attendees=["alice@example.com"],
    )
    payload = graph.build_event_create_payload(out)
    assert payload["subject"] == "Standup"
    assert payload["body"] == {"contentType": "text", "content": "agenda"}
    assert payload["start"] == {"dateTime": "2026-05-14T15:00:00", "timeZone": "UTC"}
    assert payload["end"] == {"dateTime": "2026-05-14T15:30:00", "timeZone": "UTC"}
    assert payload["location"] == {"displayName": "Room 1"}
    assert payload["attendees"] == [
        {"emailAddress": {"address": "alice@example.com"}, "type": "required"}
    ]


def test_graph_build_event_create_payload_omits_empty_fields():
    out = OutgoingEvent(
        title="x",
        start=datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc),
    )
    payload = graph.build_event_create_payload(out)
    assert "location" not in payload
    assert "attendees" not in payload


def test_graph_build_event_patch_payload_includes_only_set_fields():
    patch = EventPatch(title="renamed", location="new room")
    body = graph.build_event_patch_payload(patch)
    assert body == {
        "subject": "renamed",
        "location": {"displayName": "new room"},
    }


def test_graph_build_event_patch_empty_patch_is_empty():
    assert graph.build_event_patch_payload(EventPatch()) == {}


def test_graph_build_event_patch_can_clear_attendees():
    patch = EventPatch(attendees=[])  # explicit empty list
    body = graph.build_event_patch_payload(patch)
    assert body == {"attendees": []}


# ---------- Graph HTTP layer ----------


@respx.mock
def test_graph_create_event_posts_and_returns_id():
    respx.post(f"{graph.GRAPH_BASE}/me/events").mock(
        return_value=httpx.Response(201, json={"id": "ev-123"})
    )
    provider = graph.GraphCalendarProvider("microsoft:me@example.com", lambda: "TOK")
    out = OutgoingEvent(
        title="x",
        start=datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc),
    )
    assert provider.create_event(out) == "ev-123"


@respx.mock
def test_graph_update_event_uses_patch():
    route = respx.patch(f"{graph.GRAPH_BASE}/me/events/ev-1").mock(
        return_value=httpx.Response(200, json={})
    )
    provider = graph.GraphCalendarProvider("microsoft:me@example.com", lambda: "TOK")
    provider.update_event("ev-1", EventPatch(title="renamed"))
    assert route.called


@respx.mock
def test_graph_update_event_skips_http_when_patch_empty():
    route = respx.patch(f"{graph.GRAPH_BASE}/me/events/ev-1")
    provider = graph.GraphCalendarProvider("microsoft:me@example.com", lambda: "TOK")
    provider.update_event("ev-1", EventPatch())
    assert not route.called


@respx.mock
def test_graph_cancel_event_deletes():
    route = respx.delete(f"{graph.GRAPH_BASE}/me/events/ev-1").mock(
        return_value=httpx.Response(204)
    )
    provider = graph.GraphCalendarProvider("microsoft:me@example.com", lambda: "TOK")
    provider.cancel_event("ev-1")
    assert route.called


# ---------- Google payload construction ----------


def test_gcal_build_event_create_payload():
    out = OutgoingEvent(
        title="Standup",
        start=datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc),
        body="agenda",
        location="Room",
        attendees=["alice@example.com"],
    )
    payload = gmail.build_event_create_payload(out)
    assert payload["summary"] == "Standup"
    assert payload["description"] == "agenda"
    assert payload["location"] == "Room"
    assert payload["attendees"] == [{"email": "alice@example.com"}]
    assert "dateTime" in payload["start"]
    assert "dateTime" in payload["end"]


def test_gcal_build_event_create_payload_omits_attendees_when_none():
    out = OutgoingEvent(
        title="x",
        start=datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc),
    )
    payload = gmail.build_event_create_payload(out)
    assert "attendees" not in payload


def test_gcal_build_event_patch_payload():
    patch = EventPatch(title="renamed", body="new desc")
    body = gmail.build_event_patch_payload(patch)
    assert body == {"summary": "renamed", "description": "new desc"}


# ---------- Google HTTP layer ----------


@respx.mock
def test_gcal_create_event_with_no_attendees_uses_sendupdates_none():
    route = respx.post(f"{gmail.GCAL_BASE}/calendars/primary/events").mock(
        return_value=httpx.Response(200, json={"id": "ev-g"})
    )
    provider = gmail.GoogleCalendarProvider("google:me@example.com", lambda: "TOK")
    out = OutgoingEvent(
        title="x",
        start=datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc),
    )
    assert provider.create_event(out) == "ev-g"
    # Critical safety check: with no attendees, sendUpdates must be "none" so
    # nobody can possibly be notified (defense in depth).
    assert route.calls[0].request.url.params["sendUpdates"] == "none"


@respx.mock
def test_gcal_create_event_with_attendees_uses_sendupdates_all():
    route = respx.post(f"{gmail.GCAL_BASE}/calendars/primary/events").mock(
        return_value=httpx.Response(200, json={"id": "ev-g"})
    )
    provider = gmail.GoogleCalendarProvider("google:me@example.com", lambda: "TOK")
    out = OutgoingEvent(
        title="x",
        start=datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc),
        attendees=["alice@example.com"],
    )
    provider.create_event(out)
    assert route.calls[0].request.url.params["sendUpdates"] == "all"


@respx.mock
def test_gcal_update_event_uses_sendupdates_none():
    route = respx.patch(f"{gmail.GCAL_BASE}/calendars/primary/events/ev-1").mock(
        return_value=httpx.Response(200, json={})
    )
    provider = gmail.GoogleCalendarProvider("google:me@example.com", lambda: "TOK")
    provider.update_event("ev-1", EventPatch(title="renamed"))
    assert route.calls[0].request.url.params["sendUpdates"] == "none"


@respx.mock
def test_gcal_cancel_event_deletes_with_sendupdates_all():
    route = respx.delete(f"{gmail.GCAL_BASE}/calendars/primary/events/ev-1").mock(
        return_value=httpx.Response(204)
    )
    provider = gmail.GoogleCalendarProvider("google:me@example.com", lambda: "TOK")
    provider.cancel_event("ev-1")
    assert route.calls[0].request.url.params["sendUpdates"] == "all"
