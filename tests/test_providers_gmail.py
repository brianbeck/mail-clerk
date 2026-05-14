"""Tests for the Gmail + Google Calendar provider (read path)."""

from __future__ import annotations

import base64
from datetime import date, datetime, timezone
from email.message import EmailMessage

import httpx
import respx

from clerk.providers import gmail
from clerk.search import SearchQuery


# ---------- pure helpers ----------


def test_build_gmail_query_full():
    q = SearchQuery(
        from_="alice@example.com",
        to="bob@example.com",
        subject="quarterly budget",
        after=date(2026, 1, 1),
        before=date(2026, 2, 1),
        text="reviews",
    )
    s = gmail.build_gmail_query(q)
    assert "from:alice@example.com" in s
    assert "to:bob@example.com" in s
    assert 'subject:"quarterly budget"' in s
    assert "after:2026/01/01" in s
    assert "before:2026/02/01" in s
    assert "reviews" in s


def test_build_gmail_query_empty():
    assert gmail.build_gmail_query(SearchQuery()) == ""


def test_parse_message_summary_from_metadata():
    payload = {
        "id": "msg1",
        "threadId": "thread1",
        "snippet": "Here are the figures",
        "labelIds": ["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "bob@example.com, carol@example.com"},
                {"name": "Subject", "value": "Quarterly budget"},
                {"name": "Date", "value": "Mon, 10 May 2026 14:30:00 +0000"},
            ]
        },
    }
    msg = gmail.parse_message_summary("google:me@example.com", payload)
    assert msg.id == "msg1"
    assert msg.thread_id == "thread1"
    assert msg.from_ == "Alice <alice@example.com>"
    assert msg.to == ["bob@example.com", "carol@example.com"]
    assert msg.subject == "Quarterly budget"
    assert msg.unread is True
    assert msg.snippet == "Here are the figures"
    assert msg.date == datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc)
    # CATEGORY_PERSONAL should be filtered out of tags.
    assert "INBOX" in msg.tags
    assert "UNREAD" in msg.tags
    assert all(not t.startswith("CATEGORY_") for t in msg.tags)


def test_parse_message_full_from_raw_text():
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Subject"] = "Test"
    msg["Date"] = "Mon, 10 May 2026 14:30:00 +0000"
    msg.set_content("hello world")

    raw = base64.urlsafe_b64encode(bytes(msg)).decode("ascii")
    payload = {
        "id": "msg1",
        "raw": raw,
        "labelIds": ["INBOX"],
        "snippet": "hello world",
    }
    parsed = gmail.parse_message_full("google:me@example.com", payload)
    assert parsed.body_text.strip() == "hello world"
    assert parsed.subject == "Test"
    assert parsed.from_ == "alice@example.com"


def test_parse_event_summary_with_datetime():
    payload = {
        "id": "ev1",
        "summary": "Standup",
        "start": {"dateTime": "2026-05-13T15:00:00-04:00"},
        "end": {"dateTime": "2026-05-13T15:30:00-04:00"},
        "location": "Meet",
        "organizer": {"email": "alice@example.com"},
    }
    event = gmail.parse_event_summary("google:me@example.com", payload)
    assert event.title == "Standup"
    assert event.is_all_day is False
    assert event.organizer == "alice@example.com"
    assert event.start.utcoffset().total_seconds() == -4 * 3600


def test_parse_event_summary_all_day():
    payload = {
        "id": "ev2",
        "summary": "Vacation",
        "start": {"date": "2026-05-13"},
        "end": {"date": "2026-05-14"},
    }
    event = gmail.parse_event_summary("google:me@example.com", payload)
    assert event.is_all_day is True


def test_parse_event_full_with_video_conference():
    payload = {
        "id": "ev1",
        "summary": "Standup",
        "description": "Daily sync",
        "start": {"dateTime": "2026-05-13T15:00:00+00:00"},
        "end": {"dateTime": "2026-05-13T15:30:00+00:00"},
        "attendees": [{"email": "x@example.com"}, {"email": "y@example.com"}],
        "conferenceData": {
            "entryPoints": [
                {"entryPointType": "video", "uri": "https://meet.google.com/abc"},
                {"entryPointType": "phone", "uri": "tel:+1234"},
            ]
        },
    }
    event = gmail.parse_event_full("google:me@example.com", payload)
    assert event.body_text == "Daily sync"
    assert event.online_meeting_url == "https://meet.google.com/abc"
    assert event.attendees == ["x@example.com", "y@example.com"]


# ---------- HTTP layer with respx ----------


@respx.mock
def test_search_makes_two_calls_per_result():
    respx.get(f"{gmail.GMAIL_BASE}/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "m1"}]})
    )
    respx.get(f"{gmail.GMAIL_BASE}/users/me/messages/m1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "snippet": "hi",
                "labelIds": [],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "alice@example.com"},
                        {"name": "Subject", "value": "hi"},
                    ]
                },
            },
        )
    )
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOKEN")
    results = provider.search(SearchQuery(text="hi"), 10)
    assert len(results) == 1
    assert results[0].subject == "hi"

    # First call is the list endpoint with q param.
    list_call = respx.calls[0]
    assert list_call.request.url.params["q"] == "hi"
    assert list_call.request.headers["Authorization"] == "Bearer TOKEN"


@respx.mock
def test_search_excludes_trash_by_default():
    route = respx.get(f"{gmail.GMAIL_BASE}/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    provider.search(SearchQuery(text="hi"), 5)
    req = route.calls[0].request
    assert "includeSpamTrash" not in req.url.params


@respx.mock
def test_search_with_include_trash_sets_param():
    route = respx.get(f"{gmail.GMAIL_BASE}/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    provider.search(SearchQuery(text="hi"), 5, include_trash=True)
    req = route.calls[0].request
    assert req.url.params["includeSpamTrash"] == "true"


@respx.mock
def test_calendar_list_passes_correct_params():
    respx.get(f"{gmail.GCAL_BASE}/calendars/primary/events").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    provider = gmail.GoogleCalendarProvider("google:me@example.com", lambda: "TOKEN")
    start = datetime(2026, 5, 13, tzinfo=timezone.utc)
    end = datetime(2026, 5, 20, tzinfo=timezone.utc)
    provider.list_events(start, end, 25)

    req = respx.calls[0].request
    assert req.url.params["timeMin"] == "2026-05-13T00:00:00Z"
    assert req.url.params["timeMax"] == "2026-05-20T00:00:00Z"
    assert req.url.params["singleEvents"] == "true"
    assert req.url.params["orderBy"] == "startTime"
    assert req.url.params["maxResults"] == "25"
