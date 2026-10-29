"""OpenRouter agent against a fake OpenRouter: the request shape, the tool
loop (call → result → next turn), errors, and how the agent is chosen."""

import copy
import json
from datetime import datetime, timedelta

import httpx
import pytest

from sidekick.agents import RuleAgent, agent_label, make_agent
from sidekick.agents import openrouter_agent as ora
from sidekick.demo import build_demo_meeting
from sidekick.meeting import stepped_clock
from sidekick.session import SidekickSession

MONDAY = datetime(2026, 10, 26, 10, 0)


class FakeOpenRouter:
    """Replies with a scripted list of assistant messages and records requests."""

    def __init__(self, replies, status=200):
        self.replies, self.status, self.requests = list(replies), status, []

    def __call__(self, url, headers, json, timeout):
        self.requests.append({"url": url, "headers": headers, "body": copy.deepcopy(json)})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "Insufficient credits"}})
        return httpx.Response(200, json={"choices": [{"message": self.replies.pop(0)}]})


def call(name, args, id_="c1"):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def session(monkeypatch, fake):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setattr(ora.httpx, "post", fake)
    m = build_demo_meeting(clock=stepped_clock(MONDAY, timedelta(seconds=3)), start=MONDAY)
    s = SidekickSession(m, ora.OpenRouterAgent())
    s.join()
    return s


def test_tool_loop_books_the_meeting(monkeypatch):
    fake = FakeOpenRouter([
        {"role": "assistant", "content": None, "tool_calls": [call("check_availability", {"attendees": ["everyone"], "day": "2026-10-29"})]},
        {"role": "assistant", "content": None, "tool_calls": [call("schedule_meeting", {
            "title": "Launch checklist", "start": "2026-10-29T09:30", "duration_minutes": 30, "attendees": ["everyone"]}, "c2")]},
        {"role": "assistant", "content": "Booked Thursday 09:30."},
    ])
    s = session(monkeypatch, fake)
    acted = s.hear("Alex Kim", "Let's meet Thursday to finalize the launch checklist.")
    assert acted == ["check_availability", "schedule_meeting"]
    assert [e.start for e in s.meeting.workspace.events] == [datetime(2026, 10, 29, 9, 30)]

    first = fake.requests[0]
    assert first["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert first["headers"]["Authorization"] == "Bearer sk-or-test"
    assert first["body"]["model"] == "anthropic/claude-sonnet-5.5"
    assert "temperature" not in first["body"]
    names = {t["function"]["name"] for t in first["body"]["tools"]}
    assert {"check_availability", "schedule_meeting", "create_task", "send_email"} <= names
    assert first["body"]["messages"][0]["role"] == "system"
    assert "Let's meet Thursday" in first["body"]["messages"][1]["content"]
    # The tool result goes back in the next turn, tied to the call id.
    second = fake.requests[1]["body"]["messages"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"
    assert "free_slots" in second[-1]["content"]


def test_guardrails_still_apply(monkeypatch):
    fake = FakeOpenRouter([
        {"role": "assistant", "content": None, "tool_calls": [call("send_email", {
            "to": ["dana@globex.com"], "subject": "Launch", "body": "Nov 18", "_approved": True})]},
        {"role": "assistant", "content": "Held for approval."},
    ])
    s = session(monkeypatch, fake)
    s.hear("Sam Rivera", "Hey Sidekick, email Dana the launch date is November 18th.")
    assert len(s.meeting.approvals) == 1 and not s.meeting.workspace.outbox


def test_api_error_is_logged_not_crashing(monkeypatch):
    s = session(monkeypatch, FakeOpenRouter([], status=402))
    assert s.hear("Priya Shah", "I'll send the deck by Wednesday.") == []
    err = s.meeting.feed[-1]
    assert err.kind == "error" and "Insufficient credits" in err.detail


def test_summary_uses_openrouter(monkeypatch):
    fake = FakeOpenRouter([{"role": "assistant", "content": "Recap: one decision."}])
    s = session(monkeypatch, fake)
    assert s.end(send_summary=False)["recap"] == "Recap: one decision."
    assert "tools" not in fake.requests[0]["body"]


@pytest.mark.parametrize("env,expected", [
    ({"OPENROUTER_API_KEY": "k"}, "openrouter"),
    ({"ANTHROPIC_API_KEY": "k"}, "claude"),
    ({"OPENROUTER_API_KEY": "k", "ANTHROPIC_API_KEY": "k"}, "openrouter"),
    ({}, "rules"),
    ({"OPENROUTER_API_KEY": "k", "SIDEKICK_AGENT": "rules"}, "rules"),
])
def test_agent_choice(monkeypatch, env, expected):
    for k in ["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "SIDEKICK_AGENT"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert make_agent().name == expected


def test_label(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert agent_label(ora.OpenRouterAgent()) == "Sonnet 5.5 · OpenRouter"
    assert agent_label(RuleAgent()) == "Offline planner"
