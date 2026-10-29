"""Giving Sidekick a meeting link: validation, the no-Recall fallback, and the
bot flow with Recall faked (join, webhook transcript, leave on end)."""

import os
import tempfile

os.environ.setdefault("SIDEKICK_DATA", tempfile.mkdtemp(prefix="sidekick-test-"))
os.environ["SIDEKICK_AGENT"] = "rules"

from fastapi.testclient import TestClient  # noqa: E402

from sidekick import recall, server  # noqa: E402

PEOPLE = [{"name": "Ana Silva", "email": "ana@acme.io", "team": True}, {"name": "Ben Ode", "email": "ben@acme.io", "team": True}]
MEET = "https://meet.google.com/abc-defg-hij"


def client():
    server._state["session"] = None
    return TestClient(server.app)


def start(c, **extra):
    return c.post("/api/meeting/start", json={"title": "Sync", "participants": PEOPLE, **extra})


def test_platform_detection():
    assert recall.platform(MEET) == "Google Meet"
    assert recall.platform("https://us02web.zoom.us/j/123456789?pwd=x") == "Zoom"
    assert recall.platform("https://teams.microsoft.com/l/meetup-join/19%3a") == "Microsoft Teams"
    assert recall.platform("https://example.com/meet") is None
    assert recall.platform("meet.google.com/abc-defg-hij") is None


def test_bad_link_rejected_with_a_clear_message():
    with client() as c:
        r = start(c, meeting_url="https://example.com/x")
        assert r.status_code == 422 and "meeting link" in r.json()["detail"]


def test_link_without_recall_is_saved_with_instructions(monkeypatch):
    monkeypatch.delenv("RECALL_API_KEY", raising=False)
    with client() as c:
        r = start(c, meeting_url=MEET)
        assert r.json()["bot"] == "none"
        m = c.get("/api/status").json()["meeting"]
        assert m["link"] == MEET
        assert any("Google Meet link saved" in f["title"] for f in m["feed"])
        c.post("/api/close")


def test_link_can_be_added_during_the_meeting(monkeypatch):
    monkeypatch.delenv("RECALL_API_KEY", raising=False)
    with client() as c:
        start(c)
        assert c.post("/api/meeting/link", json={"meeting_url": "nope"}).status_code == 422
        assert c.post("/api/meeting/link", json={"meeting_url": MEET}).json()["ok"]
        assert c.get("/api/status").json()["meeting"]["link"] == MEET
        c.post("/api/close")


def test_bot_joins_listens_and_leaves(monkeypatch):
    monkeypatch.setenv("RECALL_API_KEY", "test")
    monkeypatch.setenv("PUBLIC_URL", "https://sidekick.example.ngrok.app")
    calls = {}
    monkeypatch.setattr(recall, "create_bot", lambda url, hook: calls.update(join=(url, hook)) or {"id": "bot42"})
    monkeypatch.setattr(recall, "leave", lambda bot_id: calls.update(leave=bot_id))
    with client() as c:
        assert start(c, meeting_url=MEET).json()["bot"] == "joining"
        url, hook = calls["join"]
        assert url == MEET and hook.startswith("https://sidekick.example.ngrok.app/webhooks/recall?token=")
        token = hook.split("token=")[1]
        event = {"event": "transcript.data", "data": {"data": {
            "participant": {"name": "Ana Silva"}, "words": [{"text": "I'll"}, {"text": "send"}, {"text": "the"},
                                                             {"text": "notes"}, {"text": "by"}, {"text": "Friday."}]}}}
        assert c.post("/webhooks/recall?token=wrong", json=event).status_code == 403
        assert c.post(f"/webhooks/recall?token={token}", json=event).json()["ok"]
        m = c.get("/api/status").json()["meeting"]
        assert m["bot"] == "in_call" and m["tasks"][0]["owner"] == "Ana Silva"
        # Can't send a second bot while one is in the call.
        assert c.post("/api/meeting/link", json={"meeting_url": MEET}).status_code == 409
        c.post("/api/end")
        assert calls["leave"] == "bot42"
        assert c.get("/api/status").json()["meeting"]["bot"] == "left"
        c.post("/api/close")
