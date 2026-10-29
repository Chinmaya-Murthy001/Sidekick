"""Live state of one meeting: who is in it, what has been said, and every
action the agent has taken or is waiting to take."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from .workspace import Participant, Workspace, new_id


@dataclass
class Utterance:
    id: str
    speaker: str
    text: str
    at: datetime

    def to_dict(self) -> dict:
        return {"id": self.id, "speaker": self.speaker, "text": self.text, "at": self.at.isoformat(timespec="seconds")}


@dataclass
class FeedEvent:
    id: str
    at: datetime
    kind: str          # joined | action | approval | info | warning | summary | error
    title: str
    detail: str = ""
    status: str = "done"   # done | pending | approved | rejected | failed
    data: dict = field(default_factory=dict)
    latency_ms: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["at"] = self.at.isoformat(timespec="seconds")
        return d


@dataclass
class PendingApproval:
    id: str
    tool: str
    args: dict
    reason: str
    feed_id: str


class Meeting:
    def __init__(
        self,
        title: str,
        participants: list[Participant],
        workspace: Workspace,
        clock: Callable[[], datetime] | None = None,
        contacts: list[Participant] | None = None,
    ):
        self.id = uuid.uuid4().hex[:12]
        self.started_at: datetime | None = None  # set when Sidekick joins
        self.link = ""        # the call's Meet/Zoom/Teams link, if given
        self.bot = "none"     # none | joining | in_call | failed | left
        self.bot_id = ""
        self.title = title
        self.participants = participants
        # People who are not on the call but can be emailed or invited.
        self.contacts = contacts or []
        self.workspace = workspace
        self._clock = clock or datetime.now
        self.transcript: list[Utterance] = []
        self.feed: list[FeedEvent] = []
        self.approvals: dict[str, PendingApproval] = {}
        self.chat: list[dict] = []
        self.paused = False
        self.ended = False
        self.summary: dict | None = None
        self._listeners: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    # --- time -----------------------------------------------------------
    def now(self) -> datetime:
        return self._clock()

    # --- people ---------------------------------------------------------
    def find_participant(self, name_or_email: str, include_contacts: bool = False) -> Participant | None:
        key = name_or_email.strip().lower()
        people = self.participants + (self.contacts if include_contacts else [])
        for p in people:
            if key in (p.email.lower(), p.name.lower(), p.first_name.lower()):
                return p
        return None

    def resolve_emails(self, names: list[str]) -> tuple[list[str], list[str]]:
        """Map spoken names to emails. Returns (emails, unknown_names)."""
        emails, unknown = [], []
        for n in names:
            if "@" in n:
                emails.append(n.lower())
                continue
            p = self.find_participant(n, include_contacts=True)
            (emails.append(p.email) if p else unknown.append(n))
        return emails, unknown

    def everyone(self) -> list[str]:
        return [p.email for p in self.participants]

    # --- transcript & feed ---------------------------------------------
    def add_utterance(self, speaker: str, text: str) -> Utterance:
        u = Utterance(new_id("utt"), speaker, text.strip(), self.now())
        self.transcript.append(u)
        self.publish({"type": "utterance", "utterance": u.to_dict()})
        return u

    def log(self, kind: str, title: str, detail: str = "", status: str = "done", **data: Any) -> FeedEvent:
        ev = FeedEvent(new_id("feed"), self.now(), kind, title, detail, status, data)
        self.feed.append(ev)
        self.publish({"type": "feed", "event": ev.to_dict()})
        return ev

    def update_feed(self, ev: FeedEvent) -> None:
        self.publish({"type": "feed_update", "event": ev.to_dict()})

    def post_chat(self, text: str) -> None:
        msg = {"from": "Sidekick", "text": text, "at": self.now().isoformat(timespec="seconds")}
        self.chat.append(msg)
        self.publish({"type": "chat", "message": msg})

    def recent_transcript(self, n: int = 25) -> str:
        return "\n".join(f"[{u.at:%H:%M:%S}] {u.speaker}: {u.text}" for u in self.transcript[-n:])

    # --- live updates ---------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def publish(self, message: dict) -> None:
        # Agents run in worker threads; hand messages to the event loop safely.
        outgoing = [message]
        if message.get("type") in {"feed", "feed_update", "utterance"}:
            outgoing.append({"type": "state", "state": self.state_dict(include_transcript=False)})
        for q in list(self._listeners):
            for m in outgoing:
                if self.loop is not None:
                    self.loop.call_soon_threadsafe(q.put_nowait, m)
                else:
                    q.put_nowait(m)

    def state_dict(self, include_transcript: bool = True) -> dict:
        ws = self.workspace
        d = {
            "id": self.id,
            "title": self.title,
            "started_at": self.started_at.isoformat(timespec="minutes") if self.started_at else None,
            "provider": ws.provider,
            "delivers": ws.delivers,
            "link": self.link,
            "bot": self.bot,
            "paused": self.paused,
            "ended": self.ended,
            "now": self.now().isoformat(timespec="minutes"),
            "participants": [
                {"name": p.name, "email": p.email, "internal": ws.is_internal(p.email)} for p in self.participants
            ],
            "contacts": [{"name": p.name, "email": p.email, "internal": ws.is_internal(p.email)} for p in self.contacts],
            "events": [e.to_dict() for e in ws.events],
            "outbox": [m.to_dict() for m in ws.outbox],
            "tasks": [t.to_dict() for t in ws.tasks],
            "decisions": list(ws.decisions),
            "pending": [{"id": a.id, "tool": a.tool, "args": a.args, "reason": a.reason} for a in self.approvals.values()],
            "summary": self.summary,
        }
        if include_transcript:
            d["transcript"] = [u.to_dict() for u in self.transcript]
            d["feed"] = [f.to_dict() for f in self.feed]
            d["chat"] = list(self.chat)
        return d


def fixed_clock(start: datetime) -> Callable[[], datetime]:
    """A clock that starts at `start` and advances in real time. Lets the demo
    and the evaluation pretend it is a particular Monday morning."""
    offset = start - datetime.now()
    return lambda: datetime.now() + offset


def stepped_clock(start: datetime, step: timedelta = timedelta(seconds=5)) -> Callable[[], datetime]:
    """A deterministic clock for tests: each call moves forward by `step`."""
    state = {"t": start - step}

    def tick() -> datetime:
        state["t"] += step
        return state["t"]

    return tick
