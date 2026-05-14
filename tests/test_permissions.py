"""Tests for the permission gate."""

from __future__ import annotations

import pytest

from clerk import permissions, tokens
from clerk.config import load_config, save_config
from clerk.permissions import PermissionDenied


def test_missing_token_when_required(isolated_config):
    cfg = load_config()
    with pytest.raises(PermissionDenied, match="capability token is required"):
        permissions.check(cfg, "mail", "read", token=None)


def test_valid_scope_allows(isolated_config):
    created = tokens.create(["mail:read"])
    cfg = load_config()
    # Should not raise.
    permissions.check(cfg, "mail", "read", token=created.raw)


def test_scope_mismatch_denies(isolated_config):
    created = tokens.create(["mail:read"])
    cfg = load_config()
    with pytest.raises(PermissionDenied, match="lacks the required scope"):
        permissions.check(cfg, "mail", "write", token=created.raw)


def test_write_blocked_by_global_kill_switch(isolated_config):
    created = tokens.create(["mail:write"])
    cfg = load_config()
    cfg.security.global_write_enabled = False
    save_config(cfg)

    cfg = load_config()
    with pytest.raises(PermissionDenied, match="globally disabled"):
        permissions.check(cfg, "mail", "write", token=created.raw)


def test_require_token_false_skips_token_check(isolated_config):
    cfg = load_config()
    cfg.security.require_token = False
    save_config(cfg)

    cfg = load_config()
    # No token, no error.
    permissions.check(cfg, "mail", "write", token=None)
    permissions.check(cfg, "calendar", "read", token=None)


def test_invalid_token_denied(isolated_config):
    cfg = load_config()
    with pytest.raises(PermissionDenied, match="Invalid or unknown"):
        permissions.check(cfg, "mail", "read", token="clk_deadbeef_nope")


def test_resolve_token_prefers_explicit(monkeypatch):
    monkeypatch.setenv("CLERK_TOKEN", "from-env")
    assert permissions.resolve_token("explicit") == "explicit"


def test_resolve_token_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("CLERK_TOKEN", "from-env")
    assert permissions.resolve_token(None) == "from-env"


def test_resolve_token_none_when_neither(monkeypatch):
    monkeypatch.delenv("CLERK_TOKEN", raising=False)
    assert permissions.resolve_token(None) is None
