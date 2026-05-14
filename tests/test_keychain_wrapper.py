"""Tests for the keychain wrapper (mocked keyring backend)."""

from __future__ import annotations

from clerk.auth import keychain


def test_store_load_delete_round_trip(fake_keychain):
    keychain.store("acct-1", "secret-blob")
    assert keychain.load("acct-1") == "secret-blob"
    keychain.delete("acct-1")
    assert keychain.load("acct-1") is None


def test_load_returns_none_for_unknown_account(fake_keychain):
    assert keychain.load("never-stored") is None


def test_delete_is_idempotent(fake_keychain):
    keychain.delete("never-existed")  # must not raise
    keychain.store("x", "v")
    keychain.delete("x")
    keychain.delete("x")  # second delete is a no-op
