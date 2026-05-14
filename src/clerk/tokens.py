"""Capability tokens: create, list, revoke, verify.

A token's wire form is `clk_<id>_<secret>`, where `id` is a short opaque handle
used to look up the bcrypt-hashed secret in config. This avoids bcrypt-checking
every stored token on every request.

The raw secret is shown ONCE at creation time and never stored or recoverable.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import bcrypt

from clerk.config import ALL_SCOPES, Config, Scope, TokenRecord, load_config, save_config

TOKEN_PREFIX = "clk_"


@dataclass
class CreatedToken:
    id: str
    raw: str  # full wire form `clk_<id>_<secret>`; show once
    record: TokenRecord


def create(scopes: list[Scope], note: str = "") -> CreatedToken:
    for s in scopes:
        if s not in ALL_SCOPES:
            raise ValueError(f"Unknown scope: {s!r}. Valid: {', '.join(ALL_SCOPES)}")

    token_id = secrets.token_hex(4)  # 8 hex chars
    secret = secrets.token_urlsafe(32)
    secret_hash = bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode()
    record = TokenRecord(
        id=token_id,
        secret_hash=secret_hash,
        scopes=scopes,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        note=note,
    )

    cfg = load_config()
    cfg.tokens.append(record)
    save_config(cfg)

    raw = f"{TOKEN_PREFIX}{token_id}_{secret}"
    return CreatedToken(id=token_id, raw=raw, record=record)


def revoke(token_id: str) -> bool:
    cfg = load_config()
    before = len(cfg.tokens)
    cfg.tokens = [t for t in cfg.tokens if t.id != token_id]
    if len(cfg.tokens) == before:
        return False
    save_config(cfg)
    return True


def verify(raw: str, cfg: Config | None = None) -> TokenRecord | None:
    """Return the matching TokenRecord if `raw` is a valid capability token, else None."""
    if not raw.startswith(TOKEN_PREFIX):
        return None
    body = raw[len(TOKEN_PREFIX):]
    parts = body.split("_", 1)
    if len(parts) != 2:
        return None
    token_id, secret = parts

    if cfg is None:
        cfg = load_config()
    record = next((t for t in cfg.tokens if t.id == token_id), None)
    if record is None:
        return None
    if not bcrypt.checkpw(secret.encode(), record.secret_hash.encode()):
        return None
    return record
