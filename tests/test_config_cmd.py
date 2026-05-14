"""CLI tests for `clerk config`."""

from __future__ import annotations

from typer.testing import CliRunner

from clerk.cli import app

runner = CliRunner()


def test_config_path_prints_config_file(isolated_config):
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert "config.toml" in result.stdout


def test_config_show_lists_defaults_on_first_run(isolated_config):
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "global_write_enabled = True" in result.stdout
    assert "require_token        = True" in result.stdout
    assert "tokens: 0 configured" in result.stdout


def test_config_set_microsoft_client_id_persists(isolated_config):
    result = runner.invoke(app, ["config", "set", "oauth.microsoft.client_id", "ms-abc"])
    assert result.exit_code == 0

    show = runner.invoke(app, ["config", "show"])
    assert "ms-abc" in show.stdout


def test_config_set_google_secret(isolated_config):
    result = runner.invoke(app, ["config", "set", "oauth.google.client_secret", "G-sec"])
    assert result.exit_code == 0


def test_config_set_rejects_unknown_key(isolated_config):
    result = runner.invoke(app, ["config", "set", "nonexistent.key", "1"])
    assert result.exit_code == 2
    assert "Unknown config key" in result.stderr


def test_config_set_rejects_invalid_bool(isolated_config):
    result = runner.invoke(app, ["config", "set", "security.require_token", "maybe"])
    assert result.exit_code != 0
