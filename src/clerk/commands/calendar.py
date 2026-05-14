"""`clerk calendar` subcommands (read path: list, get)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import typer

from clerk import permissions
from clerk.config import Account, load_accounts, load_config
from clerk.permissions import PermissionDenied
from clerk.providers import factory
from clerk.providers.base import EventPatch, OutgoingEvent

app = typer.Typer(
    help="List and read calendar events across configured accounts.", no_args_is_help=True
)


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


def _gate_or_die(op_kind: str, token: str | None) -> None:
    cfg = load_config()
    try:
        permissions.check(cfg, "calendar", op_kind, token=permissions.resolve_token(token))  # type: ignore[arg-type]
    except PermissionDenied as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


_REL_RE = re.compile(r"^([+-]?\d+)([dhwm])$")


def parse_when(spec: str, *, now: datetime | None = None) -> datetime:
    """Parse a date/time spec into a tz-aware UTC datetime.

    Accepts:
      - 'now'
      - 'today' (00:00 UTC)
      - 'tomorrow' (+1d 00:00 UTC)
      - '+7d', '-3d', '+2w', '+8h', '+30m'  (relative to now)
      - ISO 8601 ('2026-05-13', '2026-05-13T09:00', '2026-05-13T09:00-04:00')
    """
    now = now or datetime.now(timezone.utc)
    s = spec.strip().lower()
    if s == "now":
        return now
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if s == "tomorrow":
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return base + timedelta(days=1)

    m = _REL_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {
            "m": timedelta(minutes=n),
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
            "w": timedelta(weeks=n),
        }[unit]
        return now + delta

    # Fall through to ISO.
    try:
        dt = datetime.fromisoformat(spec)
    except ValueError as e:
        raise ValueError(f"Cannot parse date {spec!r}: {e}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@app.command("list")
def cmd_list(
    from_: str = typer.Option("now", "--from", help="Start time (ISO, relative, or 'now'/'today')."),
    to: str = typer.Option("+7d", "--to", help="End time (ISO, relative)."),
    account: Optional[list[str]] = typer.Option(
        None, "--account", help="Restrict to account id/email. Repeatable."
    ),
    limit: int = typer.Option(50, "--limit", help="Max events per account."),
    utc: bool = typer.Option(False, "--utc", help="Display dates in UTC instead of local time."),
    json_out: bool = typer.Option(False, "--json"),
    token: Optional[str] = typer.Option(None, "--token"),
) -> None:
    """List calendar events in a time window."""
    _gate_or_die("read", token)
    try:
        start = parse_when(from_)
        end = parse_when(to)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None

    accounts = _resolve_accounts(account)
    cfg = load_config()
    results, errors = factory.fanout_calendar_list(
        accounts, cfg, lambda p: p.list_events(start, end, limit)
    )

    if json_out:
        typer.echo(json.dumps([e.model_dump(mode="json") for e in results], indent=2))
    else:
        _render_events_table(results, utc=utc)

    for account_id, exc in errors:
        typer.secho(f"warn: {account_id}: {exc}", fg=typer.colors.YELLOW, err=True)


@app.command("get")
def cmd_get(
    event_id: str = typer.Argument(..., help="Provider-native event id."),
    account: str = typer.Option(..., "--account"),
    json_out: bool = typer.Option(False, "--json"),
    token: Optional[str] = typer.Option(None, "--token"),
) -> None:
    """Read a single event from a specific account."""
    _gate_or_die("read", token)
    accounts = _resolve_accounts([account])
    cfg = load_config()
    provider = factory.calendar_provider(accounts[0], cfg)
    event = provider.get_event(event_id)

    if json_out:
        typer.echo(json.dumps(event.model_dump(mode="json"), indent=2))
        return

    typer.echo(f"Title:     {event.title}")
    typer.echo(f"When:      {event.start.isoformat()} → {event.end.isoformat()}")
    if event.location:
        typer.echo(f"Location:  {event.location}")
    typer.echo(f"Organizer: {event.organizer}")
    if event.attendees:
        typer.echo(f"Attendees: {', '.join(event.attendees)}")
    if event.online_meeting_url:
        typer.echo(f"Join:      {event.online_meeting_url}")
    if event.body_text:
        typer.echo("")
        typer.echo(event.body_text)


def _render_events_table(events, *, utc: bool = False) -> None:
    if not events:
        typer.echo("No events.")
        return
    tz_label = _local_tz_label(utc)
    typer.echo(f"  (times shown in {tz_label})")
    for e in events:
        when = _format_event_when(e, utc=utc)
        typer.echo(f"{when}  [{e.account_id}]  {e.title}")
        typer.echo(f"    id={e.id}")


def _format_event_when(event, *, utc: bool) -> str:
    if event.is_all_day:
        return event.start.date().isoformat() + " (all day)"
    dt = event.start.astimezone(timezone.utc) if utc else event.start.astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def _local_tz_label(utc: bool) -> str:
    if utc:
        return "UTC"
    tz = datetime.now().astimezone().tzinfo
    name = tz.tzname(datetime.now()) if tz else "local"
    return name or "local"


@app.command("create")
def cmd_create(
    account: str = typer.Option(..., "--account", help="Organizer account id or email."),
    title: str = typer.Option(..., "--title"),
    start: str = typer.Option(..., "--start", help="Start time (ISO or relative)."),
    end: str = typer.Option(..., "--end", help="End time (ISO or relative)."),
    location: str = typer.Option("", "--location"),
    body: str = typer.Option("", "--body", help="Event description."),
    attendee: list[str] = typer.Option(
        [],
        "--attendee",
        help="Attendee email(s). Repeatable. Attendees will receive an invite.",
    ),
    all_day: bool = typer.Option(
        False,
        "--all-day",
        help="All-day event. End date is exclusive (end = day after last all-day).",
    ),
    recurrence: str = typer.Option(
        "",
        "--recurrence",
        help="RRULE (RFC 5545), e.g. 'FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10'.",
    ),
    token: Optional[str] = typer.Option(None, "--token"),
) -> None:
    """Create a new calendar event."""
    _gate_or_die("write", token)
    try:
        start_dt = parse_when(start)
        end_dt = parse_when(end)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None
    if end_dt <= start_dt:
        typer.secho("--end must be after --start.", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    accounts = _resolve_accounts([account])
    cfg = load_config()
    provider = factory.calendar_provider(accounts[0], cfg)
    out = OutgoingEvent(
        title=title,
        start=start_dt,
        end=end_dt,
        body=body,
        location=location,
        attendees=attendee,
        is_all_day=all_day,
        recurrence_rule=recurrence,
    )
    event_id = provider.create_event(out)
    typer.secho(f"Created event id={event_id}", fg=typer.colors.GREEN)


@app.command("update")
def cmd_update(
    event_id: str = typer.Argument(..., help="Provider-native event id."),
    account: str = typer.Option(..., "--account"),
    title: Optional[str] = typer.Option(None, "--title"),
    start: Optional[str] = typer.Option(None, "--start"),
    end: Optional[str] = typer.Option(None, "--end"),
    location: Optional[str] = typer.Option(None, "--location"),
    body: Optional[str] = typer.Option(None, "--body"),
    attendee: Optional[list[str]] = typer.Option(
        None,
        "--attendee",
        help="Replace the attendee list with these. Pass once with an empty value "
        "to clear. Repeatable. Update does NOT re-send invites by default.",
    ),
    all_day: Optional[bool] = typer.Option(
        None, "--all-day/--not-all-day", help="Toggle all-day."
    ),
    recurrence: Optional[str] = typer.Option(
        None,
        "--recurrence",
        help="Set a new RRULE. Pass empty string to clear recurrence.",
    ),
    token: Optional[str] = typer.Option(None, "--token"),
) -> None:
    """Update one or more fields of an existing event."""
    _gate_or_die("write", token)
    patch = EventPatch()
    if title is not None:
        patch.title = title
    if start is not None:
        try:
            patch.start = parse_when(start)
        except ValueError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from None
    if end is not None:
        try:
            patch.end = parse_when(end)
        except ValueError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from None
    if location is not None:
        patch.location = location
    if body is not None:
        patch.body = body
    if attendee is not None:
        patch.attendees = attendee
    if all_day is not None:
        patch.is_all_day = all_day
    if recurrence is not None:
        patch.recurrence_rule = recurrence

    accounts = _resolve_accounts([account])
    cfg = load_config()
    provider = factory.calendar_provider(accounts[0], cfg)
    provider.update_event(event_id, patch)
    typer.secho(f"Updated {event_id}", fg=typer.colors.GREEN)


@app.command("cancel")
def cmd_cancel(
    event_id: str = typer.Argument(..., help="Provider-native event id."),
    account: str = typer.Option(..., "--account"),
    token: Optional[str] = typer.Option(None, "--token"),
) -> None:
    """Cancel/delete an event. If attendees exist, they receive cancellation notice."""
    _gate_or_die("write", token)
    accounts = _resolve_accounts([account])
    cfg = load_config()
    provider = factory.calendar_provider(accounts[0], cfg)
    provider.cancel_event(event_id)
    typer.secho(f"Cancelled {event_id}", fg=typer.colors.GREEN)
