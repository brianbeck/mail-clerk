"""Tests for config and accounts registry."""

from __future__ import annotations

from clerk import config as config_mod


def test_load_config_creates_defaults_when_missing(isolated_config):
    cfg = config_mod.load_config()
    assert cfg.security.global_write_enabled is True
    assert cfg.security.require_token is True
    assert cfg.tokens == []
    assert config_mod.config_path().exists()


def test_save_then_load_round_trip(isolated_config):
    cfg = config_mod.load_config()
    cfg.security.global_write_enabled = False
    cfg.oauth.microsoft.client_id = "ms-client-123"
    cfg.oauth.google.client_id = "google-456"
    cfg.oauth.google.client_secret = "secret"
    config_mod.save_config(cfg)

    cfg2 = config_mod.load_config()
    assert cfg2.security.global_write_enabled is False
    assert cfg2.oauth.microsoft.client_id == "ms-client-123"
    assert cfg2.oauth.google.client_id == "google-456"
    assert cfg2.oauth.google.client_secret == "secret"


def test_save_preserves_token_records(isolated_config):
    cfg = config_mod.load_config()
    cfg.tokens.append(
        config_mod.TokenRecord(
            id="abc",
            secret_hash="$2b$12$dummy",
            scopes=["mail:read", "calendar:write"],
            created_at="2026-01-01T00:00:00+00:00",
            note='special "quoted" note',
        )
    )
    config_mod.save_config(cfg)

    cfg2 = config_mod.load_config()
    assert len(cfg2.tokens) == 1
    t = cfg2.tokens[0]
    assert t.id == "abc"
    assert t.scopes == ["mail:read", "calendar:write"]
    assert t.note == 'special "quoted" note'


def test_accounts_round_trip(isolated_config):
    reg = config_mod.load_accounts()
    assert reg.accounts == []

    reg.accounts.append(
        config_mod.Account(
            id="microsoft:alice@example.com",
            provider="microsoft",
            email="alice@example.com",
            display_name="Alice",
        )
    )
    config_mod.save_accounts(reg)

    reg2 = config_mod.load_accounts()
    assert len(reg2.accounts) == 1
    assert reg2.accounts[0].email == "alice@example.com"
