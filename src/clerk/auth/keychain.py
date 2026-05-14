"""Thin wrapper over the `keyring` package for storing per-account OAuth caches.

Service name is always "clerk"; the account `id` is used as the keychain username.
Values are arbitrary strings (typically serialized MSAL token caches or OAuth JSON).
"""

from __future__ import annotations

import keyring

SERVICE = "clerk"


def store(account_id: str, secret: str) -> None:
    keyring.set_password(SERVICE, account_id, secret)


def load(account_id: str) -> str | None:
    return keyring.get_password(SERVICE, account_id)


def delete(account_id: str) -> None:
    try:
        keyring.delete_password(SERVICE, account_id)
    except keyring.errors.PasswordDeleteError:
        pass
