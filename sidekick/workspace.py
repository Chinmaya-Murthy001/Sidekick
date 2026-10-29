"""Sandbox stand-ins for Google Calendar, Gmail and a task tracker.

The agent talks to these through the tool layer, so swapping in the real
Google APIs means replacing this module, not the agent.
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta

WORK_START = time(9, 0)
WORK_END = time(17, 0)
SLOT_STEP = timedelta(minutes=30)

_ids = itertools.count(1)


def new_id(prefix: str) -> str:
    return f"{prefix}_{next(_ids)}"


@dataclass
class Participant:
    name: str
    email: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    attendees: list[str]
    created_by_agent: bool = True
    link: str = ""         # the provider's page for the event, when real

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat(timespec="minutes")
        d["end"] = self.end.isoformat(timespec="minutes")
        return d


@dataclass
class Email:
    id: str
    to: list[str]
    subject: str
    body: str
    sent_at: datetime
    delivered: bool = False  # True when a real provider sent it

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sent_at"] = self.sent_at.isoformat(timespec="seconds")
        return d


@dataclass
class Task:
    id: str
    owner: str
    description: str
    due: date | None
    source_quote: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["due"] = self.due.isoformat() if self.due else None
        return d


# Shared mail providers: a host on one of these does not make every other
# user of that provider a teammate.
PUBLIC_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com",
}


class WorkspaceError(Exception):
    """A calendar or mail provider refused or failed a request."""


@dataclass
class Workspace:
    """Local workspace: keeps the meeting's records and simulates delivery.
    GoogleWorkspace (google_workspace.py) overrides the delivery hooks."""

    team_domain: str | None = None
    internal_emails: set[str] = field(default_factory=set)
    busy: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    events: list[CalendarEvent] = field(default_factory=list)
    outbox: list[Email] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)

    provider = "local"     # shown in the UI; "google" when connected
    delivers = False       # True when invites and emails really go out

    def is_internal(self, email: str) -> bool:
        email = email.lower()
        if email in self.internal_emails:
            return True
        return bool(self.team_domain) and self.team_domain not in PUBLIC_DOMAINS and email.endswith("@" + self.team_domain)

    def calendars_not_visible(self, emails: list[str]) -> list[str]:
        """Attendees whose free/busy the provider could not read."""
        return []

    def add_busy(self, email: str, start: datetime, end: datetime) -> None:
        self.busy.setdefault(email.lower(), []).append((start, end))

    def _blocks(self, email: str) -> list[tuple[datetime, datetime]]:
        blocks = list(self.busy.get(email.lower(), []))
        blocks += [(e.start, e.end) for e in self.events if email.lower() in (a.lower() for a in e.attendees)]
        return blocks

    def conflicts(self, emails: list[str], start: datetime, end: datetime) -> list[str]:
        """Return the attendees who are busy during [start, end)."""
        return [
            e for e in emails
            if any(s < end and start < f for s, f in self._blocks(e))
        ]

    def free_slots(
        self,
        emails: list[str],
        day: date,
        duration: timedelta,
        not_before: datetime | None = None,
        window: tuple[time, time] | None = None,
        limit: int = 3,
    ) -> list[datetime]:
        lo, hi = window or (WORK_START, WORK_END)
        cursor = datetime.combine(day, lo)
        end_of_window = datetime.combine(day, hi)
        found: list[datetime] = []
        while cursor + duration <= end_of_window and len(found) < limit:
            if (not_before is None or cursor >= not_before) and not self.conflicts(emails, cursor, cursor + duration):
                found.append(cursor)
            cursor += SLOT_STEP
        return found

    def create_event(self, title: str, start: datetime, duration: timedelta, attendees: list[str],
                     description: str = "") -> CalendarEvent:
        event = CalendarEvent(new_id("evt"), title, start, start + duration, attendees)
        self.events.append(event)
        return event

    def find_duplicate_event(self, start: datetime, attendees: list[str], title: str = "") -> CalendarEvent | None:
        """Same people at the same time, or same people on the same day for the
        same topic (a proposal repeated as agreement)."""
        wanted = {a.lower() for a in attendees}
        topic = _keywords(title)
        for e in self.events:
            if {a.lower() for a in e.attendees} != wanted:
                continue
            if e.start == start:
                return e
            if e.start.date() == start.date() and topic and topic & _keywords(e.title):
                return e
        return None

    def send_email(self, to: list[str], subject: str, body: str, now: datetime) -> Email:
        """Send a message. Locally it is only recorded; GoogleWorkspace delivers it."""
        return self.record_email(to, subject, body, now)

    def record_email(self, to: list[str], subject: str, body: str, now: datetime, delivered: bool = False) -> Email:
        email = Email(new_id("mail"), list(to), subject, body, now, delivered=delivered)
        self.outbox.append(email)
        return email

    def add_task(self, owner: str, description: str, due: date | None, quote: str) -> Task:
        task = Task(new_id("task"), owner, description, due, quote)
        self.tasks.append(task)
        return task

    def find_duplicate_task(self, owner: str, description: str) -> Task | None:
        words = _keywords(description)
        for t in self.tasks:
            if t.owner.lower() == owner.lower():
                overlap = words & _keywords(t.description)
                if words and len(overlap) / len(words) >= 0.6:
                    return t
        return None


_STOP = {"the", "a", "an", "to", "and", "of", "for", "on", "by", "i'll", "will", "it", "that", "this", "up", "out"}


def _keywords(text: str) -> set[str]:
    return {w.strip(".,!?").lower() for w in text.split() if w.lower() not in _STOP and len(w) > 2}


def local_tz():
    return datetime.now().astimezone().tzinfo


def seed_demo_workspace(team_domain: str, week_of: date) -> Workspace:
    """Busy blocks for the demo team, laid out relative to the given week so
    the demo behaves the same whichever day it is run."""
    ws = Workspace(team_domain=team_domain)
    monday = week_of - timedelta(days=week_of.weekday())

    def at(day_offset: int, hh: int, mm: int = 0) -> datetime:
        return datetime.combine(monday + timedelta(days=day_offset), time(hh, mm))

    for week in (0, 7):
        # Priya: standup every weekday morning, Thursday design review.
        for d in range(5):
            ws.add_busy(f"priya@{team_domain}", at(week + d, 9), at(week + d, 9, 30))
        ws.add_busy(f"priya@{team_domain}", at(week + 3, 10), at(week + 3, 11, 30))
        # Marcus: Thursday lunch, Tuesday and Thursday afternoons with customers.
        ws.add_busy(f"marcus@{team_domain}", at(week + 3, 12), at(week + 3, 13))
        ws.add_busy(f"marcus@{team_domain}", at(week + 1, 14), at(week + 1, 16))
        ws.add_busy(f"marcus@{team_domain}", at(week + 3, 15), at(week + 3, 17))
        # Alex: Wednesday all-hands, Friday out of office after noon.
        ws.add_busy(f"alex@{team_domain}", at(week + 2, 9), at(week + 2, 12))
        ws.add_busy(f"alex@{team_domain}", at(week + 4, 12), at(week + 4, 17))
        # External client contact: busy Thursday 9-10.
        ws.add_busy("dana@globex.com", at(week + 3, 9), at(week + 3, 10))
    return ws
