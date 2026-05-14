"""Gmail + Google Calendar provider (read path)."""

from __future__ import annotations

import base64
import email.policy
import email.utils
from datetime import datetime, timezone
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Callable

import httpx

from clerk.models import AttachmentSummary, Event, EventFull, Message, MessageFull
from clerk.providers.base import Attachment, EventPatch, OutgoingEvent, OutgoingMessage
from clerk.search import SearchQuery

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
GCAL_BASE = "https://www.googleapis.com/calendar/v3"


class GmailProvider:
    def __init__(self, account_id: str, token_provider: Callable[[], str]):
        self.account_id = account_id
        self._token_provider = token_provider
        self._client = httpx.Client(base_url=GMAIL_BASE, timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    def search(
        self, query: SearchQuery, limit: int, include_trash: bool = False
    ) -> list[Message]:
        q = build_gmail_query(query)
        params = {"maxResults": str(limit)}
        if q:
            params["q"] = q
        if include_trash:
            params["includeSpamTrash"] = "true"
        resp = self._client.get(
            "/users/me/messages", params=params, headers=self._headers()
        )
        resp.raise_for_status()
        ids = [m["id"] for m in resp.json().get("messages", [])]

        out: list[Message] = []
        for mid in ids:
            r = self._client.get(
                f"/users/me/messages/{mid}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "To", "Subject", "Date"],
                },
                headers=self._headers(),
            )
            r.raise_for_status()
            out.append(parse_message_summary(self.account_id, r.json()))
        return out

    def get(self, message_id: str) -> MessageFull:
        # format=full gives us a structured parts tree with explicit attachment
        # ids, which we need for the separate get_attachment fetch.
        resp = self._client.get(
            f"/users/me/messages/{message_id}",
            params={"format": "full"},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return parse_message_full_structured(self.account_id, resp.json())

    def get_attachment(self, message_id: str, attachment_id: str) -> Attachment:
        # Gmail's native attachmentId is NOT stable across message re-fetches —
        # each fetch returns different ids for the same logical attachment. So we
        # expose synthetic stable ids ("0", "1", ...) based on DFS order in the
        # parts tree, and resolve them to the CURRENT native id on demand.
        msg_resp = self._client.get(
            f"/users/me/messages/{message_id}",
            params={"format": "full"},
            headers=self._headers(),
        )
        msg_resp.raise_for_status()
        _, _, atts = _extract_message_parts(msg_resp.json().get("payload", {}))

        try:
            idx = int(attachment_id)
        except ValueError:
            raise ValueError(
                f"Invalid Gmail attachment id {attachment_id!r}; expected an integer index."
            ) from None
        if idx < 0 or idx >= len(atts):
            raise ValueError(
                f"Attachment index {idx} out of range (message has {len(atts)} attachments)."
            )

        part = atts[idx]
        resp = self._client.get(
            f"/users/me/messages/{message_id}/attachments/{part['gmail_id']}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        content = base64.urlsafe_b64decode(resp.json()["data"].encode("ascii"))
        return Attachment(
            filename=part["filename"],
            content=content,
            mime_type=part["mime_type"],
        )

    def send(self, msg: OutgoingMessage) -> str | None:
        raw = build_raw_message(msg)
        resp = self._client.post(
            "/users/me/messages/send",
            json={"raw": raw},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json().get("id")

    def reply(self, message_id: str, body: str, is_html: bool = False) -> str | None:
        # Fetch the original to extract Subject, From, Message-ID, threadId.
        meta_resp = self._client.get(
            f"/users/me/messages/{message_id}",
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "Subject", "Message-ID"],
            },
            headers=self._headers(),
        )
        meta_resp.raise_for_status()
        meta = meta_resp.json()
        headers = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
        thread_id = meta.get("threadId")

        orig_subject = headers.get("subject", "")
        reply_subject = (
            orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
        )
        reply_to = headers.get("from", "")
        orig_msgid = headers.get("message-id", "")

        out = OutgoingMessage(
            to=[reply_to] if reply_to else [],
            subject=reply_subject,
            body=body,
            is_html=is_html,
        )
        raw = build_raw_message(out, in_reply_to=orig_msgid, references=orig_msgid)
        resp = self._client.post(
            "/users/me/messages/send",
            json={"raw": raw, "threadId": thread_id} if thread_id else {"raw": raw},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json().get("id")

    def delete(self, message_id: str) -> None:
        # gmail.modify scope can trash (move to Trash) but not permanent-delete.
        resp = self._client.post(
            f"/users/me/messages/{message_id}/trash",
            headers=self._headers(),
        )
        resp.raise_for_status()


class GoogleCalendarProvider:
    def __init__(self, account_id: str, token_provider: Callable[[], str]):
        self.account_id = account_id
        self._token_provider = token_provider
        self._client = httpx.Client(base_url=GCAL_BASE, timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    def list_events(self, start: datetime, end: datetime, limit: int) -> list[Event]:
        params = {
            "timeMin": _rfc3339(start),
            "timeMax": _rfc3339(end),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(limit),
        }
        resp = self._client.get(
            "/calendars/primary/events", params=params, headers=self._headers()
        )
        resp.raise_for_status()
        return [parse_event_summary(self.account_id, e) for e in resp.json().get("items", [])]

    def get_event(self, event_id: str) -> EventFull:
        resp = self._client.get(
            f"/calendars/primary/events/{event_id}", headers=self._headers()
        )
        resp.raise_for_status()
        return parse_event_full(self.account_id, resp.json())

    def create_event(self, event: OutgoingEvent) -> str:
        payload = build_event_create_payload(event)
        # sendUpdates defaults to "false"; we explicitly set it based on whether
        # there are attendees. With zero attendees no invites are sent regardless.
        params = {"sendUpdates": "all" if event.attendees else "none"}
        resp = self._client.post(
            "/calendars/primary/events",
            params=params,
            json=payload,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def update_event(self, event_id: str, patch: EventPatch) -> None:
        body = build_event_patch_payload(patch)
        if not body:
            return
        # Don't spam attendees on update; default to "none". The user can re-send
        # if they really want via a separate channel.
        resp = self._client.patch(
            f"/calendars/primary/events/{event_id}",
            params={"sendUpdates": "none"},
            json=body,
            headers=self._headers(),
        )
        resp.raise_for_status()

    def cancel_event(self, event_id: str) -> None:
        # DELETE removes the event. sendUpdates controls whether cancellation
        # notices go to attendees. Default to "all" so external attendees know
        # the event was cancelled.
        resp = self._client.delete(
            f"/calendars/primary/events/{event_id}",
            params={"sendUpdates": "all"},
            headers=self._headers(),
        )
        resp.raise_for_status()


# ---------- pure functions: query building, response parsing, send raw ----------


def build_raw_message(
    msg: OutgoingMessage,
    *,
    in_reply_to: str = "",
    references: str = "",
) -> str:
    """Construct a base64url-encoded RFC822 message for Gmail's send endpoint."""
    em = EmailMessage()
    em["To"] = ", ".join(msg.to)
    if msg.cc:
        em["Cc"] = ", ".join(msg.cc)
    if msg.bcc:
        em["Bcc"] = ", ".join(msg.bcc)
    em["Subject"] = msg.subject
    if in_reply_to:
        em["In-Reply-To"] = in_reply_to
    if references:
        em["References"] = references

    if msg.is_html:
        em.set_content(msg.body, subtype="html")
    else:
        em.set_content(msg.body)

    for att in msg.attachments:
        main, _, sub = att.mime_type.partition("/")
        em.add_attachment(
            att.content,
            maintype=main or "application",
            subtype=sub or "octet-stream",
            filename=att.filename,
        )
    return base64.urlsafe_b64encode(bytes(em)).decode("ascii")


def build_event_create_payload(event: OutgoingEvent) -> dict:
    payload: dict = {
        "summary": event.title,
        "description": event.body,
        "start": _gcal_event_dt(event.start, event.is_all_day),
        "end": _gcal_event_dt(event.end, event.is_all_day),
    }
    if event.location:
        payload["location"] = event.location
    if event.attendees:
        payload["attendees"] = [{"email": a} for a in event.attendees]
    if event.recurrence_rule:
        payload["recurrence"] = [f"RRULE:{event.recurrence_rule}"]
    return payload


def build_event_patch_payload(patch: EventPatch) -> dict:
    body: dict = {}
    is_all_day = bool(patch.is_all_day)
    if patch.title is not None:
        body["summary"] = patch.title
    if patch.start is not None:
        body["start"] = _gcal_event_dt(patch.start, is_all_day)
    if patch.end is not None:
        body["end"] = _gcal_event_dt(patch.end, is_all_day)
    if patch.body is not None:
        body["description"] = patch.body
    if patch.location is not None:
        body["location"] = patch.location
    if patch.attendees is not None:
        body["attendees"] = [{"email": a} for a in patch.attendees]
    if patch.recurrence_rule is not None:
        body["recurrence"] = [f"RRULE:{patch.recurrence_rule}"] if patch.recurrence_rule else []
    return body


def _gcal_event_dt(dt: datetime, all_day: bool) -> dict:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if all_day:
        return {"date": dt.astimezone(timezone.utc).date().isoformat()}
    # Always emit timeZone alongside dateTime. Google requires this for recurring
    # events, and including it for non-recurring events is harmless.
    utc = dt.astimezone(timezone.utc)
    return {"dateTime": utc.isoformat(), "timeZone": "UTC"}


# Back-compat alias.
def _gcal_dt(dt: datetime) -> dict:
    return _gcal_event_dt(dt, all_day=False)


def build_gmail_query(query: SearchQuery) -> str:
    """Translate a SearchQuery into Gmail's q-syntax."""
    parts: list[str] = []
    if query.from_:
        parts.append(f"from:{query.from_}")
    if query.to:
        parts.append(f"to:{query.to}")
    if query.subject:
        parts.append(f'subject:"{query.subject}"')
    if query.after:
        # Gmail uses YYYY/MM/DD for after/before.
        parts.append(f"after:{query.after.strftime('%Y/%m/%d')}")
    if query.before:
        parts.append(f"before:{query.before.strftime('%Y/%m/%d')}")
    if query.text:
        parts.append(query.text)
    return " ".join(parts)


def parse_message_summary(account_id: str, m: dict) -> Message:
    """Parse a Gmail message in `format=metadata` form."""
    headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
    return Message(
        account_id=account_id,
        provider="google",
        id=m["id"],
        thread_id=m.get("threadId"),
        **{"from": headers.get("from", "")},
        to=_split_addrs(headers.get("to", "")),
        subject=headers.get("subject", ""),
        date=_parse_rfc2822(headers.get("date")),
        snippet=m.get("snippet", "") or "",
        unread="UNREAD" in (m.get("labelIds") or []),
        tags=[lbl for lbl in (m.get("labelIds") or []) if not lbl.startswith("CATEGORY_")],
    )


def parse_message_full_structured(account_id: str, m: dict) -> MessageFull:
    """Parse a Gmail message in `format=full` form (structured parts tree)."""
    payload = m.get("payload", {}) or {}
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    body_text, body_html, atts = _extract_message_parts(payload)
    attachments = [
        AttachmentSummary(
            id=str(i),  # synthetic, stable across fetches (DFS index)
            filename=a["filename"],
            mime_type=a["mime_type"],
            size_bytes=a["size"],
        )
        for i, a in enumerate(atts)
    ]

    return MessageFull(
        account_id=account_id,
        provider="google",
        id=m["id"],
        thread_id=m.get("threadId"),
        **{"from": headers.get("from", "")},
        to=_split_addrs(headers.get("to", "")),
        cc=_split_addrs(headers.get("cc", "")),
        bcc=_split_addrs(headers.get("bcc", "")),
        subject=headers.get("subject", ""),
        date=_parse_rfc2822(headers.get("date")),
        snippet=m.get("snippet", "") or "",
        unread="UNREAD" in (m.get("labelIds") or []),
        tags=[lbl for lbl in (m.get("labelIds") or []) if not lbl.startswith("CATEGORY_")],
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
    )


def _extract_message_parts(payload: dict) -> tuple[str, str, list[dict]]:
    """Walk the parts tree once. Return (body_text, body_html, attachments).

    Attachments are returned in DFS order with their *current* gmail_id so the
    caller can fetch bytes immediately (Gmail's native attachmentId is ephemeral).
    """
    body_text = ""
    body_html = ""
    attachments: list[dict] = []

    def walk(part: dict) -> None:
        nonlocal body_text, body_html
        mime = part.get("mimeType", "")
        filename = part.get("filename", "") or ""
        body = part.get("body", {}) or {}

        if part.get("parts"):
            for child in part["parts"]:
                walk(child)
            return

        if filename and body.get("attachmentId"):
            attachments.append(
                {
                    "gmail_id": body["attachmentId"],
                    "filename": filename,
                    "mime_type": mime or "application/octet-stream",
                    "size": int(body.get("size", 0)),
                }
            )
            return

        data = body.get("data")
        if data:
            decoded = base64.urlsafe_b64decode(data.encode("ascii")).decode(errors="replace")
            if mime == "text/plain" and not body_text:
                body_text = decoded
            elif mime == "text/html" and not body_html:
                body_html = decoded

    walk(payload)
    return body_text, body_html, attachments


def parse_message_full(account_id: str, m: dict) -> MessageFull:
    """Parse a Gmail message in `format=raw` form (raw RFC822 base64url-encoded)."""
    raw = base64.urlsafe_b64decode(m["raw"].encode("ascii"))
    msg: EmailMessage = BytesParser(policy=email.policy.default).parsebytes(raw)  # type: ignore[assignment]

    body_text = ""
    body_html = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and not body_text:
                body_text = part.get_content()
            elif ctype == "text/html" and not body_html:
                body_html = part.get_content()
    else:
        ctype = msg.get_content_type()
        if ctype == "text/html":
            body_html = msg.get_content()
        else:
            body_text = msg.get_content()

    return MessageFull(
        account_id=account_id,
        provider="google",
        id=m["id"],
        thread_id=m.get("threadId"),
        **{"from": msg.get("From", "") or ""},
        to=_split_addrs(msg.get("To", "")),
        cc=_split_addrs(msg.get("Cc", "")),
        bcc=_split_addrs(msg.get("Bcc", "")),
        subject=msg.get("Subject", "") or "",
        date=_parse_rfc2822(msg.get("Date")),
        snippet=m.get("snippet", "") or "",
        unread="UNREAD" in (m.get("labelIds") or []),
        tags=[lbl for lbl in (m.get("labelIds") or []) if not lbl.startswith("CATEGORY_")],
        body_text=body_text,
        body_html=body_html,
    )


def parse_event_summary(account_id: str, e: dict) -> Event:
    return Event(
        account_id=account_id,
        provider="google",
        id=e["id"],
        title=e.get("summary", "") or "",
        start=_parse_gcal_dt(e.get("start", {})),
        end=_parse_gcal_dt(e.get("end", {})),
        location=e.get("location", "") or "",
        organizer=(e.get("organizer") or {}).get("email", "") or "",
        is_all_day="date" in (e.get("start") or {}),
        # `recurringEventId` is set on instances of a recurring series.
        recurring_master_id=e.get("recurringEventId") or None,
    )


def parse_event_full(account_id: str, e: dict) -> EventFull:
    summary = parse_event_summary(account_id, e)
    attendees = [a.get("email", "") for a in e.get("attendees", []) or []]
    online = (e.get("conferenceData") or {}).get("entryPoints", [])
    join_url = next(
        (p.get("uri", "") for p in online if p.get("entryPointType") == "video"), ""
    )
    return EventFull(
        **summary.model_dump(),
        body_text=e.get("description", "") or "",
        attendees=attendees,
        online_meeting_url=join_url,
    )


# ---------- helpers ----------


def _split_addrs(header_value: str) -> list[str]:
    if not header_value:
        return []
    return [a.strip() for a in header_value.split(",") if a.strip()]


def _parse_rfc2822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _parse_gcal_dt(d: dict) -> datetime:
    if "dateTime" in d:
        return datetime.fromisoformat(d["dateTime"])
    if "date" in d:
        # All-day event: parse as midnight UTC.
        return datetime.fromisoformat(d["date"] + "T00:00:00+00:00")
    raise ValueError(f"Calendar event missing start/end: {d}")


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
