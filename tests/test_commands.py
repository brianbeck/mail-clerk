"""CLI command tests via typer's CliRunner."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clerk import tokens
from clerk.cli import app
from clerk.config import Account, load_accounts, save_accounts
from clerk.models import Event, Message

runner = CliRunner()


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


def _make_message(account_id: str, mid: str, when: datetime, subject: str) -> Message:
    return Message(
        account_id=account_id,
        provider="microsoft" if account_id.startswith("microsoft") else "google",
        id=mid,
        subject=subject,
        date=when,
        **{"from": "alice@example.com"},
    )


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "clerk" in result.stdout


def test_help_lists_all_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["config", "auth", "token", "mail", "calendar"]:
        assert cmd in result.stdout


def test_token_create_and_list(isolated_config):
    result = runner.invoke(app, ["token", "create", "--scopes", "mail:read"])
    assert result.exit_code == 0
    assert "Created token" in result.stdout
    assert "clk_" in result.stdout

    result = runner.invoke(app, ["token", "list"])
    assert result.exit_code == 0
    assert "mail:read" in result.stdout


def test_mail_search_denied_without_token(isolated_config):
    _seed_accounts()
    result = runner.invoke(app, ["mail", "search", "hi"])
    assert result.exit_code == 1
    assert "capability token is required" in result.stderr


def test_mail_search_with_token_runs_through_gate(isolated_config):
    _seed_accounts()
    created = tokens.create(["mail:read"])

    fake_messages = [
        _make_message(
            "microsoft:alice@example.com",
            "m-ms",
            datetime(2026, 5, 10, tzinfo=timezone.utc),
            "Budget",
        ),
        _make_message(
            "google:bob@example.com",
            "m-g",
            datetime(2026, 5, 11, tzinfo=timezone.utc),
            "Lunch",
        ),
    ]

    class FakeProvider:
        def __init__(self, account_id):
            self.account_id = account_id

        def search(self, query, limit, include_trash=False, include_body=False):
            return [m for m in fake_messages if m.account_id == self.account_id]

        def get(self, message_id):
            raise NotImplementedError

    with patch("clerk.providers.factory.mail_provider", side_effect=lambda a, c: FakeProvider(a.id)):
        result = runner.invoke(
            app, ["mail", "search", "budget", "--token", created.raw, "--json"]
        )
    assert result.exit_code == 0, result.stderr
    assert "m-g" in result.stdout
    assert "m-ms" in result.stdout


def test_mail_search_blocked_by_write_scope_mismatch(isolated_config):
    """A read-only token should not allow writes — but mail search is a read.
    This verifies the scope-mismatch path triggers cleanly on a different call.
    Verified separately in test_permissions; here we sanity-check the gate runs."""
    _seed_accounts()
    # Token that lacks mail:read should be denied for mail search.
    created = tokens.create(["calendar:read"])
    result = runner.invoke(app, ["mail", "search", "hi", "--token", created.raw])
    assert result.exit_code == 1
    assert "lacks the required scope" in result.stderr


def test_calendar_list_invalid_when_returns_2(isolated_config):
    _seed_accounts()
    created = tokens.create(["calendar:read"])
    result = runner.invoke(
        app, ["calendar", "list", "--from", "garbage", "--token", created.raw]
    )
    assert result.exit_code == 2
    assert "Cannot parse" in result.stderr


def test_calendar_list_with_fake_provider(isolated_config):
    _seed_accounts()
    created = tokens.create(["calendar:read"])

    fake_events = [
        Event(
            account_id="microsoft:alice@example.com",
            provider="microsoft",
            id="ev1",
            title="Standup",
            start=datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 13, 15, 30, tzinfo=timezone.utc),
        )
    ]

    class FakeCal:
        def __init__(self, account_id):
            self.account_id = account_id

        def list_events(self, start, end, limit):
            return [e for e in fake_events if e.account_id == self.account_id]

        def get_event(self, event_id):
            raise NotImplementedError

    with patch(
        "clerk.providers.factory.calendar_provider",
        side_effect=lambda a, c: FakeCal(a.id),
    ):
        result = runner.invoke(
            app,
            ["calendar", "list", "--from", "today", "--to", "+7d", "--token", created.raw],
        )
    assert result.exit_code == 0, result.stderr
    assert "Standup" in result.stdout


def test_config_set_warns_on_disabling_require_token(isolated_config):
    result = runner.invoke(app, ["config", "set", "security.require_token", "false"])
    assert result.exit_code == 0
    assert "WARNING" in result.stderr
    assert "ANY shell user" in result.stderr


@pytest.fixture(autouse=True)
def _typer_runner_separates_stderr(monkeypatch):
    # typer's CliRunner mixes stderr into stdout by default in some configs.
    # CliRunner from typer.testing already handles this; this fixture is a placeholder
    # so test_X functions that reference result.stderr work regardless of version.
    yield
