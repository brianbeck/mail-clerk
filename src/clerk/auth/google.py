"""Google OAuth via google-auth-oauthlib.

Uses InstalledAppFlow with a local-loopback redirect (Google's recommended flow for
desktop apps). The full Credentials JSON (access + refresh tokens) is serialized to
a per-account macOS Keychain entry. accounts.json holds only non-secret metadata.

Note: Google deprecated the out-of-band (device-code-style) OOB flow in 2022,
so we only support the interactive loopback flow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from clerk.auth import keychain
from clerk.config import Account, GoogleOAuth

GOOGLE_SCOPES: list[str] = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]
# gmail.modify covers read, send, label, and trash (not permanent delete).
# calendar.events covers read + write on the user's calendars.

USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


@dataclass
class AuthResult:
    account: Account
    access_token: str


def _account_id_for(email: str) -> str:
    return f"google:{email.lower()}"


def _client_config(cfg: GoogleOAuth) -> dict:
    if not cfg.client_id or not cfg.client_secret:
        raise RuntimeError(
            "Google OAuth is not configured. Run: "
            "clerk config set oauth.google.client_id <id> "
            "and clerk config set oauth.google.client_secret <secret>"
        )
    return {
        "installed": {
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def login(cfg: GoogleOAuth) -> AuthResult:
    """Run an interactive login. Opens a browser, captures the loopback redirect."""
    flow = InstalledAppFlow.from_client_config(_client_config(cfg), GOOGLE_SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="select_account",
        authorization_prompt_message="",
        success_message="Authentication complete. You can close this tab.",
        open_browser=True,
    )

    profile = _fetch_profile(creds.token)
    email = profile["email"]
    display_name = profile.get("name", "")

    account_id = _account_id_for(email)
    keychain.store(account_id, creds.to_json())

    return AuthResult(
        account=Account(
            id=account_id,
            provider="google",
            email=email,
            display_name=display_name,
        ),
        access_token=creds.token,
    )


def get_access_token(cfg: GoogleOAuth, account: Account) -> str:
    """Return a valid access token for `account`, refreshing if needed."""
    blob = keychain.load(account.id)
    if not blob:
        raise RuntimeError(
            f"No cached credentials for {account.email}. "
            f"Re-authenticate with: clerk auth add --provider google"
        )

    creds = Credentials.from_authorized_user_info(json.loads(blob), GOOGLE_SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise RuntimeError(
                f"No refresh token available for {account.email}. "
                f"Re-authenticate with: clerk auth add --provider google"
            )
        creds.refresh(Request())
        keychain.store(account.id, creds.to_json())
    return creds.token


def _fetch_profile(access_token: str) -> dict:
    resp = httpx.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()
