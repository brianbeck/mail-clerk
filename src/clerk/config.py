"""Config file and accounts registry.

Config lives at $CLERK_CONFIG_DIR or `platformdirs.user_config_dir('clerk')`.
- config.toml: security settings, OAuth client IDs, capability tokens (hashed).
- accounts.json: list of authenticated accounts (no secrets).
OAuth refresh tokens are stored in macOS Keychain, not in these files.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Literal

from platformdirs import user_config_dir
from pydantic import BaseModel, Field

Provider = Literal["microsoft", "google"]
Scope = Literal["mail:read", "mail:write", "calendar:read", "calendar:write"]
ALL_SCOPES: tuple[Scope, ...] = ("mail:read", "mail:write", "calendar:read", "calendar:write")


class TokenRecord(BaseModel):
    id: str
    secret_hash: str  # bcrypt hash of the raw token secret
    scopes: list[Scope]
    created_at: str  # ISO 8601
    note: str = ""


class SecuritySettings(BaseModel):
    global_write_enabled: bool = True
    require_token: bool = True


class MicrosoftOAuth(BaseModel):
    client_id: str = ""
    # "common" supports both work/school and personal MS accounts.
    authority: str = "https://login.microsoftonline.com/common"


class GoogleOAuth(BaseModel):
    client_id: str = ""
    client_secret: str = ""  # not actually secret for installed-app flow per Google docs


class OAuthSettings(BaseModel):
    microsoft: MicrosoftOAuth = Field(default_factory=MicrosoftOAuth)
    google: GoogleOAuth = Field(default_factory=GoogleOAuth)


class Config(BaseModel):
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    oauth: OAuthSettings = Field(default_factory=OAuthSettings)
    tokens: list[TokenRecord] = Field(default_factory=list)


class Account(BaseModel):
    id: str  # stable local id, e.g. "microsoft:alice@example.com"
    provider: Provider
    email: str
    display_name: str = ""


class AccountsRegistry(BaseModel):
    accounts: list[Account] = Field(default_factory=list)


def config_dir() -> Path:
    override = os.environ.get("CLERK_CONFIG_DIR")
    path = Path(override) if override else Path(user_config_dir("clerk"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / "config.toml"


def accounts_path() -> Path:
    return config_dir() / "accounts.json"


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        cfg = Config()
        save_config(cfg)
        return cfg
    with path.open("rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)


def save_config(cfg: Config) -> None:
    # tomllib is read-only; write our own simple TOML.
    path = config_path()
    path.write_text(_render_toml(cfg))


def load_accounts() -> AccountsRegistry:
    path = accounts_path()
    if not path.exists():
        reg = AccountsRegistry()
        save_accounts(reg)
        return reg
    data = json.loads(path.read_text())
    return AccountsRegistry.model_validate(data)


def save_accounts(reg: AccountsRegistry) -> None:
    accounts_path().write_text(json.dumps(reg.model_dump(), indent=2) + "\n")


def _render_toml(cfg: Config) -> str:
    lines: list[str] = []
    lines.append("[security]")
    lines.append(f"global_write_enabled = {str(cfg.security.global_write_enabled).lower()}")
    lines.append(f"require_token = {str(cfg.security.require_token).lower()}")
    lines.append("")
    lines.append("[oauth.microsoft]")
    lines.append(f'client_id = "{cfg.oauth.microsoft.client_id}"')
    lines.append(f'authority = "{cfg.oauth.microsoft.authority}"')
    lines.append("")
    lines.append("[oauth.google]")
    lines.append(f'client_id = "{cfg.oauth.google.client_id}"')
    lines.append(f'client_secret = "{cfg.oauth.google.client_secret}"')
    lines.append("")
    for t in cfg.tokens:
        lines.append("[[tokens]]")
        lines.append(f'id = "{_esc(t.id)}"')
        lines.append(f'secret_hash = "{_esc(t.secret_hash)}"')
        scopes = ", ".join(f'"{s}"' for s in t.scopes)
        lines.append(f"scopes = [{scopes}]")
        lines.append(f'created_at = "{_esc(t.created_at)}"')
        if t.note:
            lines.append(f'note = "{_esc(t.note)}"')
        lines.append("")
    return "\n".join(lines)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')
