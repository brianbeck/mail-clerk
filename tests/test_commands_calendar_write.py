"""CLI tests for `clerk calendar create|update|cancel`."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from clerk import tokens
from clerk.cli import app
from clerk.config import Account, load_accounts, load_config, save_accounts, save_config
from clerk.providers.base import EventPatch, OutgoingEvent

runner = CliRunner()


def _seed_accounts():
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


def test_create_denied_without_write_scope(isolated_config):
    _seed_accounts()
    created = tokens.create(["calendar:read"])
    result = runner.invoke(
        app,
        [
            "calendar", "create",
            "--account", "alice@example.com",
            "--title", "x",
            "--start", "+1d",
            "--end", "+1d",  # invalid: same as start
            "--token", created.raw,
        ],
    )
    assert result.exit_code == 1
    assert "lacks the required scope" in result.stderr


def test_create_blocked_by_global_write_disabled(isolated_config):
    _seed_accounts()
    created = tokens.create(["calendar:write"])
    cfg = load_config()
    cfg.security.global_write_enabled = False
    save_config(cfg)
    result = runner.invoke(
        app,
        [
            "calendar", "create",
            "--account", "alice@example.com",
            "--title", "x",
            "--start", "+1d",
            "--end", "+2d",
            "--token", created.raw,
        ],
    )
    assert result.exit_code == 1
    assert "globally disabled" in result.stderr


def test_create_rejects_end_not_after_start(isolated_config):
    _seed_accounts()
    created = tokens.create(["calendar:write"])
    result = runner.invoke(
        app,
        [
            "calendar", "create",
            "--account", "alice@example.com",
            "--title", "x",
            "--start", "+2d",
            "--end", "+1d",
            "--token", created.raw,
        ],
    )
    assert result.exit_code == 2
    assert "--end must be after --start" in result.stderr


def test_create_happy_path(isolated_config):
    _seed_accounts()
    created = tokens.create(["calendar:write"])
    fake = MagicMock()
    fake.create_event.return_value = "new-event-id"

    with patch("clerk.providers.factory.calendar_provider", return_value=fake):
        result = runner.invoke(
            app,
            [
                "calendar", "create",
                "--account", "alice@example.com",
                "--title", "Standup",
                "--start", "2026-06-01T15:00:00",
                "--end", "2026-06-01T15:30:00",
                "--location", "Room 1",
                "--body", "agenda",
                "--token", created.raw,
            ],
        )
    assert result.exit_code == 0, result.stderr
    assert "new-event-id" in result.stdout
    call = fake.create_event.call_args
    out: OutgoingEvent = call.args[0]
    assert out.title == "Standup"
    assert out.start == datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
    assert out.end == datetime(2026, 6, 1, 15, 30, tzinfo=timezone.utc)
    assert out.location == "Room 1"
    assert out.body == "agenda"
    assert out.attendees == []


def test_update_partial_fields(isolated_config):
    _seed_accounts()
    created = tokens.create(["calendar:write"])
    fake = MagicMock()

    with patch("clerk.providers.factory.calendar_provider", return_value=fake):
        result = runner.invoke(
            app,
            [
                "calendar", "update", "ev-1",
                "--account", "alice@example.com",
                "--title", "renamed",
                "--token", created.raw,
            ],
        )
    assert result.exit_code == 0, result.stderr
    call = fake.update_event.call_args
    assert call.args[0] == "ev-1"
    patch_arg: EventPatch = call.args[1]
    assert patch_arg.title == "renamed"
    assert patch_arg.start is None
    assert patch_arg.end is None
    assert patch_arg.attendees is None


def test_cancel_happy_path(isolated_config):
    _seed_accounts()
    created = tokens.create(["calendar:write"])
    fake = MagicMock()

    with patch("clerk.providers.factory.calendar_provider", return_value=fake):
        result = runner.invoke(
            app,
            [
                "calendar", "cancel", "ev-1",
                "--account", "alice@example.com",
                "--token", created.raw,
            ],
        )
    assert result.exit_code == 0, result.stderr
    assert "Cancelled" in result.stdout
    fake.cancel_event.assert_called_once_with("ev-1")
