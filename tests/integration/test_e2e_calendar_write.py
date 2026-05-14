"""End-to-end WRITE-path integration tests for calendar.

SAFETY: events are created with NO attendees (organizer-only). With zero
attendees, both Graph and Google Calendar suppress all invitation emails -
nobody is notified, nothing leaves the user's calendar.

Gated by `CLERK_INTEGRATION=1` AND `CLERK_INTEGRATION_WRITE=1`.

Run with:
  CLERK_INTEGRATION=1 CLERK_INTEGRATION_WRITE=1 pytest tests/integration/test_e2e_calendar_write.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from clerk.config import load_accounts, load_config
from clerk.providers import factory
from clerk.providers.base import EventPatch, OutgoingEvent

pytestmark = pytest.mark.integration

TITLE_MARKER = "[clerk-test DELETE]"


@pytest.fixture(autouse=True)
def _gate():
    if os.environ.get("CLERK_INTEGRATION") != "1":
        pytest.skip("set CLERK_INTEGRATION=1 to run integration tests")
    if os.environ.get("CLERK_INTEGRATION_WRITE") != "1":
        pytest.skip("set CLERK_INTEGRATION_WRITE=1 to run write-path integration tests")


def _accounts():
    reg = load_accounts()
    assert reg.accounts, "no accounts configured"
    return reg.accounts


def test_create_update_cancel_lifecycle_for_each_account():
    """Create a marker event (no attendees), update its title, then cancel it."""
    cfg = load_config()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now + timedelta(days=30)  # well into the future to avoid noise
    end = start + timedelta(minutes=30)

    for account in _accounts():
        provider = factory.calendar_provider(account, cfg)
        marker = f"{TITLE_MARKER} {uuid.uuid4().hex[:8]}"

        out = OutgoingEvent(
            title=marker,
            start=start,
            end=end,
            body="Self-test event; safe to delete.",
            attendees=[],  # CRITICAL: no attendees = no invite emails
        )
        event_id = provider.create_event(out)
        assert event_id

        try:
            new_marker = marker + " (updated)"
            provider.update_event(event_id, EventPatch(title=new_marker))

            # Verify the update applied by reading the event back.
            fetched = provider.get_event(event_id)
            assert fetched.title == new_marker
        finally:
            # Always cancel, even if the update assertion failed.
            provider.cancel_event(event_id)


def test_all_day_event_for_each_account():
    """Create an all-day event, verify is_all_day round-trips, cancel."""
    cfg = load_config()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    # Far future + truncate to date.
    start = (now + timedelta(days=45)).replace(hour=0, minute=0, second=0)
    end = start + timedelta(days=1)  # end exclusive; 1-day all-day event

    for account in _accounts():
        provider = factory.calendar_provider(account, cfg)
        marker = f"{TITLE_MARKER} ALL-DAY {uuid.uuid4().hex[:8]}"

        out = OutgoingEvent(
            title=marker,
            start=start,
            end=end,
            attendees=[],
            is_all_day=True,
        )
        event_id = provider.create_event(out)
        assert event_id
        try:
            fetched = provider.get_event(event_id)
            assert fetched.is_all_day is True, f"is_all_day did not round-trip on {account.email}"
        finally:
            provider.cancel_event(event_id)


def test_recurring_event_for_each_account():
    """Create a weekly recurring event (3 occurrences, no attendees), cancel master."""
    cfg = load_config()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now + timedelta(days=60)  # far in the future
    end = start + timedelta(minutes=30)

    for account in _accounts():
        provider = factory.calendar_provider(account, cfg)
        marker = f"{TITLE_MARKER} RECUR {uuid.uuid4().hex[:8]}"

        out = OutgoingEvent(
            title=marker,
            start=start,
            end=end,
            attendees=[],
            recurrence_rule="FREQ=WEEKLY;COUNT=3",
        )
        event_id = provider.create_event(out)
        assert event_id
        try:
            # Verify we can read the master back.
            fetched = provider.get_event(event_id)
            assert marker in fetched.title
        finally:
            # Cancelling the master cancels all occurrences.
            provider.cancel_event(event_id)
