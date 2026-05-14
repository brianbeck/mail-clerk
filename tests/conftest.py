"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest

from clerk import config as config_mod
from clerk.auth import keychain


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Each test gets a private CLERK_CONFIG_DIR."""
    monkeypatch.setenv("CLERK_CONFIG_DIR", str(tmp_path))
    # Force the config module to pick up the new env var.
    return tmp_path


@pytest.fixture
def fake_keychain(monkeypatch):
    """In-memory keychain so tests don't touch the real macOS Keychain."""
    store: dict[str, str] = {}

    def _store(account_id, secret):
        store[account_id] = secret

    def _load(account_id):
        return store.get(account_id)

    def _delete(account_id):
        store.pop(account_id, None)

    monkeypatch.setattr(keychain, "store", _store)
    monkeypatch.setattr(keychain, "load", _load)
    monkeypatch.setattr(keychain, "delete", _delete)
    return store


@pytest.fixture
def ms_account():
    return config_mod.Account(
        id="microsoft:alice@example.com",
        provider="microsoft",
        email="alice@example.com",
        display_name="Alice",
    )


@pytest.fixture
def gmail_account():
    return config_mod.Account(
        id="google:bob@example.com",
        provider="google",
        email="bob@example.com",
        display_name="Bob",
    )


@pytest.fixture
def integration_enabled():
    return os.environ.get("CLERK_INTEGRATION") == "1"
