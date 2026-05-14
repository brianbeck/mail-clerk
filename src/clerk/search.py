"""Cross-provider search query parsing.

A `SearchQuery` is a structured form that each provider translates to its native
search syntax (Graph KQL via $search, Gmail q-syntax). Input is a free-form string
with optional `key:value` tokens for `from`, `to`, `subject`, `after`, `before`.

Supported forms:
  "alice budget"
  "from:alice@example.com subject:budget"
  "from:alice after:2026-01-01 before:2026-02-01 budget"

Quoting:
  "from:'alice example' subject:budget"     -> single-quoted value
  'from:"alice example" subject:budget'     -> double-quoted value
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from datetime import date

KEY_TOKENS = {"from", "to", "subject", "after", "before"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class SearchQuery:
    from_: str | None = None
    to: str | None = None
    subject: str | None = None
    after: date | None = None
    before: date | None = None
    text: str = ""
    extras: list[str] = field(default_factory=list)  # leftover free terms

    def is_empty(self) -> bool:
        return not any([self.from_, self.to, self.subject, self.after, self.before, self.text])


def parse(raw: str) -> SearchQuery:
    """Parse a raw query string into a structured SearchQuery."""
    q = SearchQuery()
    if not raw.strip():
        return q

    try:
        tokens = shlex.split(raw)
    except ValueError as e:
        raise ValueError(f"Unbalanced quotes in query: {e}") from None

    free_terms: list[str] = []
    for tok in tokens:
        key, sep, value = tok.partition(":")
        if sep and key.lower() in KEY_TOKENS and value:
            _apply_key(q, key.lower(), value)
        else:
            free_terms.append(tok)

    q.text = " ".join(free_terms)
    return q


def _apply_key(q: SearchQuery, key: str, value: str) -> None:
    match key:
        case "from":
            q.from_ = value
        case "to":
            q.to = value
        case "subject":
            q.subject = value
        case "after":
            q.after = _parse_date(value, "after")
        case "before":
            q.before = _parse_date(value, "before")


def _parse_date(value: str, key: str) -> date:
    if not _DATE_RE.match(value):
        raise ValueError(f"{key}: expected YYYY-MM-DD, got {value!r}")
    return date.fromisoformat(value)
