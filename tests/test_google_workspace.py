"""GoogleWorkspace against a fake Google API: checks the exact requests we
send and how we read the replies. No network or credentials needed."""

import base64
from datetime import datetime, timedelta
from email import message_from_bytes

from googleapiclient.errors import HttpError

from sidekick.agents import RuleAgent
from sidekick.google_workspace import GoogleWorkspace
from sidekick.meeting import Meeting, stepped_clock
from sidekick.session import SidekickSession
from sidekick.workspace import Participant, Workspace, local_tz

NOW = datetime(2026, 11, 2, 10, 0)  # a Monday


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeCalendar:
    def __init__(self, busy=None, hidden=()):
        self.busy, self.hidden = busy or {}, set(hidden)
        self.queries, self.inserted = [], []
        self.fail_insert = False

    def freebusy(self):
        return self

    def events(self):
        return self

    def query(self, body):
        self.queries.append(body)

        def run():
            cals = {}
            for item in body["items"]:
                e = item["id"]
                cals[e] = {"errors": [{"reason": "notFound"}]} if e in self.hidden else {
                    "busy": [{"start": s.replace(tzinfo=local_tz()).isoformat(), "end": f.replace(tzinfo=local_tz()).isoformat()}
                             for s, f in self.busy.get(e, [])]}
            return {"calendars": cals}
        return _Call(run)

    def insert(self, calendarId, body, sendUpdates):
        def run():
            if self.fail_insert:
                raise HttpError(type("R", (), {"status": 403, "reason": "Forbidden"})(), b"{}")
            self.inserted.append({"calendarId": calendarId, "body": body, "sendUpdates": sendUpdates})
            return {"id": "gcal123", "htmlLink": "https://calendar.google.com/event?eid=gcal123"}
        return _Call(run)


class FakeGmail:
    def __init__(self):
        self.sent = []

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):
        return _Call(lambda: self.sent.append(message_from_bytes(base64.urlsafe_b64decode(body["raw"]))) or {"id": "m1"})


def make(busy=None, hidden=()):
    cal, gmail = FakeCalendar(busy, hidden), FakeGmail()
    ws = GoogleWorkspace(None, "host@acme.io", services={"calendar": cal, "gmail": gmail},
                         internal_emails={"host@acme.io", "bo@acme.io"})
    m = Meeting("Planning", [Participant("Host", "host@acme.io"), Participant("Bo", "bo@acme.io")], ws,
                clock=stepped_clock(NOW, timedelta(seconds=3)),
                contacts=[Participant("Dana Ortiz", "dana@globex.com")])
    s = SidekickSession(m, RuleAgent())
    s.join()
    return s, cal, gmail


def test_books_around_real_busy_time_and_google_sends_invites():
    thursday = datetime(2026, 11, 5)
    s, cal, gmail = make(busy={"bo@acme.io": [(thursday.replace(hour=9), thursday.replace(hour=11))]})
    s.hear("Host", "Let's meet Thursday to review the roadmap.")
    assert cal.queries and {i["id"] for i in cal.queries[0]["items"]} == {"host@acme.io", "bo@acme.io"}
    assert cal.queries[0]["timeMin"].endswith(datetime(2026, 11, 5).replace(tzinfo=local_tz()).isoformat()[-6:])
    ins = cal.inserted[0]
    assert ins["sendUpdates"] == "all" and ins["calendarId"] == "primary"
    assert ins["body"]["start"]["dateTime"].startswith("2026-11-05T11:00")
    # The organiser is the signed-in account, so only the others are listed as guests.
    assert [a["email"] for a in ins["body"]["attendees"]] == ["bo@acme.io"]
    ev = s.meeting.workspace.events[0]
    assert ev.link.startswith("https://calendar.google.com") and s.meeting.workspace.outbox[0].delivered
    assert not gmail.sent  # Google emailed the invitation, not us


def test_hidden_calendar_is_reported_in_chat():
    s, cal, _ = make(hidden=["dana@globex.com"])
    s.hear("Host", "Let's meet with Dana next Tuesday at 10am about the contract.")
    # External guest: held for approval, nothing created yet.
    assert not cal.inserted and len(s.meeting.approvals) == 1
    s.approve(next(iter(s.meeting.approvals)), True)
    assert cal.inserted and "dana@globex.com" in [a["email"] for a in cal.inserted[0]["body"]["attendees"]]


def test_email_goes_through_gmail():
    s, _, gmail = make()
    s.hear("Host", "Sidekick, email Bo the notes about the beta numbers.")
    msg = gmail.sent[0]
    assert msg["To"] == "bo@acme.io" and msg["From"] == "host@acme.io"
    assert "beta numbers" in msg.get_payload().lower()


def test_google_error_is_reported_not_crashing():
    s, cal, _ = make()
    cal.fail_insert = True
    s.hear("Host", "Let's meet Thursday to review the roadmap.")
    assert not s.meeting.workspace.events
    assert s.tools.calls[-1]["result"]["error"].startswith("Google Calendar refused")


def test_public_mail_domain_does_not_make_everyone_a_teammate():
    ws = Workspace(team_domain="gmail.com", internal_emails={"me@gmail.com"})
    assert ws.is_internal("me@gmail.com") and not ws.is_internal("stranger@gmail.com")
    assert Workspace(team_domain="acme.io").is_internal("x@acme.io")
