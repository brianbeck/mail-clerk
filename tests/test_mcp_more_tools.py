"""Coverage for MCP tools beyond accounts_list / mail_search."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from clerk import mcp_server, tokens
from clerk.config import Account, load_accounts, save_accounts
from clerk.models import AttachmentSummary, Event, EventFull, MessageFull
from clerk.providers.base import EventPatch, OutgoingEvent


@pytest.fixture
def with_full_token(monkeypatch, isolated_config):
    reg = load_accounts()
    reg.accounts = [
        Account(
            id="microsoft:alice@example.com",
            provider="microsoft",
            email="alice@example.com",
            display_name="Alice",
        )
    ]
    save_accounts(reg)
    created = tokens.create(["mail:read", "mail:write", "calendar:read", "calendar:write"])
    monkeypatch.setenv("CLERK_TOKEN", created.raw)
    return created


def test_mail_read_returns_message_dict(with_full_token):
    fake_msg = MessageFull(
        account_id="microsoft:alice@example.com",
        provider="microsoft",
        id="m1",
        subject="hi",
        date=datetime(2026, 5, 10, tzinfo=timezone.utc),
        body_text="hello body",
        **{"from": "alice@example.com"},
    )
    fake = MagicMock()
    fake.get.return_value = fake_msg
    with patch("clerk.providers.factory.mail_provider", return_value=fake):
        result = mcp_server.mail_read(message_id="m1", account="alice@example.com")
    assert result["id"] == "m1"
    assert result["body_text"] == "hello body"


def test_mail_reply_dispatches_to_provider(with_full_token):
    fake = MagicMock()
    fake.reply.return_value = "reply-id"
    with patch("clerk.providers.factory.mail_provider", return_value=fake):
        result = mcp_server.mail_reply(
            message_id="m1", account="alice@example.com", body="thanks"
        )
    assert result == {"ok": True, "id": "reply-id"}
    fake.reply.assert_called_once_with("m1", "thanks", is_html=False)


def test_mail_send_with_attachments_decodes_base64(with_full_token):
    fake = MagicMock()
    fake.send.return_value = "new-id"
    payload = base64.b64encode(b"PDF-bytes").decode("ascii")

    with patch("clerk.providers.factory.mail_provider", return_value=fake):
        result = mcp_server.mail_send(
            account="alice@example.com",
            to=["alice@example.com"],
            subject="x",
            body="see attached",
            attachments=[
                {
                    "filename": "report.pdf",
                    "content_base64": payload,
                    "mime_type": "application/pdf",
                }
            ],
        )

    assert result == {"ok": True, "id": "new-id"}
    out = fake.send.call_args.args[0]
    assert len(out.attachments) == 1
    assert out.attachments[0].content == b"PDF-bytes"
    assert out.attachments[0].filename == "report.pdf"


def test_mail_get_attachment_returns_base64(with_full_token):
    from clerk.providers.base import Attachment

    fake = MagicMock()
    fake.get_attachment.return_value = Attachment(
        filename="hi.txt", content=b"abc", mime_type="text/plain"
    )
    with patch("clerk.providers.factory.mail_provider", return_value=fake):
        result = mcp_server.mail_get_attachment(
            message_id="m1", attachment_id="0", account="alice@example.com"
        )
    assert result["filename"] == "hi.txt"
    assert result["size_bytes"] == 3
    assert base64.b64decode(result["content_base64"]) == b"abc"


def test_calendar_get_returns_event_dict(with_full_token):
    fake_event = EventFull(
        account_id="microsoft:alice@example.com",
        provider="microsoft",
        id="ev1",
        title="Standup",
        start=datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc),
        body_text="agenda",
    )
    fake = MagicMock()
    fake.get_event.return_value = fake_event
    with patch("clerk.providers.factory.calendar_provider", return_value=fake):
        result = mcp_server.calendar_get(event_id="ev1", account="alice@example.com")
    assert result["title"] == "Standup"
    assert result["body_text"] == "agenda"


def test_calendar_update_passes_each_set_field(with_full_token):
    fake = MagicMock()
    with patch("clerk.providers.factory.calendar_provider", return_value=fake):
        mcp_server.calendar_update(
            event_id="ev1",
            account="alice@example.com",
            title="renamed",
            start="2026-06-01T10:00:00",
            end="2026-06-01T11:00:00",
            location="Room 2",
            body="updated",
        )
    patch_arg: EventPatch = fake.update_event.call_args.args[1]
    assert patch_arg.title == "renamed"
    assert patch_arg.location == "Room 2"
    assert patch_arg.body == "updated"
    assert patch_arg.start is not None
    assert patch_arg.end is not None


def test_calendar_create_passes_all_day_and_recurrence(with_full_token):
    fake = MagicMock()
    fake.create_event.return_value = "ev-new"
    with patch("clerk.providers.factory.calendar_provider", return_value=fake):
        mcp_server.calendar_create(
            account="alice@example.com",
            title="Weekly",
            start="2026-06-01",
            end="2026-06-02",
            is_all_day=True,
            recurrence_rule="FREQ=WEEKLY;COUNT=4",
        )
    out: OutgoingEvent = fake.create_event.call_args.args[0]
    assert out.is_all_day is True
    assert out.recurrence_rule == "FREQ=WEEKLY;COUNT=4"


def test_accounts_list_with_no_accounts(monkeypatch, isolated_config):
    created = tokens.create(["mail:read"])
    monkeypatch.setenv("CLERK_TOKEN", created.raw)
    result = mcp_server.accounts_list()
    assert result == []
