"""End-to-end through the HTTP API: set up a real (local-mode) meeting,
talk, approve, end, and find it again in the archive."""

import os
import tempfile
import time

os.environ["SIDEKICK_DATA"] = tempfile.mkdtemp(prefix="sidekick-test-")
os.environ["SIDEKICK_AGENT"] = "rules"

from fastapi.testclient import TestClient  # noqa: E402

from sidekick import server  # noqa: E402

PEOPLE = [
    {"name": "Ana Silva", "email": "ana@acme.io", "team": True},
    {"name": "Ben Ode", "email": "ben@acme.io", "team": True},
]


def client():
    server._state["session"] = None
    return TestClient(server.app)


def test_no_meeting_until_started():
    with client() as c:
        s = c.get("/api/status").json()
        assert s["meeting"] is None and s["agent"] == "rules" and s["google"]["connected"] is False
        assert c.post("/api/utterance", json={"speaker": "x", "text": "hi"}).status_code == 409


def test_bad_email_rejected():
    with client() as c:
        r = c.post("/api/meeting/start", json={"title": "T", "participants": [{"name": "A", "email": "nope"}]})
        assert r.status_code == 422


def test_full_meeting_is_archived_and_searchable():
    with client() as c:
        r = c.post("/api/meeting/start", json={
            "title": "Roadmap review", "participants": PEOPLE,
            "contacts": [{"name": "Dana Ortiz", "email": "dana@globex.com", "team": False}]})
        assert r.status_code == 200 and r.json()["provider"] == "local"
        mid = r.json()["id"]
        c.post("/api/utterance", json={"speaker": "Ana Silva", "text": "I'll write the launch FAQ by Friday."})
        c.post("/api/utterance", json={"speaker": "Ben Ode", "text": "Hey Sidekick, email Dana the pricing is final."})
        state = c.get("/api/status").json()["meeting"]
        assert state["tasks"][0]["owner"] == "Ana Silva"
        appr = state["pending"][0]["id"]
        assert c.post(f"/api/approvals/{appr}", json={"approve": True}).json()["status"] == "sent"
        summary = c.post("/api/end").json()
        assert summary["tasks"][0]["description"].startswith("Write the launch FAQ")
        # A second meeting can't start over a running one, but can after it ended.
        assert c.post("/api/close").json()["ok"]
        listed = c.get("/api/meetings").json()
        assert listed[0]["id"] == mid and listed[0]["ended"] and listed[0]["tasks"] == 1
        assert [m["id"] for m in c.get("/api/meetings?q=launch faq").json()] == [mid]
        assert c.get("/api/meetings?q=zebra").json() == []
        assert c.get(f"/api/meetings/{mid}").json()["summary"]["meeting"] == "Roadmap review"


def test_demo_runs_in_its_own_meeting():
    with client() as c:
        assert c.post("/api/demo").json()["ok"]
        for _ in range(100):
            if len(c.get("/api/status").json()["meeting"]["transcript"]) >= 2:
                break
            time.sleep(0.1)
        assert c.get("/api/status").json()["meeting"]["title"] == "Q4 launch sync"
        assert c.post("/api/demo").status_code == 409
        c.post("/api/close")
