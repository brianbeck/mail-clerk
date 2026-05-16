"""Tests for the MCP server (in-process, no stdio transport)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from clerk import mcp_server, tokens
from clerk.config import Account, load_accounts, save_accounts
from clerk.models import Event, Message
from clerk.providers.base import EventPatch, OutgoingEvent, OutgoingMessage


def _seed_accounts():
    reg = load_accounts()
    reg.accounts = [
        Account(
            id="microsoft:alice@example.com",
            provider="microsoft",
            email="alice@example.com",
            display_name="Alice",
        ),
        Account(
            id="google:bob@example.com",
            provider="google",
            email="bob@example.com",
            display_name="Bob",
        ),
    ]
    save_accounts(reg)


@pytest.fixture
def with_write_token(monkeypatch, isolated_config):
    _seed_accounts()
    created = tokens.create(["mail:read", "mail:write", "calendar:read", "calendar:write"])
    monkeypatch.setenv("CLERK_TOKEN", created.raw)
    return created


def test_all_expected_tools_are_registered():
    registered = set(mcp_server.mcp._tool_manager._tools.keys())
    expected = {
        "accounts_list",
        "mail_search",
        "mail_read",
        "mail_send",
        "mail_reply",
        "mail_delete",
        "calendar_list",
        "calendar_get",
        "calendar_create",
        "calendar_update",
        "calendar_cancel",
    }
    assert expected <= registered, f"missing tools: {expected - registered}"


def test_accounts_list_returns_metadata(with_write_token):
    result = mcp_server.accounts_list()
    assert len(result) == 2
    emails = {a["email"] for a in result}
    assert emails == {"alice@example.com", "bob@example.com"}


def test_mail_search_denied_without_token(monkeypatch, isolated_config):
    _seed_accounts()
    monkeypatch.delenv("CLERK_TOKEN", raising=False)
    with pytest.raises(PermissionError, match="capability token is required"):
        mcp_server.mail_search(query="hi")


def test_mail_search_scope_mismatch_raises(monkeypatch, isolated_config):
    _seed_accounts()
    created = tokens.create(["calendar:read"])
    monkeypatch.setenv("CLERK_TOKEN", created.raw)
    with pytest.raises(PermissionError, match="lacks the required scope"):
        mcp_server.mail_search(query="hi")


def test_mail_search_dispatches_to_providers(with_write_token):
    fake_msg = Message(
        account_id="microsoft:alice@example.com",
        provider="microsoft",
        id="m1",
        subject="hi",
        date=datetime(2026, 5, 10, tzinfo=timezone.utc),
        **{"from": "alice@example.com"},
    )

    class FakeProvider:
        def __init__(self, account_id):
            self.account_id = account_id

        def search(self, query, limit, include_trash=False, include_body=False):
            return [fake_msg] if self.account_id == fake_msg.account_id else []

    with patch(
        "clerk.providers.factory.mail_provider",
        side_effect=lambda a, c: FakeProvider(a.id),
    ):
        results = mcp_server.mail_search(query="", limit=5)

    assert len(results) == 1
    assert results[0]["id"] == "m1"


def test_mail_send_calls_provider_with_outgoing_message(with_write_token):
    fake = MagicMock()
    fake.send.return_value = "sent-id"

    with patch("clerk.providers.factory.mail_provider", return_value=fake):
        result = mcp_server.mail_send(
            account="alice@example.com",
            to=["alice@example.com"],
            subject="self-test",
            body="hi",
        )

    assert result == {"ok": True, "id": "sent-id"}
    out: OutgoingMessage = fake.send.call_args.args[0]
    assert out.to == ["alice@example.com"]
    assert out.subject == "self-test"


def test_mail_delete_invokes_provider(with_write_token):
    fake = MagicMock()
    with patch("clerk.providers.factory.mail_provider", return_value=fake):
        result = mcp_server.mail_delete(message_id="m1", account="alice@example.com")
    assert result == {"ok": True}
    fake.delete.assert_called_once_with("m1")


def test_calendar_list_parses_relative_times(with_write_token):
    fake_event = Event(
        account_id="microsoft:alice@example.com",
        provider="microsoft",
        id="ev1",
        title="Standup",
        start=datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc),
    )

    class FakeCal:
        def __init__(self, account_id):
            self.account_id = account_id

        def list_events(self, start, end, limit):
            return [fake_event] if self.account_id == fake_event.account_id else []

    with patch(
        "clerk.providers.factory.calendar_provider",
        side_effect=lambda a, c: FakeCal(a.id),
    ):
        results = mcp_server.calendar_list(start="today", end="+7d")

    assert len(results) == 1
    assert results[0]["title"] == "Standup"


def test_calendar_create_rejects_bad_time_range(with_write_token):
    with pytest.raises(ValueError, match="end must be after start"):
        mcp_server.calendar_create(
            account="alice@example.com",
            title="x",
            start="+2d",
            end="+1d",
        )


def test_calendar_create_passes_attendees_to_provider(with_write_token):
    fake = MagicMock()
    fake.create_event.return_value = "ev-new"

    with patch("clerk.providers.factory.calendar_provider", return_value=fake):
        result = mcp_server.calendar_create(
            account="alice@example.com",
            title="Standup",
            start="2026-06-01T15:00:00",
            end="2026-06-01T15:30:00",
            attendees=None,
        )

    assert result == {"ok": True, "id": "ev-new"}
    out: OutgoingEvent = fake.create_event.call_args.args[0]
    assert out.attendees == []  # None coerces to empty list


def test_calendar_update_with_only_title(with_write_token):
    fake = MagicMock()
    with patch("clerk.providers.factory.calendar_provider", return_value=fake):
        result = mcp_server.calendar_update(
            event_id="ev1",
            account="alice@example.com",
            title="renamed",
        )
    assert result == {"ok": True}
    args = fake.update_event.call_args.args
    assert args[0] == "ev1"
    patch_arg: EventPatch = args[1]
    assert patch_arg.title == "renamed"
    assert patch_arg.start is None
    assert patch_arg.end is None


def test_calendar_cancel(with_write_token):
    fake = MagicMock()
    with patch("clerk.providers.factory.calendar_provider", return_value=fake):
        result = mcp_server.calendar_cancel(event_id="ev1", account="alice@example.com")
    assert result == {"ok": True}
    fake.cancel_event.assert_called_once_with("ev1")
