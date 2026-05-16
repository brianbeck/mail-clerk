"""Tests for fast body retrieval: search --include-body and batch read."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import respx
from typer.testing import CliRunner

from clerk import mcp_server, tokens
from clerk.cli import app
from clerk.config import Account, load_accounts, save_accounts
from clerk.models import MessageFull
from clerk.providers import gmail, graph
from clerk.search import SearchQuery

runner = CliRunner()


# ---------- Graph: include_body costs zero extra API calls ----------


@respx.mock
def test_graph_search_include_body_parses_body_no_extra_calls():
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
                        "body": {"contentType": "text", "content": "the full body"},
                    }
                ]
            },
        )
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOK")
    results = provider.search(SearchQuery(text="hi"), 10, include_body=True)

    assert len(respx.calls) == 1  # NO extra round-trips
    assert isinstance(results[0], MessageFull)
    assert results[0].body_text == "the full body"


@respx.mock
def test_graph_search_without_body_returns_summary():
    respx.get(f"{graph.GRAPH_BASE}/me/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "subject": "hi",
                        "isRead": True,
                        "receivedDateTime": "2026-05-10T00:00:00Z",
                        "from": {"emailAddress": {"address": "a@example.com"}},
                        "toRecipients": [],
                        "body": {"contentType": "text", "content": "ignored"},
                    }
                ]
            },
        )
    )
    provider = graph.GraphMailProvider("microsoft:me@example.com", lambda: "TOK")
    results = provider.search(SearchQuery(text="hi"), 10, include_body=False)
    assert not isinstance(results[0], MessageFull)
    assert not hasattr(results[0], "body_text") or getattr(results[0], "body_text", "") == ""


# ---------- Gmail: include_body uses format=full and parallelizes ----------


@respx.mock
def test_gmail_search_include_body_uses_format_full():
    respx.get(f"{gmail.GMAIL_BASE}/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
    )

    def msg_response(request):
        assert request.url.params["format"] == "full"
        mid = str(request.url).rsplit("/", 1)[-1].split("?")[0]
        return httpx.Response(
            200,
            json={
                "id": mid,
                "threadId": "t",
                "labelIds": [],
                "snippet": "snip",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": "alice@example.com"},
                        {"name": "Subject", "value": f"subject-{mid}"},
                    ],
                    "body": {
                        "data": base64.urlsafe_b64encode(f"body-{mid}".encode()).decode(),
                        "size": 6,
                    },
                },
            },
        )

    respx.get(url__regex=rf"{gmail.GMAIL_BASE}/users/me/messages/m\d").mock(
        side_effect=msg_response
    )

    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    results = provider.search(SearchQuery(text="x"), 10, include_body=True)

    assert len(results) == 2
    assert all(isinstance(r, MessageFull) for r in results)
    # Order preserved from the search id list.
    assert results[0].id == "m1"
    assert results[0].body_text == "body-m1"
    assert results[1].body_text == "body-m2"


@respx.mock
def test_gmail_search_without_body_uses_metadata():
    respx.get(f"{gmail.GMAIL_BASE}/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "m1"}]})
    )
    captured = {}

    def msg_response(request):
        captured["format"] = request.url.params["format"]
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t",
                "labelIds": [],
                "snippet": "snip",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "alice@example.com"},
                        {"name": "Subject", "value": "hi"},
                    ]
                },
            },
        )

    respx.get(url__regex=rf"{gmail.GMAIL_BASE}/users/me/messages/m1").mock(
        side_effect=msg_response
    )
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    provider.search(SearchQuery(text="x"), 10, include_body=False)
    assert captured["format"] == "metadata"


@respx.mock
def test_gmail_search_empty_returns_no_fetches():
    respx.get(f"{gmail.GMAIL_BASE}/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    provider = gmail.GmailProvider("google:me@example.com", lambda: "TOK")
    assert provider.search(SearchQuery(text="x"), 10, include_body=True) == []
    assert len(respx.calls) == 1


# ---------- CLI: --include-body ----------


def _seed_one_account():
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


def test_cli_search_include_body_json_contains_body(isolated_config):
    _seed_one_account()
    created = tokens.create(["mail:read"])

    fake_msg = MessageFull(
        account_id="microsoft:alice@example.com",
        provider="microsoft",
        id="m1",
        subject="hi",
        date=datetime(2026, 5, 10, tzinfo=timezone.utc),
        body_text="full body here",
        **{"from": "alice@example.com"},
    )

    class FakeProvider:
        def __init__(self, account_id):
            self.account_id = account_id

        def search(self, query, limit, include_trash=False, include_body=False):
            assert include_body is True
            return [fake_msg]

    with patch(
        "clerk.providers.factory.mail_provider", side_effect=lambda a, c: FakeProvider(a.id)
    ):
        result = runner.invoke(
            app,
            ["mail", "search", "hi", "--include-body", "--json", "--token", created.raw],
        )
    assert result.exit_code == 0, result.stderr
    assert "full body here" in result.stdout


# ---------- CLI + MCP: batch read ----------


def test_cli_read_multiple_ids_parallel(isolated_config):
    _seed_one_account()
    created = tokens.create(["mail:read"])

    def make(mid):
        return MessageFull(
            account_id="microsoft:alice@example.com",
            provider="microsoft",
            id=mid,
            subject=f"subj-{mid}",
            date=datetime(2026, 5, 10, tzinfo=timezone.utc),
            body_text=f"body-{mid}",
            **{"from": "alice@example.com"},
        )

    class FakeProvider:
        def get(self, mid):
            return make(mid)

    with patch("clerk.providers.factory.mail_provider", return_value=FakeProvider()):
        result = runner.invoke(
            app,
            ["mail", "read", "a", "b", "c", "--account", "alice@example.com",
             "--json", "--token", created.raw],
        )
    assert result.exit_code == 0, result.stderr
    assert '"id": "a"' in result.stdout
    assert '"id": "b"' in result.stdout
    assert '"id": "c"' in result.stdout


def test_mcp_mail_read_batch(monkeypatch, isolated_config):
    _seed_one_account()
    created = tokens.create(["mail:read"])
    monkeypatch.setenv("CLERK_TOKEN", created.raw)

    def make(mid):
        return MessageFull(
            account_id="microsoft:alice@example.com",
            provider="microsoft",
            id=mid,
            subject=f"s-{mid}",
            date=datetime(2026, 5, 10, tzinfo=timezone.utc),
            body_text=f"b-{mid}",
            **{"from": "alice@example.com"},
        )

    class FakeProvider:
        def get(self, mid):
            return make(mid)

    with patch("clerk.providers.factory.mail_provider", return_value=FakeProvider()):
        result = mcp_server.mail_read_batch(
            message_ids=["x", "y"], account="alice@example.com"
        )
    assert [m["id"] for m in result] == ["x", "y"]
    assert result[0]["body_text"] == "b-x"


def test_mcp_mail_read_batch_empty_returns_empty(monkeypatch, isolated_config):
    _seed_one_account()
    created = tokens.create(["mail:read"])
    monkeypatch.setenv("CLERK_TOKEN", created.raw)
    result = mcp_server.mail_read_batch(message_ids=[], account="alice@example.com")
    assert result == []
