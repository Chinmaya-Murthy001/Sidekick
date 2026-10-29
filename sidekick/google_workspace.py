"""Workspace backed by the user's Google account: real free/busy, real
calendar events (Google emails the invitations) and mail sent through Gmail."""

from __future__ import annotations

import base64
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .workspace import CalendarEvent, Email, Workspace, WorkspaceError, local_tz, new_id


class GoogleWorkspace(Workspace):
    provider = "google"
    delivers = True

    def __init__(self, creds, account_email: str, services: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.account_email = account_email
        self._services = services or {}
        self._creds = creds
        self._fetched: set[tuple[str, date]] = set()
        self._google_busy: dict[str, list[tuple[datetime, datetime]]] = {}
        self._not_visible: set[str] = set()

    def _service(self, name: str, version: str):
        if name not in self._services:
            self._services[name] = build(name, version, credentials=self._creds, cache_discovery=False)
        return self._services[name]

    # --- free/busy -----------------------------------------------------
    @staticmethod
    def _aware(dt: datetime) -> str:
        return dt.replace(tzinfo=local_tz()).isoformat()

    @staticmethod
    def _local(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(local_tz()).replace(tzinfo=None)

    def _prefetch(self, emails: list[str], day: date) -> None:
        missing = [e.lower() for e in emails if (e.lower(), day) not in self._fetched]
        if not missing:
            return
        start = datetime.combine(day, time(0, 0))
        body = {
            "timeMin": self._aware(start),
            "timeMax": self._aware(start + timedelta(days=1)),
            "items": [{"id": e} for e in missing],
        }
        try:
            result = self._service("calendar", "v3").freebusy().query(body=body).execute()
        except HttpError as e:
            raise WorkspaceError(f"Google Calendar free/busy failed: {e.reason}") from e
        calendars = result.get("calendars", {})
        for e in missing:
            info = calendars.get(e, {})
            if info.get("errors"):
                # Outside the organisation or not shared: Google can't tell us.
                self._not_visible.add(e)
            for b in info.get("busy", []):
                self._google_busy.setdefault(e, []).append((self._local(b["start"]), self._local(b["end"])))
            self._fetched.add((e, day))

    def _blocks(self, email: str) -> list[tuple[datetime, datetime]]:
        return self._google_busy.get(email.lower(), []) + super()._blocks(email)

    def conflicts(self, emails, start, end):
        day = start.date()
        while day <= end.date():
            self._prefetch(emails, day)
            day += timedelta(days=1)
        return super().conflicts(emails, start, end)

    def free_slots(self, emails, day, duration, not_before=None, window=None, limit=3):
        self._prefetch(emails, day)
        return super().free_slots(emails, day, duration, not_before, window, limit)

    def calendars_not_visible(self, emails: list[str]) -> list[str]:
        return [e for e in emails if e.lower() in self._not_visible]

    # --- delivery ------------------------------------------------------
    def create_event(self, title, start, duration, attendees, description=""):
        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": self._aware(start)},
            "end": {"dateTime": self._aware(start + duration)},
            "attendees": [{"email": a} for a in attendees if a.lower() != self.account_email.lower()],
        }
        try:
            created = self._service("calendar", "v3").events().insert(
                calendarId="primary", body=body, sendUpdates="all").execute()
        except HttpError as e:
            raise WorkspaceError(f"Google Calendar refused the event: {e.reason}") from e
        event = CalendarEvent(created.get("id") or new_id("evt"), title, start, start + duration, attendees,
                              link=created.get("htmlLink", ""))
        self.events.append(event)
        return event

    def send_email(self, to, subject, body, now):
        msg = EmailMessage()
        msg["To"] = ", ".join(to)
        if self.account_email:
            msg["From"] = self.account_email
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        try:
            self._service("gmail", "v1").users().messages().send(userId="me", body={"raw": raw}).execute()
        except HttpError as e:
            raise WorkspaceError(f"Gmail refused the message: {e.reason}") from e
        email = Email(new_id("mail"), list(to), subject, body, now, delivered=True)
        self.outbox.append(email)
        return email
