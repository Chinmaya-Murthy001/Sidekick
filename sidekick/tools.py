"""The actions Sidekick can take, shared by every agent.

Safety rules live here rather than in the prompt, so they hold whichever
agent is deciding: anything that leaves the team waits for a human, duplicate
actions are refused, and nothing is booked over a conflict.
"""

from __future__ import annotations

import json
import time as _time
from datetime import date, datetime, time, timedelta
from typing import Any

from .meeting import Meeting, PendingApproval, Utterance
from .workspace import WorkspaceError, new_id

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "check_availability",
        "description": (
            "Check the calendars of meeting participants. Give start_time to test one exact slot "
            "(returns conflicts and alternatives), or leave it out to list free slots on that day. "
            "Call this before schedule_meeting whenever a date or time is proposed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "attendees": {"type": "array", "items": {"type": "string"}, "description": "Names or emails. Use 'everyone' for all participants."},
                "day": {"type": "string", "description": "YYYY-MM-DD"},
                "start_time": {"type": "string", "description": "HH:MM 24h, optional"},
                "duration_minutes": {"type": "integer", "description": "Default 30"},
                "part_of_day": {"type": "string", "enum": ["any", "morning", "afternoon", "evening"]},
            },
            "required": ["attendees", "day"],
            "additionalProperties": False,
        },
    },
    {
        "name": "schedule_meeting",
        "description": (
            "Create a calendar event and email the invite to the attendees. Only call this once the "
            "group has agreed on a meeting (e.g. \"let's meet Thursday\") and check_availability shows "
            "the slot is free. Invites to anyone outside the team wait for human approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM"},
                "duration_minutes": {"type": "integer"},
                "attendees": {"type": "array", "items": {"type": "string"}},
                "agenda": {"type": "string"},
            },
            "required": ["title", "start", "duration_minutes", "attendees"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_task",
        "description": "Record an action item when someone commits to doing something (\"I'll send the deck\") or is asked and accepts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Participant name"},
                "description": {"type": "string", "description": "Short imperative, e.g. 'Send the pricing deck to Dana'"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD if a deadline was said"},
                "quote": {"type": "string", "description": "The words that created the task"},
            },
            "required": ["owner", "description", "quote"],
            "additionalProperties": False,
        },
    },
    {
        "name": "log_decision",
        "description": "Record a decision the group clearly agreed on.",
        "input_schema": {
            "type": "object",
            "properties": {"decision": {"type": "string"}},
            "required": ["decision"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_email",
        "description": "Send an email when a participant asks Sidekick to. Emails to anyone outside the team wait for human approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "post_to_meeting_chat",
        "description": "Post a short message in the meeting chat, e.g. to propose free slots or report a conflict.",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
    },
]

_PART_OF_DAY = {
    "morning": (time(9, 0), time(12, 0)),
    "afternoon": (time(13, 0), time(17, 0)),
    "evening": (time(16, 0), time(18, 0)),
}


class ToolError(Exception):
    pass


class ToolBox:
    def __init__(self, meeting: Meeting):
        self.meeting = meeting
        self.trigger: Utterance | None = None
        self.trigger_clock: float | None = None
        self.calls: list[dict] = []
        self._approval_triggers: dict[str, Utterance | None] = {}

    # --- entry point ---------------------------------------------------
    def set_trigger(self, utterance: Utterance) -> None:
        self.trigger = utterance
        self.trigger_clock = _time.perf_counter()

    def execute(self, name: str, args: dict, internal: bool = False) -> dict:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"unknown tool {name}"}
        if not internal:
            # Agents can never pass the approval flag themselves.
            args = {k: v for k, v in args.items() if not k.startswith("_")}
        try:
            result = handler(**args)
        except (ToolError, WorkspaceError) as e:
            result = {"error": str(e)}
        except TypeError as e:
            result = {"error": f"bad arguments: {e}"}
        self.calls.append({"tool": name, "args": args, "result": result})
        return result

    def execute_json(self, name: str, args: dict) -> str:
        return json.dumps(self.execute(name, args), default=str)

    # --- helpers -------------------------------------------------------
    def _latency(self) -> int | None:
        if self.trigger_clock is None:
            return None
        return int((_time.perf_counter() - self.trigger_clock) * 1000)

    def _emails(self, attendees: list[str]) -> list[str]:
        if any(a.strip().lower() in {"everyone", "all", "the team"} for a in attendees):
            return self.meeting.everyone()
        emails, unknown = self.meeting.resolve_emails(attendees)
        if unknown:
            raise ToolError(f"unknown attendee(s): {', '.join(unknown)}. Known: {', '.join(p.name for p in self.meeting.participants)}")
        return list(dict.fromkeys(emails))

    def _external(self, emails: list[str]) -> list[str]:
        return [e for e in emails if not self.meeting.workspace.is_internal(e)]

    def _quote(self) -> str:
        return f'{self.trigger.speaker}: "{self.trigger.text}"' if self.trigger else ""

    def _action(self, title: str, detail: str, **data: Any):
        ev = self.meeting.log("action", title, detail, quote=self._quote(), **data)
        ev.latency_ms = self._latency()
        self.meeting.update_feed(ev)
        return ev

    @staticmethod
    def _parse_day(day: str) -> date:
        try:
            return date.fromisoformat(day)
        except ValueError:
            raise ToolError(f"day must be YYYY-MM-DD, got {day!r}")

    @staticmethod
    def _parse_start(start: str) -> datetime:
        try:
            return datetime.fromisoformat(start)
        except ValueError:
            raise ToolError(f"start must be YYYY-MM-DDTHH:MM, got {start!r}")

    def _require_approval(self, tool: str, args: dict, external: list[str], summary: str) -> dict:
        approval_id = new_id("appr")
        ev = self.meeting.log(
            "approval",
            f"Needs your OK: {summary}",
            f"Goes outside the team to {', '.join(external)}. Approve or reject in the feed.",
            status="pending",
            approval_id=approval_id,
            quote=self._quote(),
        )
        ev.latency_ms = self._latency()
        self.meeting.approvals[approval_id] = PendingApproval(approval_id, tool, args, f"external: {', '.join(external)}", ev.id)
        self._approval_triggers[approval_id] = self.trigger
        self.meeting.update_feed(ev)
        return {"status": "pending_approval", "approval_id": approval_id, "external_recipients": external}

    # --- tools ---------------------------------------------------------
    def _tool_check_availability(self, attendees: list[str], day: str, start_time: str | None = None,
                                 duration_minutes: int = 30, part_of_day: str = "any") -> dict:
        emails = self._emails(attendees)
        d = self._parse_day(day)
        duration = timedelta(minutes=duration_minutes or 30)
        ws = self.meeting.workspace
        now = self.meeting.now()
        if start_time:
            try:
                start = datetime.combine(d, time.fromisoformat(start_time))
            except ValueError:
                raise ToolError(f"start_time must be HH:MM, got {start_time!r}")
            busy = ws.conflicts(emails, start, start + duration)
            alternatives = [] if not busy else ws.free_slots(emails, d, duration, not_before=now)
            return {
                "slot": start.isoformat(timespec="minutes"),
                "available": not busy and start >= now,
                "in_the_past": start < now,
                "busy_attendees": busy,
                "alternatives": [s.isoformat(timespec="minutes") for s in alternatives],
                "calendars_not_visible": ws.calendars_not_visible(emails),
            }
        window = _PART_OF_DAY.get(part_of_day or "any")
        slots = ws.free_slots(emails, d, duration, not_before=now, window=window)
        return {"day": d.isoformat(), "attendees": emails, "free_slots": [s.isoformat(timespec="minutes") for s in slots],
                "calendars_not_visible": ws.calendars_not_visible(emails)}

    def _tool_schedule_meeting(self, title: str, start: str, duration_minutes: int, attendees: list[str],
                               agenda: str = "", _approved: bool = False) -> dict:
        emails = self._emails(attendees)
        begin = self._parse_start(start)
        duration = timedelta(minutes=duration_minutes or 30)
        ws = self.meeting.workspace
        if begin < self.meeting.now():
            raise ToolError("that time is in the past")
        dup = ws.find_duplicate_event(begin, emails, title)
        if dup:
            return {"status": "already_scheduled", "event_id": dup.id}
        busy = ws.conflicts(emails, begin, begin + duration)
        if busy:
            raise ToolError(f"conflict: {', '.join(busy)} busy at {begin:%a %H:%M}. Use check_availability for alternatives.")
        external = self._external(emails)
        if external and not _approved:
            return self._require_approval(
                "schedule_meeting",
                {"title": title, "start": start, "duration_minutes": duration_minutes, "attendees": emails, "agenda": agenda},
                external,
                f"invite to '{title}' on {begin:%a %b %d, %H:%M}",
            )
        description = agenda or f"Scheduled live during “{self.meeting.title}” by Sidekick."
        event = ws.create_event(title, begin, duration, emails, description)
        subject = f"Invitation: {title} @ {begin:%a %b %d %H:%M}"
        body = (f"{title}\n{begin:%A, %B %d at %H:%M} ({duration_minutes} min)\n"
                f"Attendees: {', '.join(emails)}\n\n{description}")
        # A real calendar emails the invitations itself; locally we record one.
        mail = (ws.record_email(emails, subject, body, self.meeting.now(), delivered=True) if ws.delivers
                else ws.send_email(emails, subject, body, self.meeting.now()))
        self._action(
            f"Invite sent: {title}" if ws.delivers else f"Invite recorded: {title}",
            f"{begin:%a %b %d, %H:%M}-{event.end:%H:%M} to {', '.join(emails)}",
            event_id=event.id, email_id=mail.id, link=event.link,
        )
        return {"status": "scheduled", "event_id": event.id, "start": begin.isoformat(timespec="minutes"), "invited": emails}

    def _tool_create_task(self, owner: str, description: str, quote: str, due_date: str | None = None) -> dict:
        p = self.meeting.find_participant(owner)
        if not p:
            raise ToolError(f"unknown owner {owner!r}")
        ws = self.meeting.workspace
        dup = ws.find_duplicate_task(p.name, description)
        if dup:
            return {"status": "already_recorded", "task_id": dup.id}
        due = self._parse_day(due_date) if due_date else None
        task = ws.add_task(p.name, description, due, quote)
        self._action(f"Task for {p.first_name}: {description}", f"Due {due:%a %b %d}" if due else "No deadline said", task_id=task.id)
        return {"status": "created", "task_id": task.id}

    def _tool_log_decision(self, decision: str) -> dict:
        ws = self.meeting.workspace
        if any(decision.lower() == d.lower() for d in ws.decisions):
            return {"status": "already_recorded"}
        ws.decisions.append(decision)
        self._action("Decision logged", decision)
        return {"status": "logged"}

    def _tool_send_email(self, to: list[str], subject: str, body: str, _approved: bool = False) -> dict:
        emails = self._emails(to)
        external = self._external(emails)
        if external and not _approved:
            return self._require_approval("send_email", {"to": emails, "subject": subject, "body": body}, external, f"email '{subject}'")
        ws = self.meeting.workspace
        mail = ws.send_email(emails, subject, body, self.meeting.now())
        self._action(f"Email sent: {subject}" if ws.delivers else f"Email recorded: {subject}",
                     f"To {', '.join(emails)}", email_id=mail.id)
        return {"status": "sent", "email_id": mail.id}

    def _tool_post_to_meeting_chat(self, message: str) -> dict:
        self.meeting.post_chat(message)
        self.meeting.log("info", "Posted in meeting chat", message, quote=self._quote())
        return {"status": "posted"}

    # --- human decisions ------------------------------------------------
    def resolve_approval(self, approval_id: str, approve: bool) -> dict:
        pending = self.meeting.approvals.pop(approval_id, None)
        if not pending:
            return {"error": "no such pending approval"}
        ev = next((f for f in self.meeting.feed if f.id == pending.feed_id), None)
        if not approve:
            if ev:
                ev.status = "rejected"
                self.meeting.update_feed(ev)
            self.meeting.log("info", "Rejected by host", f"{pending.tool} was not sent")
            return {"status": "rejected"}
        # Attribute the action to the words that asked for it, not the latest line.
        saved = self.trigger
        self.trigger, self.trigger_clock = self._approval_triggers.pop(approval_id, None), None
        result = self.execute(pending.tool, {**pending.args, "_approved": True}, internal=True)
        self.trigger = saved
        if ev:
            ev.status = "approved" if "error" not in result else "failed"
            self.meeting.update_feed(ev)
        return result
