"""Turn a short piece of call audio into who-said-what, using an audio-capable
model on OpenRouter. Used when the page shares a meeting tab's sound, so the
whole call is heard without a meeting bot."""

from __future__ import annotations

import json
import os
import re

import httpx

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.5-flash-lite"

PROMPT = """You transcribe live meeting audio for a meeting assistant.
People in the meeting: {people}.
Recent transcript, for context and to keep speaker labels consistent:
{recent}

Transcribe the attached audio exactly, split into turns by speaker. For each turn give the speaker:
use a person's name only when you are confident (they are addressed by name, introduce themselves, or the voice clearly
matches an earlier labelled turn); otherwise use "Speaker 1", "Speaker 2", ... consistently.
Skip silence, music, and noise. Do not summarise or correct what was said.
Reply with JSON only: {{"utterances": [{{"speaker": "...", "text": "..."}}]}}. If nobody speaks, reply {{"utterances": []}}."""


class TranscribeError(Exception):
    pass


def enabled() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


def model() -> str:
    return os.getenv("OPENROUTER_AUDIO_MODEL") or DEFAULT_MODEL


def _parse(content: str) -> list[dict]:
    """Pull the utterances out of the model's reply, tolerating code fences or extra text."""
    match = re.search(r"\{.*\}", content or "", flags=re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for u in data.get("utterances") or []:
        text = str(u.get("text", "")).strip()
        if text:
            out.append({"speaker": str(u.get("speaker") or "Speaker").strip()[:60], "text": text[:1000]})
    return out


def transcribe(audio_b64: str, fmt: str, people: list[str], recent: str) -> list[dict]:
    """Return [{"speaker", "text"}, ...] for one audio piece."""
    body = {
        "model": model(),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT.format(people=", ".join(people) or "unknown", recent=recent or "(none yet)")},
                {"type": "input_audio", "input_audio": {"data": audio_b64, "format": fmt}},
            ],
        }],
        "max_tokens": 1500,
    }
    try:
        resp = httpx.post(
            API_URL,
            headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "X-Title": "Sidekick"},
            json=body,
            timeout=45,
        )
    except httpx.TransportError as e:
        raise TranscribeError(f"Transcription service did not answer: {type(e).__name__}") from e
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            detail = resp.text
        raise TranscribeError(f"Transcription failed ({resp.status_code}): {detail[:200]}")
    choices = resp.json().get("choices") or []
    if not choices:
        raise TranscribeError("Transcription returned nothing")
    return _parse(choices[0]["message"].get("content") or "")
