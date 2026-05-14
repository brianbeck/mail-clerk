"""Tests for the cross-provider search query parser."""

from __future__ import annotations

from datetime import date

import pytest

from clerk.search import parse


def test_empty_query():
    q = parse("")
    assert q.is_empty()
    assert q.text == ""


def test_freetext_only():
    q = parse("budget review")
    assert q.text == "budget review"
    assert q.from_ is None


def test_structured_terms():
    q = parse("from:alice@example.com subject:budget after:2026-01-01 before:2026-02-01")
    assert q.from_ == "alice@example.com"
    assert q.subject == "budget"
    assert q.after == date(2026, 1, 1)
    assert q.before == date(2026, 2, 1)
    assert q.text == ""


def test_structured_plus_freetext():
    q = parse("from:alice review notes")
    assert q.from_ == "alice"
    assert q.text == "review notes"


def test_quoted_subject_preserves_spaces():
    q = parse('subject:"quarterly budget"')
    assert q.subject == "quarterly budget"


def test_unknown_key_falls_through_to_freetext():
    q = parse("frobnicate:foo bar")
    assert q.from_ is None
    assert q.text == "frobnicate:foo bar"


def test_invalid_date():
    with pytest.raises(ValueError, match="after"):
        parse("after:nope")


def test_to_field():
    q = parse("to:bob@example.com")
    assert q.to == "bob@example.com"


def test_unbalanced_quotes():
    with pytest.raises(ValueError, match="Unbalanced quotes"):
        parse('subject:"unclosed')


def test_case_insensitive_keys():
    q = parse("From:alice Subject:hi")
    assert q.from_ == "alice"
    assert q.subject == "hi"
