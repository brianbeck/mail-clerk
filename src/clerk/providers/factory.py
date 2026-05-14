"""Build the right provider for an account, and fan out across accounts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, TypeVar

from clerk.auth import google as google_auth
from clerk.auth import microsoft as ms_auth
from clerk.config import Account, Config
from clerk.models import Event, Message
from clerk.providers.base import CalendarProvider, MailProvider
from clerk.providers.gmail import GmailProvider, GoogleCalendarProvider
from clerk.providers.graph import GraphCalendarProvider, GraphMailProvider

T = TypeVar("T")


def mail_provider(account: Account, cfg: Config) -> MailProvider:
    if account.provider == "microsoft":
        return GraphMailProvider(
            account.id,
            lambda: ms_auth.get_access_token(cfg.oauth.microsoft, account),
        )
    if account.provider == "google":
        return GmailProvider(
            account.id,
            lambda: google_auth.get_access_token(cfg.oauth.google, account),
        )
    raise ValueError(f"Unknown provider: {account.provider}")


def calendar_provider(account: Account, cfg: Config) -> CalendarProvider:
    if account.provider == "microsoft":
        return GraphCalendarProvider(
            account.id,
            lambda: ms_auth.get_access_token(cfg.oauth.microsoft, account),
        )
    if account.provider == "google":
        return GoogleCalendarProvider(
            account.id,
            lambda: google_auth.get_access_token(cfg.oauth.google, account),
        )
    raise ValueError(f"Unknown provider: {account.provider}")


def fanout_mail_search(
    accounts: list[Account],
    cfg: Config,
    fn: Callable[[MailProvider], list[Message]],
) -> tuple[list[Message], list[tuple[str, Exception]]]:
    """Run `fn` against each account's mail provider in parallel.

    Returns (merged sorted results, errors). Errors are returned, not raised,
    so a single bad account doesn't kill the entire search.
    """
    results: list[Message] = []
    errors: list[tuple[str, Exception]] = []
    if not accounts:
        return results, errors

    with ThreadPoolExecutor(max_workers=min(len(accounts), 8)) as pool:
        futures = {pool.submit(fn, mail_provider(a, cfg)): a for a in accounts}
        for fut in as_completed(futures):
            account = futures[fut]
            try:
                results.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                errors.append((account.id, e))

    results.sort(key=_message_sort_key, reverse=True)
    return results, errors


def fanout_calendar_list(
    accounts: list[Account],
    cfg: Config,
    fn: Callable[[CalendarProvider], list[Event]],
) -> tuple[list[Event], list[tuple[str, Exception]]]:
    results: list[Event] = []
    errors: list[tuple[str, Exception]] = []
    if not accounts:
        return results, errors

    with ThreadPoolExecutor(max_workers=min(len(accounts), 8)) as pool:
        futures = {pool.submit(fn, calendar_provider(a, cfg)): a for a in accounts}
        for fut in as_completed(futures):
            account = futures[fut]
            try:
                results.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                errors.append((account.id, e))

    results.sort(key=lambda e: e.start)
    return results, errors


def _message_sort_key(m: Message) -> datetime:
    return m.date or datetime.min.replace(tzinfo=timezone.utc)
