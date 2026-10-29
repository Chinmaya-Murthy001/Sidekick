"""Send Sidekick into a real call (Google Meet, Zoom or Teams) through Recall.ai.

Recall runs the bot that joins the call and posts finished transcript
segments to our webhook. Without RECALL_API_KEY and PUBLIC_URL, you open the
call yourself and the dashboard's microphone feeds the same pipeline.

Note: written against Recall's v1 bot API; it has not been run end to end
in this prototype because it needs a paid key and a public webhook URL.
"""

from __future__ import annotations

import os
import re

import httpx

# Links Recall's bots can join.
MEETING_LINK_RE = re.compile(
    r"^https://(meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}"
    r"|([\w-]+\.)?zoom\.us/(j|my|w)/\S+"
    r"|teams\.(microsoft|live)\.com/\S+)$",
    re.I,
)


def platform(url: str) -> str | None:
    """"Google Meet", "Zoom" or "Microsoft Teams" for a valid meeting link, else None."""
    url = url.strip()
    if not MEETING_LINK_RE.match(url):
        return None
    if "meet.google.com" in url:
        return "Google Meet"
    if "zoom.us" in url:
        return "Zoom"
    return "Microsoft Teams"


def enabled() -> bool:
    return bool(os.getenv("RECALL_API_KEY"))


def ready() -> bool:
    """A bot can join only if Recall can also reach our webhook."""
    return enabled() and bool(os.getenv("PUBLIC_URL"))


def webhook_url() -> str:
    return os.getenv("PUBLIC_URL", "").rstrip("/") + "/webhooks/recall"


def _base() -> str:
    return f"https://{os.getenv('RECALL_REGION') or 'us-east-1'}.recall.ai/api/v1"


def _headers() -> dict:
    return {"Authorization": f"Token {os.environ['RECALL_API_KEY']}"}


def create_bot(meeting_url: str, webhook: str, bot_name: str = "Sidekick (AI assistant)") -> dict:
    resp = httpx.post(
        f"{_base()}/bot/",
        headers=_headers(),
        json={
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "recording_config": {
                "transcript": {"provider": {"meeting_captions": {}}},
                "realtime_endpoints": [{"type": "webhook", "url": webhook, "events": ["transcript.data"]}],
            },
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def leave(bot_id: str) -> None:
    resp = httpx.post(f"{_base()}/bot/{bot_id}/leave_call/", headers=_headers(), timeout=20)
    resp.raise_for_status()


def parse_transcript_event(payload: dict) -> tuple[str, str] | None:
    """Extract (speaker, text) from a transcript.data webhook payload."""
    if payload.get("event") != "transcript.data":
        return None
    data = payload.get("data", {}).get("data", {})
    words = data.get("words") or []
    text = " ".join(w.get("text", "") for w in words).strip()
    speaker = (data.get("participant") or {}).get("name") or "Unknown speaker"
    return (speaker, text) if text else None
