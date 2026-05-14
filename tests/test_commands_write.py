"""CLI tests for `clerk mail send|reply|delete`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from clerk import tokens
from clerk.cli import app
from clerk.config import Account, load_accounts, load_config, save_accounts, save_config
from clerk.providers.base import OutgoingMessage

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


def test_send_denied_without_write_scope(isolated_config):
    _seed_accounts()
    created = tokens.create(["mail:read"])
    result = runner.invoke(
        app,
        [
            "mail", "send",
            "--account", "alice@example.com",
            "--to", "alice@example.com",
            "--subject", "self test",
            "--body", "hi",
            "--token", created.raw,
        ],
    )
    assert result.exit_code == 1
    assert "lacks the required scope" in result.stderr


def test_send_blocked_by_global_write_disabled(isolated_config):
    _seed_accounts()
    created = tokens.create(["mail:write"])
    cfg = load_config()
    cfg.security.global_write_enabled = False
    save_config(cfg)

    result = runner.invoke(
        app,
        [
            "mail", "send",
            "--account", "alice@example.com",
            "--to", "alice@example.com",
            "--subject", "self test",
            "--body", "hi",
            "--token", created.raw,
        ],
    )
    assert result.exit_code == 1
    assert "globally disabled" in result.stderr


def test_send_requires_body_or_body_file(isolated_config):
    _seed_accounts()
    created = tokens.create(["mail:write"])
    result = runner.invoke(
        app,
        [
            "mail", "send",
            "--account", "alice@example.com",
            "--to", "alice@example.com",
            "--subject", "x",
            "--token", created.raw,
        ],
    )
    assert result.exit_code == 2
    assert "--body" in result.stderr


def test_send_rejects_both_body_and_body_file(isolated_config, tmp_path):
    _seed_accounts()
    created = tokens.create(["mail:write"])
    f = tmp_path / "body.txt"
    f.write_text("hi")
    result = runner.invoke(
        app,
        [
            "mail", "send",
            "--account", "alice@example.com",
            "--to", "alice@example.com",
            "--subject", "x",
            "--body", "inline",
            "--body-file", str(f),
            "--token", created.raw,
        ],
    )
    assert result.exit_code == 2


def test_send_happy_path_with_mocked_provider(isolated_config):
    _seed_accounts()
    created = tokens.create(["mail:write"])

    fake_provider = MagicMock()
    fake_provider.send.return_value = "new-msg-id"

    with patch("clerk.providers.factory.mail_provider", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "mail", "send",
                "--account", "alice@example.com",
                "--to", "alice@example.com",
                "--subject", "self test",
                "--body", "hi",
                "--token", created.raw,
            ],
        )

    assert result.exit_code == 0, result.stderr
    assert "Sent" in result.stdout
    call = fake_provider.send.call_args
    out_msg: OutgoingMessage = call.args[0]
    assert out_msg.to == ["alice@example.com"]
    assert out_msg.subject == "self test"
    assert out_msg.body == "hi"


def test_send_reads_body_from_file(isolated_config, tmp_path):
    _seed_accounts()
    created = tokens.create(["mail:write"])
    f = tmp_path / "body.txt"
    f.write_text("file contents here")

    fake_provider = MagicMock()
    fake_provider.send.return_value = None

    with patch("clerk.providers.factory.mail_provider", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "mail", "send",
                "--account", "alice@example.com",
                "--to", "alice@example.com",
                "--subject", "x",
                "--body-file", str(f),
                "--token", created.raw,
            ],
        )

    assert result.exit_code == 0, result.stderr
    out_msg: OutgoingMessage = fake_provider.send.call_args.args[0]
    assert out_msg.body == "file contents here"


def test_reply_happy_path(isolated_config):
    _seed_accounts()
    created = tokens.create(["mail:write"])

    fake_provider = MagicMock()
    fake_provider.reply.return_value = "reply-id"

    with patch("clerk.providers.factory.mail_provider", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "mail", "reply", "orig-msg-id",
                "--account", "alice@example.com",
                "--body", "thanks!",
                "--token", created.raw,
            ],
        )

    assert result.exit_code == 0, result.stderr
    assert "Replied" in result.stdout
    fake_provider.reply.assert_called_once_with("orig-msg-id", "thanks!", is_html=False)


def test_delete_happy_path(isolated_config):
    _seed_accounts()
    created = tokens.create(["mail:write"])

    fake_provider = MagicMock()
    fake_provider.delete.return_value = None

    with patch("clerk.providers.factory.mail_provider", return_value=fake_provider):
        result = runner.invoke(
            app,
            [
                "mail", "delete", "msg-id-here",
                "--account", "alice@example.com",
                "--token", created.raw,
            ],
        )

    assert result.exit_code == 0, result.stderr
    assert "Moved to trash" in result.stdout
    fake_provider.delete.assert_called_once_with("msg-id-here")
