"""Provider interfaces. Each authenticated account is served by one MailProvider
and one CalendarProvider implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from clerk.models import Event, EventFull, Message, MessageFull
from clerk.search import SearchQuery


@dataclass
class Attachment:
    """A binary attachment (used for both send and download)."""

    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"


@dataclass
class OutgoingMessage:
    to: list[str]
    subject: str
    body: str
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    is_html: bool = False
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class OutgoingEvent:
    title: str
    start: datetime
    end: datetime
    body: str = ""
    location: str = ""
    attendees: list[str] = field(default_factory=list)
    is_all_day: bool = False
    recurrence_rule: str = ""  # RFC 5545 RRULE, e.g. "FREQ=WEEKLY;BYDAY=MO;COUNT=10"


@dataclass
class EventPatch:
    """Partial event update. Any field set to a non-None value is applied;
    None means "leave unchanged"."""

    title: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    body: str | None = None
    location: str | None = None
    attendees: list[str] | None = None
    is_all_day: bool | None = None
    recurrence_rule: str | None = None


class MailProvider(Protocol):
    def search(
        self, query: SearchQuery, limit: int, include_trash: bool = False
    ) -> list[Message]: ...
    def get(self, message_id: str) -> MessageFull: ...
    def send(self, msg: OutgoingMessage) -> str | None: ...
    def reply(self, message_id: str, body: str, is_html: bool = False) -> str | None: ...
    def delete(self, message_id: str) -> None: ...
    def get_attachment(self, message_id: str, attachment_id: str) -> Attachment: ...


class CalendarProvider(Protocol):
    def list_events(self, start: datetime, end: datetime, limit: int) -> list[Event]: ...
    def get_event(self, event_id: str) -> EventFull: ...
    def create_event(self, event: OutgoingEvent) -> str: ...
    def update_event(self, event_id: str, patch: EventPatch) -> None: ...
    def cancel_event(self, event_id: str) -> None: ...
