"""Runs one meeting end to end: join, listen, act, summarise.

Pause and resume are handled here with fixed patterns, not by the agent, so
"Sidekick, stop recording" always works the same way.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import timedelta

from .meeting import Meeting
from .tools import ToolBox

PAUSE_RE = re.compile(r"\bsidekick\b[^.?!]*\b(stop|pause)\b[^.?!]*\b(recording|listening|notes|transcrib\w*)\b|\bsidekick,? (pause|stop)\b", re.I)
RESUME_RE = re.compile(r"\bsidekick\b[^.?!]*\b(resume|start (recording|listening) again|you can (listen|record) again|unpause)\b", re.I)


class SidekickSession:
    def __init__(self, meeting: Meeting, agent):
        self.meeting = meeting
        self.agent = agent
        self.tools = ToolBox(meeting)
        # One utterance at a time, in order, so actions never race each other.
        self._lock = threading.Lock()

    def join(self) -> None:
        m = self.meeting
        m.log("joined", "Sidekick joined the call",
              f"Agent: {self.agent.name}. Listening to {len(m.participants)} participants.")
        m.post_chat("Hi, I'm Sidekick, an AI assistant. I'm transcribing this call and will schedule meetings, "
                    "log tasks and decisions as you talk. Say \"Sidekick, stop recording\" to pause me. "
                    "Nothing goes outside the team without the host's OK.")

    def hear(self, speaker: str, text: str) -> list[str]:
        """Process one finished utterance. Returns the kinds of action taken."""
        text = text.strip()
        if not text:
            return []
        with self._lock:
            m = self.meeting
            if m.ended:
                return []
            if m.paused:
                if RESUME_RE.search(text):
                    m.paused = False
                    m.add_utterance(speaker, text)
                    m.log("info", "Recording resumed", f"Resumed by {speaker}")
                    m.post_chat("I'm listening again.")
                    return ["resume"]
                return []  # nothing said while paused is stored or acted on
            u = m.add_utterance(speaker, text)
            if PAUSE_RE.search(text):
                m.paused = True
                m.log("warning", "Recording paused", f"Paused by {speaker}. Nothing is transcribed until someone says \"Sidekick, resume\".")
                m.post_chat("Paused. I'm not recording until you say \"Sidekick, resume\".")
                return ["pause"]
            self.tools.set_trigger(u)
            try:
                return self.agent.handle(m, self.tools, u)
            except Exception as e:  # keep listening even if one step fails
                m.log("error", "Agent step failed", f"{type(e).__name__}: {e}", status="failed")
                return []

    def approve(self, approval_id: str, approve: bool) -> dict:
        with self._lock:
            return self.tools.resolve_approval(approval_id, approve)

    def end(self, send_summary: bool = True) -> dict:
        with self._lock:
            m = self.meeting
            if m.ended:
                return m.summary or {}
            m.ended = True
            summary = build_summary(m)
            if hasattr(self.agent, "summarize"):
                try:
                    summary["recap"] = self.agent.summarize(m, summary)
                except Exception as e:
                    m.log("warning", "LLM recap failed, sent the structured summary instead", str(e))
            m.summary = summary
            if send_summary:
                body = summary.get("recap") or format_summary(summary)
                internal = [p.email for p in m.participants if m.workspace.is_internal(p.email)]
                m.workspace.send_email(internal, f"Summary: {m.title}", body, m.now())
                m.log("summary", "Summary emailed to the team", f"{len(internal)} recipients", recipients=internal)
            m.publish({"type": "state", "state": m.state_dict()})
            return summary


def open_questions(m: Meeting) -> list[str]:
    """Questions nobody answered: a question followed only by more questions,
    by the same speaker, or by nothing."""
    out = []
    t = m.transcript
    for i, u in enumerate(t):
        if not u.text.endswith("?") or re.search(r"\bsidekick\b", u.text, re.I):
            continue
        if any(f.data.get("quote", "").endswith(f'"{u.text}"') for f in m.feed):
            continue  # it led to an action, so it was handled
        replies = [r for r in t[i + 1:i + 3]
                   if r.speaker != u.speaker and not r.text.endswith("?") and not re.search(r"\bsidekick\b", r.text, re.I)]
        if not replies:
            out.append(f"{u.speaker}: {u.text}")
    return out


def build_summary(m: Meeting) -> dict:
    ws = m.workspace
    start = m.transcript[0].at if m.transcript else m.now()
    return {
        "meeting": m.title,
        "duration_minutes": max(1, int((m.now() - start) / timedelta(minutes=1))),
        "participants": [p.name for p in m.participants],
        "decisions": list(ws.decisions),
        "tasks": [t.to_dict() for t in ws.tasks],
        "meetings_scheduled": [e.to_dict() for e in ws.events],
        "pending_approvals": [a.args for a in m.approvals.values()],
        "open_questions": open_questions(m),
    }


def format_summary(s: dict) -> str:
    lines = [f"Summary of '{s['meeting']}' ({s['duration_minutes']} min, {', '.join(s['participants'])})", ""]
    lines.append("Decisions:")
    lines += [f"  - {d}" for d in s["decisions"]] or ["  - none recorded"]
    lines.append("Action items:")
    lines += [f"  - {t['owner']}: {t['description']}" + (f" (due {t['due']})" if t["due"] else "") for t in s["tasks"]] or ["  - none"]
    lines.append("Meetings scheduled:")
    lines += [f"  - {e['title']} at {e['start']}" for e in s["meetings_scheduled"]] or ["  - none"]
    lines.append("Open questions:")
    lines += [f"  - {q}" for q in s["open_questions"]] or ["  - none"]
    lines += ["", "- Sidekick (AI meeting assistant)"]
    return "\n".join(lines)


def play_script(session: SidekickSession, lines: list[dict], delay: float = 0.0) -> None:
    for line in lines:
        session.hear(line["speaker"], line["text"])
        if delay:
            time.sleep(delay)
