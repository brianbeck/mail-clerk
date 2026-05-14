"""End-to-end WRITE-path integration tests.

SAFETY RULES (also enforced by tests/integration/test_safety_guard.py):
  - Every send targets `account.email` only (self-send). No external recipients.
  - Every test cleans up its artifact (move-to-trash after creating).
  - Calendar tests do not include external attendees.
  - Gated by BOTH `CLERK_INTEGRATION=1` AND `CLERK_INTEGRATION_WRITE=1`.

Run with:
  CLERK_INTEGRATION=1 CLERK_INTEGRATION_WRITE=1 pytest tests/integration/test_e2e_write.py -v
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from clerk.config import load_accounts, load_config
from clerk.providers import factory
from clerk.providers.base import OutgoingMessage
from clerk.search import SearchQuery

pytestmark = pytest.mark.integration

SUBJECT_MARKER = "[clerk-test DELETE]"


@pytest.fixture(autouse=True)
def _gate():
    if os.environ.get("CLERK_INTEGRATION") != "1":
        pytest.skip("set CLERK_INTEGRATION=1 to run integration tests")
    if os.environ.get("CLERK_INTEGRATION_WRITE") != "1":
        pytest.skip("set CLERK_INTEGRATION_WRITE=1 to run write-path integration tests")


def _accounts():
    reg = load_accounts()
    assert reg.accounts, "no accounts configured; run `clerk auth add` first"
    return reg.accounts


def test_self_send_then_trash_for_each_account():
    """Send a marker message to oneself, find it via search, then trash it."""
    cfg = load_config()
    for account in _accounts():
        provider = factory.mail_provider(account, cfg)

        marker = f"{SUBJECT_MARKER} {uuid.uuid4().hex[:8]}"
        out = OutgoingMessage(
            to=[account.email],  # SELF-SEND ONLY
            subject=marker,
            body=f"This is a self-test message for {account.email}. Safe to delete.",
        )
        provider.send(out)

        # Find by listing recent messages and matching the marker substring.
        # Recent-list is more reliable than a KQL/Gmail-q search against a
        # subject containing brackets and punctuation, and doesn't depend on
        # the search index catching up.
        found_id = None
        for _ in range(15):
            results = provider.search(SearchQuery(), limit=30)
            for m in results:
                if marker in m.subject:
                    found_id = m.id
                    break
            if found_id:
                break
            time.sleep(3)

        assert found_id is not None, (
            f"Could not find self-sent marker {marker!r} for {account.email}"
        )
        provider.delete(found_id)
