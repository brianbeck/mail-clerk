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
