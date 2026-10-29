"""LLM agent: Claude reads the rolling transcript after each new utterance and
decides whether to act, calling the shared tools in a loop until done."""

from __future__ import annotations

import json
import logging
import os

import anthropic

from ..meeting import Meeting, Utterance
from ..tools import TOOL_SCHEMAS, ToolBox

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
MAX_STEPS = 8

SYSTEM_PROMPT = """You are Sidekick, an assistant that sits in a live video meeting as a visible participant. \
You receive the rolling transcript and, after each new utterance, decide whether something needs doing right now.

Act only on clear signals:
- A meeting is proposed ("let's meet Thursday", "can we sync tomorrow at 3?", "let's circle back Wednesday \
afternoon"): treat it as agreed unless someone objects, and book it now without asking for confirmation. Call \
check_availability for the attendees, then schedule_meeting on the earliest slot that fits (or the exact slot if one \
was said and it is free). If the exact slot is taken, do not book; post the alternatives with post_to_meeting_chat.
- Someone commits to a task ("I'll send the deck by Friday") or accepts one: create_task with owner, short description \
and due date if one was said.
- The group states a decision: log_decision.
- Someone addresses you ("Hey Sidekick, email Dana the launch date"): do what they asked with the tools.

Rules:
- Default attendees are everyone on the call. If people outside the call are named for the meeting, add them.
- Meetings default to 30 minutes, within 09:00-17:00, never in the past.
- Resolve relative dates against the current time given below.
- Never repeat an action already listed under "Actions so far".
- Do not invent facts in emails; use what was said in the meeting.
- Messages to people outside the team are held for human approval automatically. That is expected; do not retry.
- If check_availability lists calendars_not_visible, you still may book, but say in the meeting chat whose calendar you could not check.
- If nothing in the new utterance needs action, reply with exactly NONE and call no tools.
- When you act, end with one short sentence saying what you did."""

SUMMARY_PROMPT = ("Write a concise meeting recap email body in plain text: 2-sentence overview, then Decisions, "
                  "Action items (owner, due), Meetings scheduled, Open questions. Use only the facts given.")


def meeting_context(meeting: Meeting, u: Utterance) -> str:
    """What the agent sees for one new utterance: people, time, actions so far, transcript."""
    ws = meeting.workspace
    people = "\n".join(
        f"- {p.name} <{p.email}> ({'team' if ws.is_internal(p.email) else 'external'})" for p in meeting.participants
    )
    contacts = "\n".join(
        f"- {p.name} <{p.email}> ({'team' if ws.is_internal(p.email) else 'external'})" for p in meeting.contacts
    ) or "- none"
    done = [f"- {f.title}: {f.detail}" for f in meeting.feed if f.kind in {"action", "approval"}]
    return (
        f"Meeting: {meeting.title}\n"
        f"Current time: {meeting.now():%A %Y-%m-%d %H:%M}\n\n"
        f"On the call:\n{people}\n\nOther known contacts:\n{contacts}\n\n"
        f"Actions so far:\n{chr(10).join(done) or '- none'}\n\n"
        f"Transcript (most recent last):\n{meeting.recent_transcript(30)}\n\n"
        f"New utterance to consider: {u.speaker}: \"{u.text}\""
    )


def summary_input(meeting: Meeting, facts: dict) -> str:
    return json.dumps(facts, default=str) + "\n\nTranscript:\n" + meeting.recent_transcript(200)


class ClaudeAgent:
    name = "claude"

    def __init__(self, model: str | None = None, client: anthropic.Anthropic | None = None):
        self.model = model or os.getenv("SIDEKICK_MODEL", DEFAULT_MODEL)
        self.effort = os.getenv("SIDEKICK_EFFORT", "low")
        self.client = client or anthropic.Anthropic()

    def handle(self, meeting: Meeting, tools: ToolBox, u: Utterance) -> list[str]:
        messages: list[dict] = [{"role": "user", "content": meeting_context(meeting, u)}]
        called: list[str] = []
        for _ in range(MAX_STEPS):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                meeting.log("warning", "Agent declined this request", getattr(response.stop_details, "explanation", "") or "")
                break
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                break
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in tool_uses:
                called.append(block.name)
                result = tools.execute(block.name, dict(block.input))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                    **({"is_error": True} if "error" in result else {}),
                })
            messages.append({"role": "user", "content": results})
        return called

    def summarize(self, meeting: Meeting, facts: dict) -> str:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SUMMARY_PROMPT,
            messages=[{"role": "user", "content": summary_input(meeting, facts)}],
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        return next((b.text for b in response.content if b.type == "text"), "")
