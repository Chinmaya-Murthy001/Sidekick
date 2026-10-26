"""Optional connection to a real Google Meet call through Recall.ai.

Recall runs the bot that joins the call and posts finished transcript
segments to our webhook. Without RECALL_API_KEY the dashboard's own
microphone and text input feed the same pipeline instead.

Note: written against Recall's v1 bot API; it has not been run end to end
in this prototype because it needs a paid key and a public webhook URL.
"""

from __future__ import annotations

import os

import httpx

RECALL_REGION = os.getenv("RECALL_REGION", "us-east-1")


def enabled() -> bool:
    return bool(os.getenv("RECALL_API_KEY"))


def create_bot(meeting_url: str, webhook_url: str, bot_name: str = "Sidekick (AI assistant)") -> dict:
    resp = httpx.post(
        f"https://{RECALL_REGION}.recall.ai/api/v1/bot/",
        headers={"Authorization": f"Token {os.environ['RECALL_API_KEY']}"},
        json={
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "recording_config": {
                "transcript": {"provider": {"meeting_captions": {}}},
                "realtime_endpoints": [{"type": "webhook", "url": webhook_url, "events": ["transcript.data"]}],
            },
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def parse_transcript_event(payload: dict) -> tuple[str, str] | None:
    """Extract (speaker, text) from a transcript.data webhook payload."""
    if payload.get("event") != "transcript.data":
        return None
    data = payload.get("data", {}).get("data", {})
    words = data.get("words") or []
    text = " ".join(w.get("text", "") for w in words).strip()
    speaker = (data.get("participant") or {}).get("name") or "Unknown speaker"
    return (speaker, text) if text else None
