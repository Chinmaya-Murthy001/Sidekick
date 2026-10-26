"""Offline planner: recognises the core meeting intents with patterns and
drives the same tools as the Claude agent. Used when no API key is set, and
as a fast, predictable baseline in the evaluation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..meeting import Meeting, Utterance
from ..timeparse import parse_deadline, parse_when
from ..tools import ToolBox

SCHEDULE_RE = re.compile(
    r"\b(let'?s|let us|we should|can we|could we|shall we|should we|how about we|why don'?t we)\b[^.?!]*?"
    r"\b(meet|sync|catch up|get together|reconvene|regroup|schedule|hop on a call|follow up|talk again)\b"
    r"|\bschedule (a|the) (meeting|call|sync|follow[- ]?up)\b",
    re.I,
)
NEGATION_RE = re.compile(
    r"\b(let'?s not|let us not|don'?t|do not|no need to|shouldn'?t|should not|cancel|skip)\b[^.?!]*\b(meet|sync|catch up|schedule|call)\b",
    re.I,
)
SELF_COMMIT_RE = re.compile(
    r"\b(i'?ll|i will|i can|let me|i'?m going to|i am going to|i'?ll go ahead and)\s+(?!be\b|see\b|think\b|try\b)(.+)",
    re.I,
)
ASK_RE = re.compile(r"^\s*(?P<name>[A-Z][a-z]+),?\s+(can|could|would|will) you\s+(?P<what>.+?)\??$", re.I)
ASK_TAIL_RE = re.compile(r"^\s*(can|could|would|will) you\s+(?P<what>.+?),\s*(?P<name>[A-Z][a-z]+)\s*\??$", re.I)
DECISION_RE = re.compile(
    r"\b(we(?:'ve| have)? decided(?: to)?|we(?:'re| are) going (?:to go )?with|let'?s go with|we agreed(?: to| on| that)?|"
    r"decision is|final decision|we'll go with)\b\s*(?P<what>.+)",
    re.I,
)
COMMAND_RE = re.compile(r"\b(?:hey\s+)?sidekick[,:]?\s+(?P<cmd>.+)", re.I)
EMAIL_CMD_RE = re.compile(
    r"\b(?:send|email|e-mail|mail|write)\s+(?:an?\s+(?:email|note|message)\s+to\s+)?(?P<who>[A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)*|everyone|the team)"
    r"(?:\s+at\s+\w+)?\s*(?:(?:about|with|that|saying|to say)\s+)?(?P<what>.*)",
    re.I,
)
TRAILING_DEADLINE_RE = re.compile(
    r"\s*(,?\s*(by|before|until|due)\s+.*|\s+(today|tomorrow|tonight|this week|next week|on \w+day)\b.*)$", re.I
)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" .,!?")
    return text[:1].upper() + text[1:] if text else text


def _first_person_to_owner(text: str) -> str:
    return re.sub(r"\bmy\b", "their", re.sub(r"\bme\b", "them", text, flags=re.I), flags=re.I)


class RuleAgent:
    name = "rules"

    def handle(self, meeting: Meeting, tools: ToolBox, u: Utterance) -> list[str]:
        acted: list[str] = []
        text = u.text
        cmd = COMMAND_RE.search(text)
        if cmd and self._command(meeting, tools, u, cmd.group("cmd")):
            acted.append("command")
            return acted
        if SCHEDULE_RE.search(text) and not NEGATION_RE.search(text) and self._schedule(meeting, tools, u):
            acted.append("schedule")
        if self._task(meeting, tools, u):
            acted.append("task")
        m = DECISION_RE.search(text)
        if m and len(m.group("what").split()) >= 2:
            tools.execute("log_decision", {"decision": _clean(m.group(0))})
            acted.append("decision")
        return acted

    # --- scheduling ----------------------------------------------------
    def _attendees(self, meeting: Meeting, u: Utterance) -> list[str]:
        """Everyone on the call, unless the speaker narrows it ("Priya and I")
        or names someone who is not on the call."""
        text = u.text
        named = [p for p in meeting.participants + meeting.contacts
                 if re.search(rf"\b{re.escape(p.first_name)}\b", text, re.I)]
        narrowed = re.search(r"\b(just|only)\b|\b\w+ and (i|me)\b|\b(i|me) and \w+\b|\bwith\s+[A-Z]", text)
        if named and narrowed:
            speaker = meeting.find_participant(u.speaker)
            people = named + ([speaker] if speaker and speaker not in named else [])
            return [p.email for p in people]
        extra = [p.email for p in named if p in meeting.contacts]
        return meeting.everyone() + extra

    def _schedule(self, meeting: Meeting, tools: ToolBox, u: Utterance) -> bool:
        when = parse_when(u.text, meeting.now())
        if not when.has_day:
            return False
        attendees = self._attendees(meeting, u)
        duration = 60 if re.search(r"\b(hour|60 ?min)", u.text, re.I) else 30
        topic = re.search(r"\b(?:to|about|on|for)\s+(?:discuss\s+|finalize\s+|review\s+|go over\s+)?(?P<t>(the\s+)?[a-z][\w\s-]{3,40}?)(?:[.?!]|$| on | at | next| this)", u.text, re.I)
        title = _clean(topic.group("t")) if topic and not parse_when(topic.group("t"), meeting.now()).has_day else "Follow-up"
        if title.lower().startswith("the "):
            title = title[4:].capitalize()
        title = f"{title} ({meeting.title})" if title == "Follow-up" else title

        if when.at:
            check = tools.execute("check_availability", {
                "attendees": attendees, "day": when.day.isoformat(),
                "start_time": when.at.strftime("%H:%M"), "duration_minutes": duration,
            })
            if "error" in check:
                return False
            if not check["available"]:
                alts = ", ".join(datetime.fromisoformat(a).strftime("%a %H:%M") for a in check["alternatives"]) or "none that day"
                busy = ", ".join(check["busy_attendees"]) or "the slot is in the past"
                tools.execute("post_to_meeting_chat", {
                    "message": f"{when.day:%a} {when.at:%H:%M} doesn't work ({busy}). Free slots for everyone: {alts}."
                })
                return True
            start = datetime.combine(when.day, when.at)
        else:
            part = "morning" if when.window and when.window[0].hour < 12 else ("afternoon" if when.window else "any")
            check = tools.execute("check_availability", {
                "attendees": attendees, "day": when.day.isoformat(), "duration_minutes": duration, "part_of_day": part,
            })
            if "error" in check:
                return False
            if not check["free_slots"]:
                tools.execute("post_to_meeting_chat", {"message": f"No common free slot on {when.day:%A}. Want me to look at another day?"})
                return True
            start = datetime.fromisoformat(check["free_slots"][0])
        result = tools.execute("schedule_meeting", {
            "title": title, "start": start.isoformat(timespec="minutes"),
            "duration_minutes": duration, "attendees": attendees,
        })
        if result.get("status") == "scheduled":
            tools.execute("post_to_meeting_chat", {"message": f"Booked '{title}' for {start:%A %H:%M}. Invites are out."})
        return "error" not in result

    # --- tasks ---------------------------------------------------------
    def _task(self, meeting: Meeting, tools: ToolBox, u: Utterance) -> bool:
        owner, what = None, None
        m = ASK_RE.match(u.text) or ASK_TAIL_RE.match(u.text)
        if m and meeting.find_participant(m.group("name")):
            owner, what = m.group("name"), _first_person_to_owner(m.group("what"))
        else:
            m = SELF_COMMIT_RE.search(u.text)
            if m and not SCHEDULE_RE.search(u.text):
                owner, what = u.speaker, m.group(2)
                if re.match(r"(take|handle|do) (that|it|this)\b", what, re.I):
                    what = self._previous_ask(meeting, u) or what
        if not owner or not what or len(what.split()) < 2:
            return False
        due = parse_deadline(what, meeting.now())
        description = _clean(TRAILING_DEADLINE_RE.sub("", what))
        args = {"owner": owner, "description": description, "quote": u.text}
        if due:
            args["due_date"] = due.isoformat()
        return tools.execute("create_task", args).get("status") in {"created", "already_recorded"}

    def _previous_ask(self, meeting: Meeting, u: Utterance) -> str | None:
        for prev in reversed(meeting.transcript[:-1][-4:]):
            m = re.search(r"(can|could|would) (you|someone|anyone)\s+(?P<what>.+?)\??$", prev.text, re.I)
            if m:
                return m.group("what")
        return None

    # --- voice commands -------------------------------------------------
    def _command(self, meeting: Meeting, tools: ToolBox, u: Utterance, cmd: str) -> bool:
        m = EMAIL_CMD_RE.search(cmd)
        if m:
            who = re.split(r"\s+(?:and|&)\s+", m.group("who"))
            what = _clean(m.group("what")) or "Notes from our meeting"
            speaker = meeting.find_participant(u.speaker)
            body = (f"Hi,\n\n{what}.\n\nSent on behalf of {speaker.name if speaker else u.speaker} "
                    f"from the '{meeting.title}' meeting.\n\n- Sidekick (meeting assistant)")
            result = tools.execute("send_email", {"to": who, "subject": what[:60], "body": body})
            return "error" not in result
        m = re.search(r"\bremind (us|me|everyone)\b(?P<rest>.*)", cmd, re.I)
        if m:
            due = parse_deadline(m.group("rest"), meeting.now()) or (meeting.now() + timedelta(days=7)).date()
            what = _clean(TRAILING_DEADLINE_RE.sub("", re.sub(r"^\s*(to|about)\s+", "", m.group("rest")))) or "Follow up"
            tools.execute("create_task", {"owner": u.speaker, "description": f"Reminder: {what}", "due_date": due.isoformat(), "quote": u.text})
            return True
        return False
