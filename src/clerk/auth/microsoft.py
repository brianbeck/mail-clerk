"""Microsoft OAuth via MSAL.

Interactive auth flow (opens a browser, captures the loopback redirect). Falls back
to device-code flow when explicitly requested or when no browser is available.

The full MSAL token cache (access + refresh tokens) is serialized to a per-account
macOS Keychain entry. The clerk accounts.json file only stores non-secret metadata.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from msal import PublicClientApplication, SerializableTokenCache

from clerk.auth import keychain
from clerk.config import Account, MicrosoftOAuth

GRAPH_SCOPES: list[str] = [
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.ReadWrite",
    "User.Read",
]
# `offline_access` is added automatically by MSAL for public clients.
# `openid` / `profile` are reserved and also added automatically.

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@dataclass
class AuthResult:
    account: Account
    access_token: str


def _account_id_for(email: str) -> str:
    return f"microsoft:{email.lower()}"


def _load_cache(account_id: str) -> SerializableTokenCache:
    cache = SerializableTokenCache()
    blob = keychain.load(account_id)
    if blob:
        cache.deserialize(blob)
    return cache


def _save_cache(account_id: str, cache: SerializableTokenCache) -> None:
    if cache.has_state_changed:
        keychain.store(account_id, cache.serialize())


def _build_app(cfg: MicrosoftOAuth, cache: SerializableTokenCache) -> PublicClientApplication:
    if not cfg.client_id:
        raise RuntimeError(
            "Microsoft client_id is not configured. Run: "
            "clerk config set oauth.microsoft.client_id <id>"
        )
    return PublicClientApplication(
        client_id=cfg.client_id,
        authority=cfg.authority,
        token_cache=cache,
    )


def login(cfg: MicrosoftOAuth, device_code: bool = False) -> AuthResult:
    """Run an interactive (or device-code) login. Returns the new Account and an access token.

    The account email is not known until after authentication, so we acquire the token
    against a temporary cache, look up the email via /me, then move the cache to its
    final Keychain slot keyed by email.
    """
    cache = SerializableTokenCache()
    app = _build_app(cfg, cache)

    if device_code:
        flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to initiate device flow: {flow}")
        # Caller's UI prints flow["message"] before this; for now, print here.
        print(flow["message"])
        result = app.acquire_token_by_device_flow(flow)
    else:
        result = app.acquire_token_interactive(scopes=GRAPH_SCOPES, prompt="select_account")

    if "access_token" not in result:
        raise RuntimeError(
            f"Authentication failed: {result.get('error_description') or result}"
        )

    access_token = result["access_token"]
    profile = _fetch_profile(access_token)
    email = profile["email"]
    display_name = profile.get("displayName", "")

    account_id = _account_id_for(email)
    # Persist the cache under the email-derived id.
    keychain.store(account_id, cache.serialize())

    account = Account(
        id=account_id,
        provider="microsoft",
        email=email,
        display_name=display_name,
    )
    return AuthResult(account=account, access_token=access_token)


def get_access_token(cfg: MicrosoftOAuth, account: Account) -> str:
    """Return a valid access token for `account`, refreshing silently if needed."""
    cache = _load_cache(account.id)
    app = _build_app(cfg, cache)

    msal_accounts = app.get_accounts(username=account.email)
    if not msal_accounts:
        raise RuntimeError(
            f"No cached credentials for {account.email}. "
            f"Re-authenticate with: clerk auth add --provider microsoft"
        )

    result = app.acquire_token_silent(GRAPH_SCOPES, account=msal_accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError(
            f"Silent token acquisition failed for {account.email}. "
            f"Re-authenticate with: clerk auth add --provider microsoft"
        )

    _save_cache(account.id, cache)
    return result["access_token"]


def _fetch_profile(access_token: str) -> dict:
    """Call Graph /me to get the canonical email and display name."""
    resp = httpx.get(
        f"{GRAPH_BASE}/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    me = resp.json()
    # Personal accounts return userPrincipalName; some return mail.
    # For personal MSA, `mail` may be null and `userPrincipalName` holds the email.
    email = me.get("mail") or me.get("userPrincipalName")
    if not email:
        raise RuntimeError(f"Could not determine account email from Graph /me: {me}")
    return {"email": email, "displayName": me.get("displayName", "")}
