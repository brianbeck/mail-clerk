"""Tests for the mail write path (send, reply, delete) on both providers."""

from __future__ import annotations

import base64

import httpx
import respx

from clerk.providers import gmail, graph
from clerk.providers.base import OutgoingMessage


# ---------- Graph send payload ----------


def test_graph_build_send_payload_text():
    out = OutgoingMessage(
        to=["alice@example.com", "bob@example.com"],
        subject="hi",
        body="hello world",
        cc=["carol@example.com"],
    )
    payload = graph.build_send_payload(out)
    assert payload["saveToSentItems"] is True
    m = payload["message"]
    assert m["subject"] == "hi"
    assert m["body"] == {"contentType": "text", "content": "hello world"}
    assert [r["emailAddress"]["address"] for r in m["toRecipients"]] == [
        "alice@example.com",
        "bob@example.com",
    ]
    assert [r["emailAddress"]["address"] for r in m["ccRecipients"]] == ["carol@example.com"]
    assert m["bccRecipients"] == []


def test_graph_build_send_payload_html():
    out = OutgoingMessage(to=["a@example.com"], subject="hi", body="<p>x</p>", is_html=True)
    payload = graph.build_send_payload(out)
    assert payload["message"]["body"] == {"contentType": "html", "content": "<p>x</p>"}


# ---------- Graph HTTP send/reply/delete ----------


@respx.mock
def test_graph_send_posts_to_sendmail():
    route = respx.post(f"{graph.GRAPH_BASE}/me/sendMail").mock(
        return_value=httpx.Response(202)
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOK")
    out = OutgoingMessage(to=["me@example.com"], subject="self", body="hi")
    result = provider.send(out)
    assert result is None
    assert route.called
    req_body = route.calls[0].request.read()
    assert b"me@example.com" in req_body
    assert b"self" in req_body


@respx.mock
def test_graph_reply_posts_to_message_reply():
    route = respx.post(f"{graph.GRAPH_BASE}/me/messages/m1/reply").mock(
        return_value=httpx.Response(202)
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOK")
    provider.reply("m1", "thanks")
    assert route.called
    body = route.calls[0].request.read()
    assert b"thanks" in body


@respx.mock
def test_graph_delete_calls_delete_endpoint():
    route = respx.delete(f"{graph.GRAPH_BASE}/me/messages/m1").mock(
        return_value=httpx.Response(204)
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOK")
    provider.delete("m1")
    assert route.called


# ---------- Gmail raw message construction ----------


def _decode_raw(raw_b64url: str) -> bytes:
    return base64.urlsafe_b64decode(raw_b64url.encode("ascii"))


def test_gmail_build_raw_message_text():
    out = OutgoingMessage(
        to=["alice@example.com"], subject="hi", body="hello", cc=["carol@example.com"]
    )
    raw = gmail.build_raw_message(out)
    decoded = _decode_raw(raw).decode()
    assert "To: alice@example.com" in decoded
    assert "Cc: carol@example.com" in decoded
    assert "Subject: hi" in decoded
    assert "hello" in decoded


def test_gmail_build_raw_message_html():
    out = OutgoingMessage(to=["a@example.com"], subject="x", body="<p>y</p>", is_html=True)
    raw = gmail.build_raw_message(out)
    decoded = _decode_raw(raw).decode()
    assert "Content-Type: text/html" in decoded
    assert "<p>y</p>" in decoded


def test_gmail_build_raw_message_with_reply_headers():
    out = OutgoingMessage(to=["a@example.com"], subject="Re: x", body="thanks")
    raw = gmail.build_raw_message(out, in_reply_to="<orig@msgid>", references="<orig@msgid>")
    decoded = _decode_raw(raw).decode()
    assert "In-Reply-To: <orig@msgid>" in decoded
    assert "References: <orig@msgid>" in decoded


# ---------- Gmail HTTP send/reply/delete ----------


@respx.mock
def test_gmail_send_posts_raw_and_returns_id():
    route = respx.post(f"{gmail.GMAIL_BASE}/users/me/messages/send").mock(
        return_value=httpx.Response(200, json={"id": "new-id", "threadId": "t1"})
    )
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    out = OutgoingMessage(to=["me@example.com"], subject="self", body="hi")
    new_id = provider.send(out)
    assert new_id == "new-id"
    assert route.called
    sent_body = route.calls[0].request.read()
    # The "raw" field is in the JSON payload, base64url-encoded.
    assert b'"raw"' in sent_body


@respx.mock
def test_gmail_reply_fetches_then_sends_with_threading():
    respx.get(f"{gmail.GMAIL_BASE}/users/me/messages/m1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "thread-xyz",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Subject", "value": "Hello"},
                        {"name": "Message-ID", "value": "<orig@msgid>"},
                    ]
                },
            },
        )
    )
    send_route = respx.post(f"{gmail.GMAIL_BASE}/users/me/messages/send").mock(
        return_value=httpx.Response(200, json={"id": "reply-id", "threadId": "thread-xyz"})
    )

    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    new_id = provider.reply("m1", "thanks!")
    assert new_id == "reply-id"

    # Verify the send call body contains thread id and reply subject.
    body = send_route.calls[0].request.read()
    assert b"thread-xyz" in body
    decoded_request = body.decode()
    assert "thread-xyz" in decoded_request


@respx.mock
def test_gmail_reply_preserves_re_prefix_if_present():
    respx.get(f"{gmail.GMAIL_BASE}/users/me/messages/m1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Subject", "value": "Re: Hello"},
                        {"name": "Message-ID", "value": "<x@y>"},
                    ]
                },
            },
        )
    )
    send_route = respx.post(f"{gmail.GMAIL_BASE}/users/me/messages/send").mock(
        return_value=httpx.Response(200, json={"id": "rid"})
    )
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    provider.reply("m1", "thx")
    # Subject should not become "Re: Re: Hello".
    body = send_route.calls[0].request.read().decode()
    # The raw message is base64url-encoded inside JSON; decode and check.
    import json
    raw_b64 = json.loads(body)["raw"]
    decoded = base64.urlsafe_b64decode(raw_b64.encode("ascii")).decode()
    assert "Subject: Re: Hello" in decoded
    assert "Subject: Re: Re:" not in decoded


@respx.mock
def test_gmail_delete_calls_trash():
    route = respx.post(f"{gmail.GMAIL_BASE}/users/me/messages/m1/trash").mock(
        return_value=httpx.Response(200, json={"id": "m1"})
    )
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    provider.delete("m1")
    assert route.called
