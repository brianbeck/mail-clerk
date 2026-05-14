"""Tests for capability tokens."""

from __future__ import annotations

import pytest

from clerk import tokens


def test_create_returns_wire_form_with_prefix(isolated_config):
    created = tokens.create(["mail:read", "calendar:read"], note="ro")
    assert created.raw.startswith("clk_")
    assert created.id in created.raw
    assert created.record.scopes == ["mail:read", "calendar:read"]
    assert created.record.note == "ro"
    # Secret is not stored anywhere we can recover.
    assert created.record.secret_hash != created.raw


def test_create_rejects_unknown_scope(isolated_config):
    with pytest.raises(ValueError, match="Unknown scope"):
        tokens.create(["mail:read", "frobnicate:write"])  # type: ignore[list-item]


def test_verify_accepts_real_token(isolated_config):
    created = tokens.create(["mail:read"])
    record = tokens.verify(created.raw)
    assert record is not None
    assert record.id == created.id


def test_verify_rejects_tampered_secret(isolated_config):
    created = tokens.create(["mail:read"])
    tampered = created.raw + "x"
    assert tokens.verify(tampered) is None


def test_verify_rejects_unknown_id(isolated_config):
    tokens.create(["mail:read"])
    fake = "clk_deadbeef_doesntmatter"
    assert tokens.verify(fake) is None


def test_verify_rejects_malformed(isolated_config):
    assert tokens.verify("not-a-token") is None
    assert tokens.verify("clk_no-second-part") is None


def test_revoke_removes_token(isolated_config):
    created = tokens.create(["mail:read"])
    assert tokens.revoke(created.id) is True
    assert tokens.verify(created.raw) is None
    # Idempotency: revoking again returns False.
    assert tokens.revoke(created.id) is False
