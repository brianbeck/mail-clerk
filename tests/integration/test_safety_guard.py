"""Static safety check: integration tests must not target external recipients.

This test parses tests/integration/*.py and verifies that no string literal
looks like an external email address. Self-send patterns use `account.email`
attribute access, which is fine.

This is a belt-and-suspenders defense. The real safety comes from authors
following the rule in tests/integration/test_e2e_write.py docstring.
"""

from __future__ import annotations

import re
from pathlib import Path

EMAIL_LITERAL = re.compile(r'["\']([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)["\']')

INTEGRATION_DIR = Path(__file__).parent


def test_no_email_literals_in_integration_tests():
    offenders: list[tuple[str, str]] = []
    for path in INTEGRATION_DIR.glob("*.py"):
        if path.name == "test_safety_guard.py":
            continue
        text = path.read_text()
        for match in EMAIL_LITERAL.finditer(text):
            address = match.group(1)
            offenders.append((path.name, address))

    assert not offenders, (
        "Integration tests must not contain email-address string literals "
        "(use `account.email` instead). Found: " + ", ".join(f"{p}: {a}" for p, a in offenders)
    )
