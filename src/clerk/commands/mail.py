"""`clerk mail` subcommands (read + write)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

import mimetypes

from clerk import permissions, search
from clerk.config import Account, load_accounts, load_config
from clerk.permissions import PermissionDenied
from clerk.providers import factory
from clerk.providers.base import Attachment, OutgoingMessage

app = typer.Typer(help="Search and read mail across configured accounts.", no_args_is_help=True)


def _resolve_accounts(account_filter: Optional[list[str]]) -> list[Account]:
    reg = load_accounts()
    if not account_filter:
        return reg.accounts
    by_id = {a.id: a for a in reg.accounts}
    by_email = {a.email: a for a in reg.accounts}
    out: list[Account] = []
    for f in account_filter:
        match = by_id.get(f) or by_email.get(f)
        if not match:
            raise typer.BadParameter(f"No account matching {f!r}.")
        out.append(match)
    return out


def _gate_or_die(resource, op_kind, token: str | None) -> None:
    cfg = load_config()
    try:
        permissions.check(cfg, resource, op_kind, token=permissions.resolve_token(token))
    except PermissionDenied as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@app.command("search")
def cmd_search(
    query: str = typer.Argument("", help="Search query, e.g. 'from:alice subject:budget'"),
    account: Optional[list[str]] = typer.Option(
        None,
        "--account",
        help="Restrict to specific account ids or emails. Repeatable. Default: all.",
    ),
    limit: int = typer.Option(20, "--limit", help="Max results per account."),
    include_trash: bool = typer.Option(
        False,
        "--include-trash",
        help="Include trashed/spam messages. Gmail excludes them by default; "
        "Microsoft Graph already includes them.",
    ),
    utc: bool = typer.Option(
        False, "--utc", help="Display dates in UTC instead of local time."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
    token: Optional[str] = typer.Option(None, "--token", help="Capability token."),
) -> None:
    """Search mail across one or more accounts."""
    _gate_or_die("mail", "read", token)

    try:
        parsed = search.parse(query)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None

    accounts = _resolve_accounts(account)
    cfg = load_config()
    results, errors = factory.fanout_mail_search(
        accounts, cfg, lambda p: p.search(parsed, limit, include_trash=include_trash)
    )

    if json_out:
        typer.echo(json.dumps([m.model_dump(mode="json", by_alias=True) for m in results], indent=2))
    else:
        _render_messages_table(results, utc=utc)

    for account_id, exc in errors:
        typer.secho(f"warn: {account_id}: {exc}", fg=typer.colors.YELLOW, err=True)


@app.command("read")
def cmd_read(
    message_id: str = typer.Argument(..., help="Provider-native message id."),
    account: str = typer.Option(..., "--account", help="Account id or email."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of formatted."),
    token: Optional[str] = typer.Option(None, "--token", help="Capability token."),
) -> None:
    """Read a single message from a specific account."""
    _gate_or_die("mail", "read", token)
    accounts = _resolve_accounts([account])
    cfg = load_config()
    provider = factory.mail_provider(accounts[0], cfg)
    msg = provider.get(message_id)

    if json_out:
        typer.echo(json.dumps(msg.model_dump(mode="json", by_alias=True), indent=2))
        return

    typer.echo(f"From:    {msg.from_}")
    typer.echo(f"To:      {', '.join(msg.to)}")
    if msg.cc:
        typer.echo(f"Cc:      {', '.join(msg.cc)}")
    typer.echo(f"Date:    {msg.date.isoformat() if msg.date else '?'}")
    typer.echo(f"Subject: {msg.subject}")
    if msg.attachments:
        typer.echo(f"Attachments ({len(msg.attachments)}):")
        for a in msg.attachments:
            typer.echo(f"  - {a.filename}  ({a.mime_type}, {a.size_bytes} bytes)  id={a.id}")
    typer.echo("")
    typer.echo(msg.body_text or msg.body_html or "(empty body)")


def _render_messages_table(messages, *, utc: bool = False) -> None:
    if not messages:
        typer.echo("No results.")
        return
    tz_label = _local_tz_label(utc)
    typer.echo(f"  (times shown in {tz_label})")
    for m in messages:
        date_str = _format_dt(m.date, utc=utc)
        unread = "*" if m.unread else " "
        typer.echo(
            f"{unread} {date_str}  [{m.account_id}]  {_trim(m.from_, 30)}  {_trim(m.subject, 60)}"
        )
        typer.echo(f"    id={m.id}")


def _format_dt(dt, *, utc: bool) -> str:
    if dt is None:
        return "????-??-?? ??:??"
    from datetime import timezone

    if utc:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _local_tz_label(utc: bool) -> str:
    if utc:
        return "UTC"
    from datetime import datetime

    tz = datetime.now().astimezone().tzinfo
    name = tz.tzname(datetime.now()) if tz else "local"
    return name or "local"


def _trim(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


@app.command("send")
def cmd_send(
    account: str = typer.Option(..., "--account", help="Sending account id or email."),
    to: list[str] = typer.Option(..., "--to", help="Recipient(s). Repeatable."),
    subject: str = typer.Option(..., "--subject"),
    body: Optional[str] = typer.Option(None, "--body", help="Message body text."),
    body_file: Optional[Path] = typer.Option(
        None, "--body-file", help="Read body from a file."
    ),
    cc: list[str] = typer.Option([], "--cc", help="CC recipient(s). Repeatable."),
    bcc: list[str] = typer.Option([], "--bcc", help="BCC recipient(s). Repeatable."),
    html: bool = typer.Option(False, "--html", help="Treat body as HTML."),
    attach: list[Path] = typer.Option(
        [], "--attach", help="Attach file at this path. Repeatable."
    ),
    token: Optional[str] = typer.Option(None, "--token"),
) -> None:
    """Send a new message from the specified account."""
    _gate_or_die("mail", "write", token)

    if body is None and body_file is None:
        typer.secho("Provide --body or --body-file.", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    if body is not None and body_file is not None:
        typer.secho("Use only one of --body and --body-file.", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    body_text = body if body is not None else body_file.read_text()

    attachments: list[Attachment] = []
    for path in attach:
        if not path.is_file():
            typer.secho(f"Attachment not found: {path}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        guessed_type, _ = mimetypes.guess_type(path.name)
        attachments.append(
            Attachment(
                filename=path.name,
                content=path.read_bytes(),
                mime_type=guessed_type or "application/octet-stream",
            )
        )

    accounts = _resolve_accounts([account])
    cfg = load_config()
    provider = factory.mail_provider(accounts[0], cfg)
    out = OutgoingMessage(
        to=to, subject=subject, body=body_text, cc=cc, bcc=bcc, is_html=html,
        attachments=attachments,
    )
    try:
        new_id = provider.send(out)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None

    if new_id:
        typer.secho(f"Sent (id={new_id})", fg=typer.colors.GREEN)
    else:
        typer.secho("Sent", fg=typer.colors.GREEN)


@app.command("reply")
def cmd_reply(
    message_id: str = typer.Argument(..., help="Provider-native message id to reply to."),
    account: str = typer.Option(..., "--account"),
    body: Optional[str] = typer.Option(None, "--body"),
    body_file: Optional[Path] = typer.Option(None, "--body-file"),
    html: bool = typer.Option(False, "--html"),
    token: Optional[str] = typer.Option(None, "--token"),
) -> None:
    """Reply to a message. Recipients are inferred from the original."""
    _gate_or_die("mail", "write", token)

    if body is None and body_file is None:
        typer.secho("Provide --body or --body-file.", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    if body is not None and body_file is not None:
        typer.secho("Use only one of --body and --body-file.", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    body_text = body if body is not None else body_file.read_text()

    accounts = _resolve_accounts([account])
    cfg = load_config()
    provider = factory.mail_provider(accounts[0], cfg)
    try:
        new_id = provider.reply(message_id, body_text, is_html=html)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None

    typer.secho(f"Replied{' (id=' + new_id + ')' if new_id else ''}", fg=typer.colors.GREEN)


@app.command("delete")
def cmd_delete(
    message_id: str = typer.Argument(..., help="Provider-native message id."),
    account: str = typer.Option(..., "--account"),
    token: Optional[str] = typer.Option(None, "--token"),
) -> None:
    """Move a message to Trash / Deleted Items."""
    _gate_or_die("mail", "write", token)
    accounts = _resolve_accounts([account])
    cfg = load_config()
    provider = factory.mail_provider(accounts[0], cfg)
    provider.delete(message_id)
    typer.secho(f"Moved to trash: {message_id}", fg=typer.colors.GREEN)


@app.command("attachment")
def cmd_attachment(
    message_id: str = typer.Argument(..., help="Provider-native message id."),
    attachment_id: str = typer.Argument(..., help="Provider-native attachment id."),
    account: str = typer.Option(..., "--account"),
    save: Optional[Path] = typer.Option(
        None,
        "--save",
        help="Save to this path (or directory). If a directory, the original "
        "filename is preserved. Default: write to stdout (binary).",
    ),
    token: Optional[str] = typer.Option(None, "--token"),
) -> None:
    """Download an attachment from a message."""
    _gate_or_die("mail", "read", token)
    accounts = _resolve_accounts([account])
    cfg = load_config()
    provider = factory.mail_provider(accounts[0], cfg)
    att = provider.get_attachment(message_id, attachment_id)

    if save is None:
        import sys
        sys.stdout.buffer.write(att.content)
        return

    target = save / att.filename if save.is_dir() else save
    target.write_bytes(att.content)
    typer.secho(f"Saved {len(att.content)} bytes to {target}", fg=typer.colors.GREEN)
