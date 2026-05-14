"""Tests for mail attachment send + receive on both providers."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from clerk.providers import gmail, graph
from clerk.providers.base import Attachment, OutgoingMessage


# ---------- Gmail: outgoing raw with attachments ----------


def test_gmail_build_raw_message_includes_attachment():
    payload_bytes = b"\x89PNG\r\n\x1a\nfake-png-data"
    out = OutgoingMessage(
        to=["me@example.com"],
        subject="with-attach",
        body="see attached",
        attachments=[
            Attachment(filename="logo.png", content=payload_bytes, mime_type="image/png"),
        ],
    )
    raw = gmail.build_raw_message(out)
    decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
    # Should be a multipart/mixed message containing both text and attachment.
    assert b"Content-Type: multipart" in decoded
    assert b"logo.png" in decoded
    assert b"image/png" in decoded


# ---------- Gmail: parse format=full structured response ----------


def test_gmail_parse_full_structured_simple_text():
    payload = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": [],
        "snippet": "hello",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "Subject", "value": "hi"},
                {"name": "Date", "value": "Mon, 10 May 2026 14:30:00 +0000"},
            ],
            "body": {
                "data": base64.urlsafe_b64encode(b"hello world").decode("ascii"),
                "size": 11,
            },
        },
    }
    msg = gmail.parse_message_full_structured("google:me@example.com", payload)
    assert msg.body_text == "hello world"
    assert msg.attachments == []
    assert msg.from_ == "alice@example.com"


def test_gmail_parse_full_structured_with_attachment():
    payload = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": [],
        "snippet": "see attached",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "Subject", "value": "with attach"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(b"see attached").decode("ascii"),
                        "size": 12,
                    },
                },
                {
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "ANGjdJxxx", "size": 12345},
                },
            ],
        },
    }
    msg = gmail.parse_message_full_structured("google:me@example.com", payload)
    assert msg.body_text == "see attached"
    assert len(msg.attachments) == 1
    att = msg.attachments[0]
    # Synthetic stable id (DFS index), not the ephemeral native attachmentId.
    assert att.id == "0"
    assert att.filename == "report.pdf"
    assert att.mime_type == "application/pdf"
    assert att.size_bytes == 12345


def test_gmail_extract_message_parts_returns_native_id_for_fetch():
    payload = {
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(b"hi").decode("ascii")},
            },
            {
                "filename": "doc.pdf",
                "mimeType": "application/pdf",
                "body": {"attachmentId": "att1", "size": 7},
            },
        ]
    }
    _, _, atts = gmail._extract_message_parts(payload)
    assert atts == [
        {"gmail_id": "att1", "filename": "doc.pdf", "mime_type": "application/pdf", "size": 7}
    ]


# ---------- Gmail: HTTP layer for get_attachment ----------


@respx.mock
def test_gmail_get_attachment_uses_index_and_fetches_current_native_id():
    """Confirm the synthetic id is the DFS index and we use the *current* native
    id from the re-fetched message to download bytes."""
    msg_payload = {
        "id": "m1",
        "payload": {
            "parts": [
                {
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "fresh-native-id", "size": 5},
                }
            ]
        },
    }
    respx.get(f"{gmail.GMAIL_BASE}/users/me/messages/m1").mock(
        return_value=httpx.Response(200, json=msg_payload)
    )
    respx.get(
        f"{gmail.GMAIL_BASE}/users/me/messages/m1/attachments/fresh-native-id"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"data": base64.urlsafe_b64encode(b"hello").decode("ascii"), "size": 5},
        )
    )
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    att = provider.get_attachment("m1", "0")  # synthetic index id
    assert att.filename == "report.pdf"
    assert att.content == b"hello"


def test_gmail_get_attachment_invalid_index_raises():
    payload_msg = {"id": "m1", "payload": {"parts": []}}
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(f"{gmail.GMAIL_BASE}/users/me/messages/m1").mock(
            return_value=httpx.Response(200, json=payload_msg)
        )
        with pytest.raises(ValueError, match="out of range"):
            provider.get_attachment("m1", "0")


def test_gmail_get_attachment_non_integer_id_raises():
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(f"{gmail.GMAIL_BASE}/users/me/messages/m1").mock(
            return_value=httpx.Response(200, json={"id": "m1", "payload": {"parts": []}})
        )
        with pytest.raises(ValueError, match="expected an integer index"):
            provider.get_attachment("m1", "not-a-number")


# ---------- Graph: outgoing payload with attachments ----------


def test_graph_send_payload_includes_attachment():
    out = OutgoingMessage(
        to=["me@example.com"],
        subject="x",
        body="hi",
        attachments=[
            Attachment(filename="a.txt", content=b"hello", mime_type="text/plain"),
        ],
    )
    payload = graph.build_send_payload(out)
    atts = payload["message"]["attachments"]
    assert len(atts) == 1
    assert atts[0]["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert atts[0]["name"] == "a.txt"
    assert atts[0]["contentType"] == "text/plain"
    assert base64.b64decode(atts[0]["contentBytes"]) == b"hello"


def test_graph_send_payload_omits_attachments_key_when_none():
    out = OutgoingMessage(to=["x@example.com"], subject="x", body="hi")
    payload = graph.build_send_payload(out)
    assert "attachments" not in payload["message"]


def test_graph_send_payload_rejects_oversized_attachment():
    huge = b"x" * (graph.GRAPH_INLINE_ATTACHMENT_MAX_BYTES + 1)
    out = OutgoingMessage(
        to=["x@example.com"],
        subject="x",
        body="x",
        attachments=[Attachment(filename="big.bin", content=huge)],
    )
    with pytest.raises(ValueError, match="exceeds Graph inline send limit"):
        graph.build_send_payload(out)


# ---------- Graph: HTTP layer for get + get_attachment ----------


@respx.mock
def test_graph_get_lists_attachments_when_present():
    respx.get(f"{graph.GRAPH_BASE}/me/messages/m1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "subject": "with attach",
                "isRead": True,
                "receivedDateTime": "2026-05-10T00:00:00Z",
                "from": {"emailAddress": {"address": "alice@example.com"}},
                "toRecipients": [],
                "hasAttachments": True,
                "body": {"contentType": "text", "content": "hello"},
            },
        )
    )
    respx.get(f"{graph.GRAPH_BASE}/me/messages/m1/attachments").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "AAMkAGI=",
                        "name": "doc.pdf",
                        "contentType": "application/pdf",
                        "size": 12345,
                    }
                ]
            },
        )
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOK")
    msg = provider.get("m1")
    assert len(msg.attachments) == 1
    assert msg.attachments[0].filename == "doc.pdf"


@respx.mock
def test_graph_get_attachment_returns_bytes():
    payload = {
        "id": "AAMkAGI=",
        "name": "doc.pdf",
        "contentType": "application/pdf",
        "contentBytes": base64.b64encode(b"PDF-bytes-here").decode("ascii"),
    }
    respx.get(f"{graph.GRAPH_BASE}/me/messages/m1/attachments/AAMkAGI=").mock(
        return_value=httpx.Response(200, json=payload)
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOK")
    att = provider.get_attachment("m1", "AAMkAGI=")
    assert att.filename == "doc.pdf"
    assert att.mime_type == "application/pdf"
    assert att.content == b"PDF-bytes-here"
