"""Tests for the Microsoft Graph provider (read path)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest
import respx

from clerk.providers import graph
from clerk.search import SearchQuery


# ---------- pure helpers ----------


def test_build_mail_search_params_with_all_fields():
    q = SearchQuery(
        from_="alice@example.com",
        to="bob@example.com",
        subject="budget",
        after=date(2026, 1, 1),
        before=date(2026, 2, 1),
        text="reviews",
    )
    params = graph.build_mail_search_params(q, 50)
    assert params["$top"] == "50"
    search_expr = params["$search"]
    assert search_expr.startswith('"') and search_expr.endswith('"')
    assert "from:alice@example.com" in search_expr
    assert "to:bob@example.com" in search_expr
    # Single-word subject does not get parenthesized.
    assert "subject:budget" in search_expr
    assert "received>=2026-01-01" in search_expr
    assert "received<=2026-02-01" in search_expr
    assert "reviews" in search_expr


def test_build_mail_search_params_multiword_subject_uses_parens():
    from clerk.search import SearchQuery as SQ
    q = SQ(subject="quarterly budget")
    expr = graph.build_mail_search_params(q, 10)["$search"]
    # Multi-word subjects must use KQL parentheses, not nested double-quotes.
    assert "subject:(quarterly budget)" in expr
    # Confirm we never produce nested double-quotes inside the outer $search quotes.
    inner = expr.strip('"')
    assert '"' not in inner


def test_build_mail_search_params_subject_with_brackets_falls_back_to_parens():
    from clerk.search import SearchQuery as SQ
    q = SQ(subject="[clerk-test DELETE] abc")
    expr = graph.build_mail_search_params(q, 10)["$search"]
    assert "subject:([clerk-test DELETE] abc)" in expr
    inner = expr.strip('"')
    assert '"' not in inner


def test_build_mail_search_params_empty_query_falls_back_to_orderby():
    params = graph.build_mail_search_params(SearchQuery(), 25)
    assert params["$top"] == "25"
    assert "$search" not in params
    assert params["$orderby"] == "receivedDateTime desc"


def test_parse_message_summary():
    payload = {
        "id": "AAMkAGI2",
        "conversationId": "thread-1",
        "subject": "Quarterly budget",
        "bodyPreview": "Here are the figures...",
        "isRead": False,
        "receivedDateTime": "2026-05-10T14:30:00Z",
        "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "toRecipients": [
            {"emailAddress": {"name": "Bob", "address": "bob@example.com"}},
            {"emailAddress": {"address": "carol@example.com"}},
        ],
        "categories": ["Finance"],
    }
    msg = graph.parse_message_summary("microsoft:me@example.com", payload)
    assert msg.id == "AAMkAGI2"
    assert msg.thread_id == "thread-1"
    assert msg.subject == "Quarterly budget"
    assert msg.from_ == "Alice <alice@example.com>"
    assert msg.to == ["Bob <bob@example.com>", "carol@example.com"]
    assert msg.unread is True
    assert msg.snippet == "Here are the figures..."
    assert msg.date == datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc)
    assert msg.tags == ["Finance"]


def test_parse_message_full_with_html_body():
    payload = {
        "id": "x",
        "subject": "hi",
        "isRead": True,
        "receivedDateTime": "2026-05-10T14:30:00Z",
        "from": {"emailAddress": {"address": "alice@example.com"}},
        "toRecipients": [],
        "ccRecipients": [{"emailAddress": {"address": "carol@example.com"}}],
        "body": {"contentType": "html", "content": "<p>hello</p>"},
    }
    msg = graph.parse_message_full("microsoft:me@example.com", payload)
    assert msg.body_html == "<p>hello</p>"
    assert msg.body_text == ""
    assert msg.cc == ["carol@example.com"]


def test_parse_event_summary_with_utc_dates():
    payload = {
        "id": "ev1",
        "subject": "Standup",
        "start": {"dateTime": "2026-05-13T15:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-05-13T15:30:00", "timeZone": "UTC"},
        "location": {"displayName": "Zoom"},
        "organizer": {"emailAddress": {"address": "alice@example.com"}},
    }
    event = graph.parse_event_summary("microsoft:me@example.com", payload)
    assert event.id == "ev1"
    assert event.title == "Standup"
    assert event.start == datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
    assert event.end == datetime(2026, 5, 13, 15, 30, tzinfo=timezone.utc)
    assert event.location == "Zoom"


def test_parse_event_full_with_attendees():
    payload = {
        "id": "ev1",
        "subject": "Standup",
        "start": {"dateTime": "2026-05-13T15:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-05-13T15:30:00", "timeZone": "UTC"},
        "body": {"content": "Notes here"},
        "attendees": [
            {"emailAddress": {"address": "x@example.com"}},
            {"emailAddress": {"address": "y@example.com"}},
        ],
        "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/x"},
    }
    event = graph.parse_event_full("microsoft:me@example.com", payload)
    assert event.attendees == ["x@example.com", "y@example.com"]
    assert event.body_text == "Notes here"
    assert event.online_meeting_url.startswith("https://teams")


# ---------- HTTP layer with respx ----------


@respx.mock
def test_search_makes_correct_request():
    route = respx.get(f"{graph.GRAPH_BASE}/me/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "subject": "hi",
                        "isRead": True,
                        "receivedDateTime": "2026-05-10T00:00:00Z",
                        "from": {"emailAddress": {"address": "alice@example.com"}},
                        "toRecipients": [],
                    }
                ]
            },
        )
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOKEN")
    results = provider.search(SearchQuery(text="hi"), 10)

    assert route.called
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer TOKEN"
    assert "$search" in req.url.params
    assert len(results) == 1
    assert results[0].id == "m1"


@respx.mock
def test_get_returns_full_message():
    payload = {
        "id": "m1",
        "subject": "hi",
        "isRead": True,
        "receivedDateTime": "2026-05-10T00:00:00Z",
        "from": {"emailAddress": {"address": "alice@example.com"}},
        "toRecipients": [],
        "body": {"contentType": "text", "content": "hello"},
    }
    respx.get(f"{graph.GRAPH_BASE}/me/messages/m1").mock(
        return_value=httpx.Response(200, json=payload)
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOKEN")
    msg = provider.get("m1")
    assert msg.body_text == "hello"


@respx.mock
def test_calendar_list_passes_time_window():
    respx.get(f"{graph.GRAPH_BASE}/me/calendarView").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    provider = graph.GraphCalendarProvider("microsoft:me@example.com", lambda: "TOKEN")
    start = datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 5, 20, 0, 0, tzinfo=timezone.utc)
    events = provider.list_events(start, end, 50)

    assert events == []
    req = respx.calls[0].request
    assert req.url.params["startDateTime"] == "2026-05-13T00:00:00Z"
    assert req.url.params["endDateTime"] == "2026-05-20T00:00:00Z"


@respx.mock
def test_search_propagates_http_errors():
    respx.get(f"{graph.GRAPH_BASE}/me/messages").mock(
        return_value=httpx.Response(401, json={"error": {"message": "unauth"}})
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOKEN")
    with pytest.raises(httpx.HTTPStatusError):
        provider.search(SearchQuery(text="hi"), 10)
