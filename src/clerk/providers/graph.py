"""Microsoft Graph mail + calendar provider (read path)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import httpx

import base64

from clerk.models import AttachmentSummary, Event, EventFull, Message, MessageFull
from clerk.providers.base import Attachment, EventPatch, OutgoingEvent, OutgoingMessage
from clerk.search import SearchQuery

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphMailProvider:
    def __init__(self, account_id: str, token_provider: Callable[[], str]):
        self.account_id = account_id
        self._token_provider = token_provider
        self._client = httpx.Client(base_url=GRAPH_BASE, timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token_provider()}",
            # ImmutableId makes message + event IDs stable across folder moves
            # (sent items → archive → trash) which is what we want for
            # send-then-delete round-trips.
            "Prefer": 'IdType="ImmutableId"',
        }

    def search(
        self,
        query: SearchQuery,
        limit: int,
        include_trash: bool = False,
        include_body: bool = False,
    ) -> list[Message]:
        # Graph's /me/messages searches across all folders by default, including
        # Deleted Items, so `include_trash` is a no-op here. Kept for interface
        # symmetry with Gmail.
        del include_trash
        params = build_mail_search_params(query, limit)
        resp = self._client.get("/me/messages", params=params, headers=self._headers())
        resp.raise_for_status()
        items = resp.json().get("value", [])
        if include_body:
            # The list response already carries the full `body` — parsing it as a
            # full message costs zero extra API calls. Attachment metadata is NOT
            # populated here (that needs a separate /attachments call per message);
            # use `mail read` if you need the attachment list.
            return [parse_message_full(self.account_id, m) for m in items]
        return [parse_message_summary(self.account_id, m) for m in items]

    def get(self, message_id: str) -> MessageFull:
        resp = self._client.get(f"/me/messages/{message_id}", headers=self._headers())
        resp.raise_for_status()
        msg = parse_message_full(self.account_id, resp.json())
        # Attachments are listed by a separate endpoint.
        if resp.json().get("hasAttachments"):
            att_resp = self._client.get(
                f"/me/messages/{message_id}/attachments",
                params={"$select": "id,name,contentType,size"},
                headers=self._headers(),
            )
            att_resp.raise_for_status()
            msg.attachments = [
                AttachmentSummary(
                    id=a["id"],
                    filename=a.get("name", "attachment"),
                    mime_type=a.get("contentType", "application/octet-stream"),
                    size_bytes=int(a.get("size", 0)),
                )
                for a in att_resp.json().get("value", [])
            ]
        return msg

    def get_attachment(self, message_id: str, attachment_id: str) -> Attachment:
        resp = self._client.get(
            f"/me/messages/{message_id}/attachments/{attachment_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        content_bytes = data.get("contentBytes", "")
        return Attachment(
            filename=data.get("name", "attachment"),
            content=base64.b64decode(content_bytes) if content_bytes else b"",
            mime_type=data.get("contentType", "application/octet-stream"),
        )

    def send(self, msg: OutgoingMessage) -> str | None:
        payload = build_send_payload(msg)
        resp = self._client.post(
            "/me/sendMail", json=payload, headers=self._headers()
        )
        resp.raise_for_status()
        # /sendMail returns 202 Accepted with no body — no message id available.
        return None

    def reply(self, message_id: str, body: str, is_html: bool = False) -> str | None:
        # Graph's /reply endpoint handles threading and recipient inference.
        payload: dict = {
            "comment": body,
        }
        if is_html:
            # /reply with `comment` is plaintext only. For HTML, use createReply
            # then update + send. Skip in v1 — plaintext reply is fine.
            raise ValueError("HTML reply is not supported by Graph /reply; send plaintext.")
        resp = self._client.post(
            f"/me/messages/{message_id}/reply",
            json=payload,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return None

    def delete(self, message_id: str) -> None:
        # Graph DELETE moves the message to the Deleted Items folder (not permanent).
        resp = self._client.delete(
            f"/me/messages/{message_id}", headers=self._headers()
        )
        resp.raise_for_status()


class GraphCalendarProvider:
    def __init__(self, account_id: str, token_provider: Callable[[], str]):
        self.account_id = account_id
        self._token_provider = token_provider
        self._client = httpx.Client(base_url=GRAPH_BASE, timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token_provider()}",
            # ImmutableId makes message + event IDs stable across folder moves
            # (sent items → archive → trash) which is what we want for
            # send-then-delete round-trips.
            "Prefer": 'IdType="ImmutableId"',
        }

    def list_events(self, start: datetime, end: datetime, limit: int) -> list[Event]:
        params = {
            "startDateTime": _isoformat_utc(start),
            "endDateTime": _isoformat_utc(end),
            "$orderby": "start/dateTime",
            "$top": str(limit),
        }
        resp = self._client.get(
            "/me/calendarView", params=params, headers=self._headers()
        )
        resp.raise_for_status()
        return [parse_event_summary(self.account_id, e) for e in resp.json().get("value", [])]

    def get_event(self, event_id: str) -> EventFull:
        resp = self._client.get(f"/me/events/{event_id}", headers=self._headers())
        resp.raise_for_status()
        return parse_event_full(self.account_id, resp.json())

    def create_event(self, event: OutgoingEvent) -> str:
        payload = build_event_create_payload(event)
        resp = self._client.post("/me/events", json=payload, headers=self._headers())
        resp.raise_for_status()
        return resp.json()["id"]

    def update_event(self, event_id: str, patch: EventPatch) -> None:
        body = build_event_patch_payload(patch)
        if not body:
            return  # nothing to update
        resp = self._client.patch(
            f"/me/events/{event_id}", json=body, headers=self._headers()
        )
        resp.raise_for_status()

    def cancel_event(self, event_id: str) -> None:
        # DELETE on /me/events/{id} removes the event from the organizer's calendar
        # and (if there are attendees) sends cancellation notices.
        resp = self._client.delete(f"/me/events/{event_id}", headers=self._headers())
        resp.raise_for_status()


# ---------- pure functions: query building + response parsing + send payload ----------


GRAPH_INLINE_ATTACHMENT_MAX_BYTES = 3 * 1024 * 1024  # 3 MB per Graph docs


def build_send_payload(msg: OutgoingMessage) -> dict:
    """Build the JSON body for Graph /me/sendMail."""
    message: dict = {
        "subject": msg.subject,
        "body": {
            "contentType": "html" if msg.is_html else "text",
            "content": msg.body,
        },
        "toRecipients": [_recipient(a) for a in msg.to],
        "ccRecipients": [_recipient(a) for a in msg.cc],
        "bccRecipients": [_recipient(a) for a in msg.bcc],
    }
    if msg.attachments:
        attachments: list[dict] = []
        for att in msg.attachments:
            if len(att.content) > GRAPH_INLINE_ATTACHMENT_MAX_BYTES:
                raise ValueError(
                    f"Attachment {att.filename!r} exceeds Graph inline send limit "
                    f"of {GRAPH_INLINE_ATTACHMENT_MAX_BYTES // (1024*1024)} MB. "
                    "Larger attachments require the upload session API (not yet supported)."
                )
            attachments.append(
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att.filename,
                    "contentType": att.mime_type,
                    "contentBytes": base64.b64encode(att.content).decode("ascii"),
                }
            )
        message["attachments"] = attachments
    return {"message": message, "saveToSentItems": True}


def _recipient(address: str) -> dict:
    return {"emailAddress": {"address": address}}


def build_event_create_payload(event: OutgoingEvent) -> dict:
    payload: dict = {
        "subject": event.title,
        "body": {"contentType": "text", "content": event.body},
        "start": _graph_event_dt(event.start, event.is_all_day),
        "end": _graph_event_dt(event.end, event.is_all_day),
    }
    if event.is_all_day:
        payload["isAllDay"] = True
    if event.location:
        payload["location"] = {"displayName": event.location}
    if event.attendees:
        payload["attendees"] = [
            {"emailAddress": {"address": a}, "type": "required"} for a in event.attendees
        ]
    if event.recurrence_rule:
        payload["recurrence"] = rrule_to_graph(event.recurrence_rule, event.start)
    return payload


def build_event_patch_payload(patch: EventPatch) -> dict:
    body: dict = {}
    is_all_day = bool(patch.is_all_day)  # for dt formatting
    if patch.title is not None:
        body["subject"] = patch.title
    if patch.start is not None:
        body["start"] = _graph_event_dt(patch.start, is_all_day)
    if patch.end is not None:
        body["end"] = _graph_event_dt(patch.end, is_all_day)
    if patch.body is not None:
        body["body"] = {"contentType": "text", "content": patch.body}
    if patch.location is not None:
        body["location"] = {"displayName": patch.location}
    if patch.attendees is not None:
        body["attendees"] = [
            {"emailAddress": {"address": a}, "type": "required"} for a in patch.attendees
        ]
    if patch.is_all_day is not None:
        body["isAllDay"] = patch.is_all_day
    if patch.recurrence_rule is not None and patch.start is not None:
        if patch.recurrence_rule:
            body["recurrence"] = rrule_to_graph(patch.recurrence_rule, patch.start)
        else:
            body["recurrence"] = None  # clear recurrence
    return body


def _graph_event_dt(dt: datetime, all_day: bool) -> dict:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    if all_day:
        return {
            "dateTime": utc.strftime("%Y-%m-%dT00:00:00"),
            "timeZone": "UTC",
        }
    return {"dateTime": utc.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}


# Back-compat alias for tests that referenced _graph_dt.
def _graph_dt(dt: datetime) -> dict:
    return _graph_event_dt(dt, all_day=False)


# ---------- recurrence: RRULE → Graph structured pattern ----------

_GRAPH_DAYS = {
    "MO": "monday",
    "TU": "tuesday",
    "WE": "wednesday",
    "TH": "thursday",
    "FR": "friday",
    "SA": "saturday",
    "SU": "sunday",
}


def rrule_to_graph(rrule: str, start: datetime) -> dict:
    """Translate a minimal RFC 5545 RRULE into Microsoft Graph's structured form.

    Supports: FREQ (DAILY|WEEKLY|MONTHLY|YEARLY), INTERVAL, BYDAY (weekly only),
    BYMONTHDAY (monthly), and either COUNT or UNTIL. Other RRULE fields are ignored.
    """
    parts: dict[str, str] = {}
    for token in rrule.replace("RRULE:", "").split(";"):
        if "=" in token:
            k, v = token.split("=", 1)
            parts[k.upper()] = v

    freq = parts.get("FREQ", "DAILY").lower()
    interval = int(parts.get("INTERVAL", "1"))

    pattern: dict = {"type": freq, "interval": interval}
    if freq == "weekly":
        days = parts.get("BYDAY", "")
        if days:
            pattern["daysOfWeek"] = [_GRAPH_DAYS[d.strip()] for d in days.split(",")]
        else:
            pattern["daysOfWeek"] = [_GRAPH_DAYS[_weekday_short(start.weekday())]]
        pattern["firstDayOfWeek"] = "sunday"
    elif freq == "monthly":
        if "BYMONTHDAY" in parts:
            pattern["dayOfMonth"] = int(parts["BYMONTHDAY"])
        else:
            pattern["dayOfMonth"] = start.day
    elif freq == "yearly":
        pattern["month"] = start.month
        pattern["dayOfMonth"] = start.day
    # daily needs no extra fields.

    start_date_iso = start.astimezone(timezone.utc).date().isoformat()
    range_: dict = {"type": "noEnd", "startDate": start_date_iso}
    if "COUNT" in parts:
        range_ = {
            "type": "numbered",
            "startDate": start_date_iso,
            "numberOfOccurrences": int(parts["COUNT"]),
        }
    elif "UNTIL" in parts:
        until = parts["UNTIL"]
        # UNTIL can be YYYYMMDD or YYYYMMDDTHHMMSSZ.
        date_part = until[:8]
        range_ = {
            "type": "endDate",
            "startDate": start_date_iso,
            "endDate": f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}",
        }

    return {"pattern": pattern, "range": range_}


def _weekday_short(weekday_idx: int) -> str:
    """0=Monday → 'MO', ..., 6=Sunday → 'SU'."""
    return ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][weekday_idx]


def build_mail_search_params(query: SearchQuery, limit: int) -> dict[str, str]:
    """Translate a SearchQuery into Graph $search params.

    Graph's `$search` uses KQL: `from:foo`, `subject:bar`, `received>=YYYY-MM-DD`,
    plus bare terms for full-text. The whole expression is wrapped in double quotes;
    inner double quotes are NOT supported (Graph returns 400), so multi-word values
    use parenthesis grouping (`subject:(foo bar)`) instead.
    """
    parts: list[str] = []
    if query.from_:
        parts.append(f"from:{query.from_}")
    if query.to:
        parts.append(f"to:{query.to}")
    if query.subject:
        parts.append(f"subject:{_kql_value(query.subject)}")
    if query.after:
        parts.append(f"received>={query.after.isoformat()}")
    if query.before:
        parts.append(f"received<={query.before.isoformat()}")
    if query.text:
        parts.append(query.text)

    params: dict[str, str] = {"$top": str(limit)}
    if parts:
        params["$search"] = '"' + " ".join(parts) + '"'
    else:
        params["$orderby"] = "receivedDateTime desc"
    return params


def _kql_value(value: str) -> str:
    """Wrap a KQL value in parentheses if it contains spaces/specials.

    KQL parentheses group tokens without introducing nested quotes that Graph
    rejects. Characters that confuse KQL parsing ([ ] : etc.) cause us to fall
    back to a freetext approximation by dropping the value into parens.
    """
    if not value:
        return ""
    needs_grouping = any(c in value for c in ' \t[]"\'')
    return f"({value})" if needs_grouping else value


def parse_message_summary(account_id: str, m: dict) -> Message:
    sender = m.get("from", {}).get("emailAddress", {}) if m.get("from") else {}
    return Message(
        account_id=account_id,
        provider="microsoft",
        id=m["id"],
        thread_id=m.get("conversationId"),
        **{"from": _format_addr(sender)},
        to=[_format_addr(r.get("emailAddress", {})) for r in m.get("toRecipients", [])],
        subject=m.get("subject", "") or "",
        date=_parse_dt(m.get("receivedDateTime")),
        snippet=m.get("bodyPreview", "") or "",
        unread=not m.get("isRead", True),
        tags=m.get("categories", []) or [],
    )


def parse_message_full(account_id: str, m: dict) -> MessageFull:
    summary = parse_message_summary(account_id, m)
    body = m.get("body", {}) or {}
    body_text = body.get("content", "") if body.get("contentType") == "text" else ""
    body_html = body.get("content", "") if body.get("contentType") == "html" else ""
    return MessageFull(
        **summary.model_dump(by_alias=True),
        body_text=body_text,
        body_html=body_html,
        cc=[_format_addr(r.get("emailAddress", {})) for r in m.get("ccRecipients", [])],
        bcc=[_format_addr(r.get("emailAddress", {})) for r in m.get("bccRecipients", [])],
    )


def parse_event_summary(account_id: str, e: dict) -> Event:
    start = e.get("start", {}) or {}
    end = e.get("end", {}) or {}
    organizer = e.get("organizer", {}).get("emailAddress", {}) if e.get("organizer") else {}
    # `seriesMasterId` is set on occurrences of a recurring series; the master
    # event itself does not have it. We surface it for callers that need to know.
    return Event(
        account_id=account_id,
        provider="microsoft",
        id=e["id"],
        title=e.get("subject", "") or "",
        start=_parse_graph_event_dt(start),
        end=_parse_graph_event_dt(end),
        location=(e.get("location", {}) or {}).get("displayName", "") or "",
        organizer=_format_addr(organizer),
        is_all_day=bool(e.get("isAllDay", False)),
        recurring_master_id=e.get("seriesMasterId") or None,
    )


def parse_event_full(account_id: str, e: dict) -> EventFull:
    summary = parse_event_summary(account_id, e)
    body = e.get("body", {}) or {}
    attendees = [
        _format_addr(a.get("emailAddress", {}))
        for a in e.get("attendees", []) or []
    ]
    online = (e.get("onlineMeeting") or {}).get("joinUrl", "") or ""
    return EventFull(
        **summary.model_dump(),
        body_text=body.get("content", "") or "",
        attendees=attendees,
        online_meeting_url=online,
    )


# ---------- helpers ----------


def _format_addr(addr: dict) -> str:
    email = addr.get("address", "") or ""
    name = addr.get("name", "") or ""
    if name and email and name != email:
        return f"{name} <{email}>"
    return email or name


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # Graph returns ISO 8601 with 'Z' suffix.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_graph_event_dt(d: dict) -> datetime:
    raw = d.get("dateTime")
    if not raw:
        raise ValueError(f"Event missing dateTime: {d}")
    tz_name = d.get("timeZone", "UTC")
    # Graph returns dateTime without offset; UTC timezone is most common.
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None and tz_name == "UTC":
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _isoformat_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
