"""Normalized models shared across providers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Provider = Literal["microsoft", "google"]


class Message(BaseModel):
    """Lightweight message metadata returned from search."""

    account_id: str
    provider: Provider
    id: str  # provider-native id (Graph message id, Gmail message id)
    thread_id: str | None = None
    from_: str = Field(default="", alias="from")
    to: list[str] = Field(default_factory=list)
    subject: str = ""
    date: datetime | None = None
    snippet: str = ""
    unread: bool = False
    tags: list[str] = Field(default_factory=list)  # Gmail labels / Outlook categories

    model_config = {"populate_by_name": True}


class AttachmentSummary(BaseModel):
    """Metadata about an attachment, without the binary content."""

    id: str  # provider-native attachment id
    filename: str
    mime_type: str
    size_bytes: int


class MessageFull(Message):
    """Full message body returned from `mail read`."""

    body_text: str = ""
    body_html: str = ""
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    attachments: list[AttachmentSummary] = Field(default_factory=list)


class Event(BaseModel):
    """Lightweight calendar event metadata."""

    account_id: str
    provider: Provider
    id: str
    title: str = ""
    start: datetime
    end: datetime
    location: str = ""
    organizer: str = ""
    is_all_day: bool = False
    # If this event is an occurrence of a recurring series, the master series id.
    # None means this is a standalone event (or the master itself).
    recurring_master_id: str | None = None


class EventFull(Event):
    body_text: str = ""
    attendees: list[str] = Field(default_factory=list)
    online_meeting_url: str = ""
