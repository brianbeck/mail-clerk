"""End-to-end WRITE-path integration test for attachments.

Self-sends a marker message with a small attachment, locates it, downloads the
attachment, verifies bytes round-trip identically, then moves the message to
Trash. Per the project's safety rules, every send is to `account.email` only.

Gated by `CLERK_INTEGRATION=1` AND `CLERK_INTEGRATION_WRITE=1`.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from clerk.config import load_accounts, load_config
from clerk.providers import factory
from clerk.providers.base import Attachment, OutgoingMessage
from clerk.search import SearchQuery

pytestmark = pytest.mark.integration

SUBJECT_MARKER = "[clerk-test DELETE attach]"


@pytest.fixture(autouse=True)
def _gate():
    if os.environ.get("CLERK_INTEGRATION") != "1":
        pytest.skip("set CLERK_INTEGRATION=1")
    if os.environ.get("CLERK_INTEGRATION_WRITE") != "1":
        pytest.skip("set CLERK_INTEGRATION_WRITE=1")


def _accounts():
    reg = load_accounts()
    assert reg.accounts, "no accounts configured"
    return reg.accounts


def test_self_send_with_attachment_round_trip():
    cfg = load_config()
    fake_content = b"clerk-test attachment payload " + uuid.uuid4().bytes
    fake_filename = "clerk-test.bin"

    for account in _accounts():
        provider = factory.mail_provider(account, cfg)
        marker = f"{SUBJECT_MARKER} {uuid.uuid4().hex[:8]}"

        out = OutgoingMessage(
            to=[account.email],  # self-send only
            subject=marker,
            body="Self-test with an attachment. Safe to delete.",
            attachments=[
                Attachment(filename=fake_filename, content=fake_content, mime_type="application/octet-stream"),
            ],
        )
        provider.send(out)

        # Find the message via recent listing.
        found_id = None
        for _ in range(15):
            results = provider.search(SearchQuery(), limit=30, include_trash=True)
            for m in results:
                if marker in m.subject:
                    found_id = m.id
                    break
            if found_id:
                break
            time.sleep(3)

        assert found_id is not None, (
            f"Did not find self-sent attachment marker {marker!r} for {account.email}"
        )

        try:
            full = provider.get(found_id)
            assert full.attachments, f"No attachments parsed on {account.email}"
            att_meta = full.attachments[0]
            assert att_meta.filename == fake_filename

            downloaded = provider.get_attachment(found_id, att_meta.id)
            assert downloaded.content == fake_content, (
                f"Attachment bytes did not round-trip on {account.email}"
            )
        finally:
            provider.delete(found_id)
