"""Hearing the whole call: /api/audio transcribes a piece of shared audio and
feeds each turn into the meeting. The transcription service is faked."""

import os
import tempfile

os.environ.setdefault("SIDEKICK_DATA", tempfile.mkdtemp(prefix="sidekick-test-"))
os.environ["SIDEKICK_AGENT"] = "rules"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sidekick import server, transcribe  # noqa: E402

PEOPLE = [{"name": "Chinmaya", "email": "c@acme.io", "team": True}, {"name": "Pranu", "email": "p@acme.io", "team": True}]


def client():
    server._state["session"] = None
    return TestClient(server.app)


def test_audio_turns_reach_the_meeting(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    seen = {}

    def fake(audio, fmt, people, recent):
        seen.update(audio=audio, fmt=fmt, people=people)
        return [{"speaker": "Pranu", "text": "I'll send the updated deck by Friday."}]

    monkeypatch.setattr(transcribe, "transcribe", fake)
    with client() as c:
        c.post("/api/meeting/start", json={"title": "Sync", "participants": PEOPLE})
        r = c.post("/api/audio", json={"audio": "UklGRg==", "format": "wav"}).json()
        assert r["heard"] == 1 and seen["people"] == ["Chinmaya", "Pranu"] and seen["fmt"] == "wav"
        m = c.get("/api/status").json()["meeting"]
        assert m["transcript"][-1]["speaker"] == "Pranu"
        assert m["tasks"][0]["owner"] == "Pranu"
        c.post("/api/close")


def test_nothing_is_transcribed_while_paused(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    calls = []
    monkeypatch.setattr(transcribe, "transcribe", lambda *a: calls.append(a) or [])
    with client() as c:
        c.post("/api/meeting/start", json={"title": "Sync", "participants": PEOPLE})
        c.post("/api/utterance", json={"speaker": "Pranu", "text": "Sidekick, stop recording."})
        assert c.post("/api/audio", json={"audio": "UklGRg==", "format": "wav"}).json() == {"heard": 0, "paused": True}
        assert calls == []
        c.post("/api/close")


def test_needs_a_key_and_a_known_format(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with client() as c:
        c.post("/api/meeting/start", json={"title": "Sync", "participants": PEOPLE})
        assert c.post("/api/audio", json={"audio": "x", "format": "wav"}).status_code == 400
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        assert c.post("/api/audio", json={"audio": "x", "format": "ogg"}).status_code == 422
        assert c.get("/api/status").json()["call_audio"] is True
        c.post("/api/close")


def test_transcription_error_is_shown(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(transcribe.httpx, "post", lambda *a, **k: httpx.Response(402, json={"error": {"message": "No credits"}}))
    with client() as c:
        c.post("/api/meeting/start", json={"title": "Sync", "participants": PEOPLE})
        r = c.post("/api/audio", json={"audio": "x", "format": "wav"})
        assert r.status_code == 502 and "No credits" in r.json()["detail"]
        assert c.get("/api/status").json()["meeting"]["feed"][-1]["kind"] == "error"
        c.post("/api/close")


def test_reply_parsing():
    assert transcribe._parse('```json\n{"utterances": [{"speaker": "Speaker 1", "text": "Hi"}]}\n```') == [
        {"speaker": "Speaker 1", "text": "Hi"}]
    assert transcribe._parse("no speech") == []
    assert transcribe._parse('{"utterances": [{"speaker": "A", "text": "  "}]}') == []
