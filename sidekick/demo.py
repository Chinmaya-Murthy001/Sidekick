"""The demo meeting: a fictional product team (acme.io) and one external
client contact, with calendars seeded so scheduling has real conflicts."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .meeting import Meeting, fixed_clock
from .workspace import Participant, seed_demo_workspace

TEAM_DOMAIN = "acme.io"

PARTICIPANTS = [
    Participant("Sam Rivera", f"sam@{TEAM_DOMAIN}"),
    Participant("Priya Shah", f"priya@{TEAM_DOMAIN}"),
    Participant("Marcus Lee", f"marcus@{TEAM_DOMAIN}"),
    Participant("Alex Kim", f"alex@{TEAM_DOMAIN}"),
]
CONTACTS = [Participant("Dana Ortiz", "dana@globex.com")]

DEMO_SCRIPT = [
    {"speaker": "Sam Rivera", "text": "Thanks everyone for joining the launch sync."},
    {"speaker": "Priya Shah", "text": "The beta numbers look good, retention is up twelve percent."},
    {"speaker": "Marcus Lee", "text": "Great. We decided to go with the annual pricing plan for launch."},
    {"speaker": "Alex Kim", "text": "Let's meet Thursday to finalize the launch checklist."},
    {"speaker": "Priya Shah", "text": "I'll send the updated pricing deck to the team by Wednesday."},
    {"speaker": "Marcus Lee", "text": "Sam, can you draft the press release by Friday?"},
    {"speaker": "Sam Rivera", "text": "Sure, I'll take that."},
    {"speaker": "Alex Kim", "text": "Can we also sync on Tuesday at 2pm about the budget?"},
    {"speaker": "Marcus Lee", "text": "Who owns the app store screenshots?"},
    {"speaker": "Sam Rivera", "text": "Hey Sidekick, email Dana the launch date is November 18th."},
    {"speaker": "Priya Shah", "text": "Sidekick, stop recording for a minute."},
    {"speaker": "Marcus Lee", "text": "Off the record, the budget might get cut next quarter."},
    {"speaker": "Priya Shah", "text": "Okay Sidekick, resume."},
    {"speaker": "Sam Rivera", "text": "Great, thanks all."},
]


def demo_start(today: date | None = None) -> datetime:
    """Monday 10:00 of the current week, so 'Thursday' is always ahead."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return datetime.combine(monday, time(10, 0))


def build_demo_meeting(clock=None, start: datetime | None = None) -> Meeting:
    start = start or demo_start()
    workspace = seed_demo_workspace(TEAM_DOMAIN, start.date())
    return Meeting(
        "Q4 launch sync",
        list(PARTICIPANTS),
        workspace,
        clock=clock or fixed_clock(start),
        contacts=list(CONTACTS),
    )
