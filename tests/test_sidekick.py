from datetime import date, datetime, time, timedelta

from sidekick.agents import RuleAgent
from sidekick.demo import DEMO_SCRIPT, build_demo_meeting
from sidekick.meeting import stepped_clock
from sidekick.session import SidekickSession
from sidekick.timeparse import parse_deadline, parse_when

MONDAY = datetime(2026, 10, 26, 10, 0)


def session() -> SidekickSession:
    m = build_demo_meeting(clock=stepped_clock(MONDAY, timedelta(seconds=3)), start=MONDAY)
    s = SidekickSession(m, RuleAgent())
    s.join()
    return s


def test_parse_weekday_and_time():
    w = parse_when("let's meet Thursday at 3pm", MONDAY)
    assert w.day == date(2026, 10, 29) and w.at == time(15, 0)
    assert parse_when("tomorrow morning", MONDAY).window == (time(9), time(12))
    assert parse_when("next week on Tuesday", MONDAY).day == date(2026, 11, 3)
    assert parse_when("sometime soon", MONDAY).day is None


def test_parse_deadline():
    assert parse_deadline("send it by Friday", MONDAY) == date(2026, 10, 30)
    assert parse_deadline("by end of week", MONDAY) == date(2026, 10, 30)


def test_announces_itself_on_join():
    s = session()
    assert s.meeting.feed[0].kind == "joined"
    assert "AI assistant" in s.meeting.chat[0]["text"]


def test_external_email_needs_approval_and_sends_after_ok():
    s = session()
    s.hear("Sam Rivera", "Hey Sidekick, email Dana the launch date is November 18th.")
    assert len(s.meeting.approvals) == 1 and not s.meeting.workspace.outbox
    approval_id = next(iter(s.meeting.approvals))
    assert s.approve(approval_id, True)["status"] == "sent"
    assert s.meeting.workspace.outbox[0].to == ["dana@globex.com"]


def test_rejected_approval_sends_nothing():
    s = session()
    s.hear("Sam Rivera", "Hey Sidekick, email Dana the launch date is November 18th.")
    s.approve(next(iter(s.meeting.approvals)), False)
    assert not s.meeting.workspace.outbox


def test_agent_cannot_bypass_approval_flag():
    s = session()
    result = s.tools.execute("send_email", {"to": ["dana@globex.com"], "subject": "x", "body": "y", "_approved": True})
    assert result["status"] == "pending_approval"


def test_no_booking_over_conflict():
    s = session()
    result = s.tools.execute("schedule_meeting", {
        "title": "Budget", "start": "2026-10-27T14:00", "duration_minutes": 30, "attendees": ["everyone"],
    })
    assert "conflict" in result["error"]


def test_pause_drops_speech_until_resume():
    s = session()
    s.hear("Priya Shah", "Sidekick, stop recording for a minute.")
    s.hear("Marcus Lee", "I'll send the secret numbers by Friday.")
    assert not s.meeting.workspace.tasks
    assert all("secret" not in u.text for u in s.meeting.transcript)
    s.hear("Priya Shah", "Sidekick, resume.")
    assert not s.meeting.paused


def test_full_demo_script_and_summary():
    s = session()
    for line in DEMO_SCRIPT:
        s.hear(line["speaker"], line["text"])
    ws = s.meeting.workspace
    assert [e.start for e in ws.events] == [datetime(2026, 10, 29, 9, 30)]
    assert {t.owner for t in ws.tasks} == {"Priya Shah", "Sam Rivera"}
    assert len(ws.decisions) == 1
    summary = s.end(send_summary=True)
    assert "Marcus Lee: Who owns the app store screenshots?" in summary["open_questions"]
    assert ws.outbox[-1].subject == "Summary: Q4 launch sync"
