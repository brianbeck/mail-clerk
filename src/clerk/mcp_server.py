"""MCP stdio server exposing clerk's mail + calendar operations as MCP tools.

Designed for agent consumers (Claude Code, other MCP clients). One tool per
CLI operation. Each tool runs through the same permission gate as the CLI.

Token resolution:
  The capability token is read from CLERK_TOKEN at server startup. The MCP
  client never sees or passes the token; that's the operator's concern, set
  before launching the server.

Run with:
  clerk mcp serve
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

import base64

from clerk import permissions, search as search_mod
from clerk.commands.calendar import parse_when
from clerk.config import Account, load_accounts, load_config
from clerk.permissions import PermissionDenied
from clerk.providers import factory
from clerk.providers.base import Attachment, EventPatch, OutgoingEvent, OutgoingMessage

mcp = FastMCP("clerk")


# ---------- helpers ----------


def _token() -> str | None:
    return permissions.resolve_token(None)


def _check(resource: str, op_kind: str) -> None:
    cfg = load_config()
    try:
        permissions.check(cfg, resource, op_kind, token=_token())  # type: ignore[arg-type]
    except PermissionDenied as e:
        raise PermissionError(str(e)) from None


def _resolve_accounts(account_filter: list[str] | None) -> list[Account]:
    reg = load_accounts()
    if not account_filter:
        return reg.accounts
    by_id = {a.id: a for a in reg.accounts}
    by_email = {a.email: a for a in reg.accounts}
    out: list[Account] = []
    for f in account_filter:
        match = by_id.get(f) or by_email.get(f)
        if not match:
            raise ValueError(f"No account matching {f!r}")
        out.append(match)
    return out


# ---------- mail tools ----------


@mcp.tool()
def accounts_list() -> list[dict]:
    """List the configured mail/calendar accounts."""
    reg = load_accounts()
    return [a.model_dump() for a in reg.accounts]


@mcp.tool()
def mail_search(
    query: Annotated[str, Field(description="Search query, e.g. 'from:alice subject:budget'")] = "",
    accounts: Annotated[
        list[str] | None,
        Field(description="Account id(s) or email(s). Default: all configured accounts."),
    ] = None,
    limit: Annotated[int, Field(description="Max results per account.", ge=1, le=100)] = 20,
    include_trash: Annotated[
        bool, Field(description="Include trashed/spam messages (Gmail default-excludes them).")
    ] = False,
) -> list[dict]:
    """Search mail across one or more configured accounts. Returns lightweight metadata."""
    _check("mail", "read")
    parsed = search_mod.parse(query)
    resolved = _resolve_accounts(accounts)
    cfg = load_config()
    results, _errors = factory.fanout_mail_search(
        resolved, cfg, lambda p: p.search(parsed, limit, include_trash=include_trash)
    )
    return [m.model_dump(mode="json", by_alias=True) for m in results]


@mcp.tool()
def mail_read(
    message_id: Annotated[str, Field(description="Provider-native message id.")],
    account: Annotated[str, Field(description="Account id or email.")],
) -> dict:
    """Read a single message, including body, from a specific account."""
    _check("mail", "read")
    acct = _resolve_accounts([account])[0]
    cfg = load_config()
    provider = factory.mail_provider(acct, cfg)
    msg = provider.get(message_id)
    return msg.model_dump(mode="json", by_alias=True)


@mcp.tool()
def mail_send(
    account: Annotated[str, Field(description="Sending account id or email.")],
    to: Annotated[list[str], Field(description="Recipient address(es).")],
    subject: Annotated[str, Field(description="Subject line.")],
    body: Annotated[str, Field(description="Message body text (or HTML if is_html=true).")],
    cc: Annotated[list[str] | None, Field(description="CC recipients.")] = None,
    bcc: Annotated[list[str] | None, Field(description="BCC recipients.")] = None,
    is_html: Annotated[bool, Field(description="Treat body as HTML.")] = False,
    attachments: Annotated[
        list[dict] | None,
        Field(
            description="Attachments. Each: {filename, content_base64, mime_type}. "
            "Graph caps inline attachments at 3 MB; larger raises an error."
        ),
    ] = None,
) -> dict:
    """Send a new email. Requires mail:write scope and global writes enabled."""
    _check("mail", "write")
    acct = _resolve_accounts([account])[0]
    cfg = load_config()
    provider = factory.mail_provider(acct, cfg)

    parsed_attachments: list[Attachment] = []
    for att in attachments or []:
        try:
            content = base64.b64decode(att["content_base64"])
        except KeyError as e:
            raise ValueError(f"Attachment missing required field: {e}") from None
        parsed_attachments.append(
            Attachment(
                filename=att.get("filename", "attachment"),
                content=content,
                mime_type=att.get("mime_type", "application/octet-stream"),
            )
        )

    out = OutgoingMessage(
        to=to,
        subject=subject,
        body=body,
        cc=cc or [],
        bcc=bcc or [],
        is_html=is_html,
        attachments=parsed_attachments,
    )
    new_id = provider.send(out)
    return {"ok": True, "id": new_id}


@mcp.tool()
def mail_get_attachment(
    message_id: Annotated[str, Field(description="Provider-native message id.")],
    attachment_id: Annotated[str, Field(description="Provider-native attachment id.")],
    account: Annotated[str, Field(description="Account id or email.")],
) -> dict:
    """Download an attachment. Returns base64-encoded content + metadata."""
    _check("mail", "read")
    acct = _resolve_accounts([account])[0]
    cfg = load_config()
    provider = factory.mail_provider(acct, cfg)
    att = provider.get_attachment(message_id, attachment_id)
    return {
        "filename": att.filename,
        "mime_type": att.mime_type,
        "size_bytes": len(att.content),
        "content_base64": base64.b64encode(att.content).decode("ascii"),
    }


@mcp.tool()
def mail_reply(
    message_id: Annotated[str, Field(description="Provider-native id of the message to reply to.")],
    account: Annotated[str, Field(description="Replying account id or email.")],
    body: Annotated[str, Field(description="Reply body.")],
    is_html: Annotated[bool, Field(description="Treat body as HTML (Gmail only).")] = False,
) -> dict:
    """Reply to a message. Recipients are inferred from the original; threading is preserved."""
    _check("mail", "write")
    acct = _resolve_accounts([account])[0]
    cfg = load_config()
    provider = factory.mail_provider(acct, cfg)
    new_id = provider.reply(message_id, body, is_html=is_html)
    return {"ok": True, "id": new_id}


@mcp.tool()
def mail_delete(
    message_id: Annotated[str, Field(description="Provider-native message id.")],
    account: Annotated[str, Field(description="Account id or email.")],
) -> dict:
    """Move a message to Trash / Deleted Items (not permanent delete)."""
    _check("mail", "write")
    acct = _resolve_accounts([account])[0]
    cfg = load_config()
    provider = factory.mail_provider(acct, cfg)
    provider.delete(message_id)
    return {"ok": True}


# ---------- calendar tools ----------


@mcp.tool()
def calendar_list(
    start: Annotated[
        str, Field(description="Start time (ISO 8601 or relative: 'now', 'today', '+7d').")
    ] = "now",
    end: Annotated[str, Field(description="End time (ISO 8601 or relative).")] = "+7d",
    accounts: Annotated[
        list[str] | None, Field(description="Restrict to specific accounts.")
    ] = None,
    limit: Annotated[int, Field(description="Max events per account.", ge=1, le=200)] = 50,
) -> list[dict]:
    """List calendar events in a time window across configured accounts."""
    _check("calendar", "read")
    start_dt = parse_when(start)
    end_dt = parse_when(end)
    resolved = _resolve_accounts(accounts)
    cfg = load_config()
    results, _errors = factory.fanout_calendar_list(
        resolved, cfg, lambda p: p.list_events(start_dt, end_dt, limit)
    )
    return [e.model_dump(mode="json") for e in results]


@mcp.tool()
def calendar_get(
    event_id: Annotated[str, Field(description="Provider-native event id.")],
    account: Annotated[str, Field(description="Account id or email.")],
) -> dict:
    """Read a single calendar event in full."""
    _check("calendar", "read")
    acct = _resolve_accounts([account])[0]
    cfg = load_config()
    provider = factory.calendar_provider(acct, cfg)
    return provider.get_event(event_id).model_dump(mode="json")


@mcp.tool()
def calendar_create(
    account: Annotated[str, Field(description="Organizer account id or email.")],
    title: Annotated[str, Field(description="Event title.")],
    start: Annotated[str, Field(description="Start time (ISO 8601 or relative).")],
    end: Annotated[str, Field(description="End time. Must be after start.")],
    body: Annotated[str, Field(description="Description / body.")] = "",
    location: Annotated[str, Field(description="Location string.")] = "",
    attendees: Annotated[
        list[str] | None,
        Field(description="Attendee email(s). Each receives an invite."),
    ] = None,
    is_all_day: Annotated[
        bool, Field(description="All-day event. End date is exclusive.")
    ] = False,
    recurrence_rule: Annotated[
        str,
        Field(description="RRULE (RFC 5545), e.g. 'FREQ=WEEKLY;BYDAY=MO;COUNT=10'."),
    ] = "",
) -> dict:
    """Create a calendar event. With no attendees, no invitation emails are sent."""
    _check("calendar", "write")
    start_dt = parse_when(start)
    end_dt = parse_when(end)
    if end_dt <= start_dt:
        raise ValueError("end must be after start")
    acct = _resolve_accounts([account])[0]
    cfg = load_config()
    provider = factory.calendar_provider(acct, cfg)
    out = OutgoingEvent(
        title=title,
        start=start_dt,
        end=end_dt,
        body=body,
        location=location,
        attendees=attendees or [],
        is_all_day=is_all_day,
        recurrence_rule=recurrence_rule,
    )
    event_id = provider.create_event(out)
    return {"ok": True, "id": event_id}


@mcp.tool()
def calendar_update(
    event_id: Annotated[str, Field(description="Provider-native event id.")],
    account: Annotated[str, Field(description="Organizer account id or email.")],
    title: Annotated[str | None, Field(description="New title.")] = None,
    start: Annotated[str | None, Field(description="New start time.")] = None,
    end: Annotated[str | None, Field(description="New end time.")] = None,
    body: Annotated[str | None, Field(description="New description.")] = None,
    location: Annotated[str | None, Field(description="New location.")] = None,
    attendees: Annotated[
        list[str] | None, Field(description="Replace attendee list. Updates do NOT re-send invites.")
    ] = None,
) -> dict:
    """Update one or more fields of an existing event. Attendees are not re-notified."""
    _check("calendar", "write")
    patch = EventPatch()
    if title is not None:
        patch.title = title
    if start is not None:
        patch.start = parse_when(start)
    if end is not None:
        patch.end = parse_when(end)
    if body is not None:
        patch.body = body
    if location is not None:
        patch.location = location
    if attendees is not None:
        patch.attendees = attendees
    acct = _resolve_accounts([account])[0]
    cfg = load_config()
    provider = factory.calendar_provider(acct, cfg)
    provider.update_event(event_id, patch)
    return {"ok": True}


@mcp.tool()
def calendar_cancel(
    event_id: Annotated[str, Field(description="Provider-native event id.")],
    account: Annotated[str, Field(description="Account id or email.")],
) -> dict:
    """Cancel/delete an event. If attendees exist, they receive cancellation notice."""
    _check("calendar", "write")
    acct = _resolve_accounts([account])[0]
    cfg = load_config()
    provider = factory.calendar_provider(acct, cfg)
    provider.cancel_event(event_id)
    return {"ok": True}


def run() -> None:
    """Entry point: launch the MCP server over stdio."""
    # Validate startup config: if a token is required but not present, fail loudly
    # BEFORE we start accepting MCP traffic (so the user sees the error).
    cfg = load_config()
    if cfg.security.require_token and not _token():
        msg = (
            "CLERK_TOKEN is not set, but security.require_token = true. "
            "Either set CLERK_TOKEN before launching the server, or disable the "
            "token gate with: clerk config set security.require_token false"
        )
        # Print to stderr so it shows up in the user's launcher; do NOT print to
        # stdout, which is the MCP channel.
        import sys

        print(msg, file=sys.stderr)
        sys.exit(2)

    mcp.run(transport="stdio")


__all__ = ["mcp", "run"]
