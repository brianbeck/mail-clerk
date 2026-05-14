"""Permission gate.

Every mail/calendar operation passes through `check()` before executing.
Three rules, evaluated in order:

  1. Writes require `security.global_write_enabled = true`. Master kill-switch.
  2. If `security.require_token = true`, a valid capability token must be supplied.
  3. The token's scopes must cover the requested `<resource>:<op>` (e.g. `mail:write`).

When `require_token = false`, the token check is skipped entirely — any shell user
on this machine gains full read+write (subject only to the global write toggle).
"""

from __future__ import annotations

import os
from typing import Literal

from clerk.config import Config, Scope
from clerk.tokens import verify

OpKind = Literal["read", "write"]
Resource = Literal["mail", "calendar"]

TOKEN_ENV = "CLERK_TOKEN"


class PermissionDenied(Exception):
    pass


def resolve_token(explicit: str | None) -> str | None:
    """Token resolution order: explicit (--token flag) > CLERK_TOKEN env var."""
    if explicit:
        return explicit
    env_val = os.environ.get(TOKEN_ENV)
    return env_val or None


def check(
    cfg: Config,
    resource: Resource,
    op_kind: OpKind,
    token: str | None = None,
) -> None:
    """Raise PermissionDenied if the operation is not allowed; return None otherwise."""
    if op_kind == "write" and not cfg.security.global_write_enabled:
        raise PermissionDenied(
            "Writes are globally disabled (security.global_write_enabled = false). "
            "Re-enable with: clerk config set security.global_write_enabled true"
        )

    if not cfg.security.require_token:
        return

    if not token:
        raise PermissionDenied(
            f"A capability token is required. Pass --token, set ${TOKEN_ENV}, "
            f"or disable the gate with: clerk config set security.require_token false"
        )

    record = verify(token, cfg)
    if record is None:
        raise PermissionDenied("Invalid or unknown capability token.")

    required: Scope = f"{resource}:{op_kind}"  # type: ignore[assignment]
    if required not in record.scopes:
        raise PermissionDenied(
            f"Token {record.id!r} lacks the required scope {required!r}. "
            f"Has: {', '.join(record.scopes) or '(none)'}"
        )
