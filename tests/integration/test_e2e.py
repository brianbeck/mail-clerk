"""End-to-end integration tests against real APIs.

These tests:
  - require CLERK_INTEGRATION=1 in the environment to run (otherwise skipped),
  - read against the user's actual configured accounts via the normal config dir,
  - do not perform writes (read-only is safe to run automatically).

Run with:
  CLERK_INTEGRATION=1 pytest tests/integration -v
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from clerk.config import load_accounts, load_config
from clerk.providers import factory
from clerk.search import SearchQuery

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return os.environ.get("CLERK_INTEGRATION") == "1"


@pytest.fixture(autouse=True)
def _gate():
    if not _enabled():
        pytest.skip("set CLERK_INTEGRATION=1 to run integration tests")


def test_each_account_can_list_one_mail():
    reg = load_accounts()
    assert reg.accounts, "no accounts configured; run `clerk auth add` first"
    cfg = load_config()

    for account in reg.accounts:
        provider = factory.mail_provider(account, cfg)
        results = provider.search(SearchQuery(), limit=1)
        # Empty mailbox is acceptable; we only care that the call succeeds.
        assert isinstance(results, list)


def test_each_account_search_include_body_returns_full_messages():
    """include_body should return MessageFull instances in one call (no per-message
    read round-trips from the caller's perspective)."""
    from clerk.models import MessageFull

    reg = load_accounts()
    assert reg.accounts, "no accounts configured"
    cfg = load_config()

    for account in reg.accounts:
        provider = factory.mail_provider(account, cfg)
        results = provider.search(SearchQuery(), limit=2, include_body=True)
        assert isinstance(results, list)
        for m in results:
            assert isinstance(m, MessageFull), (
                f"{account.email}: include_body did not yield MessageFull"
            )
            # body_text/body_html attributes exist on the fast path (may be empty
            # for genuinely empty messages, but the fields must be present).
            assert hasattr(m, "body_text")
            assert hasattr(m, "body_html")


def test_each_account_can_list_calendar_window():
    reg = load_accounts()
    assert reg.accounts, "no accounts configured"
    cfg = load_config()

    start = datetime.now(timezone.utc)
    end = start + timedelta(days=14)

    for account in reg.accounts:
        provider = factory.calendar_provider(account, cfg)
        events = provider.list_events(start, end, limit=10)
        assert isinstance(events, list)
