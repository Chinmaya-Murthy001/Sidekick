"""The same agent loop, run through OpenRouter (one key, many models, Claude
included). OpenRouter speaks the OpenAI-style chat API, so the tool schemas
are converted to that format here; the prompt, context and tools are shared
with the Claude agent."""

from __future__ import annotations

import json
import os

import httpx

from ..meeting import Meeting, Utterance
from ..tools import TOOL_SCHEMAS, ToolBox
from .claude_agent import MAX_STEPS, SUMMARY_PROMPT, SYSTEM_PROMPT, meeting_context, summary_input

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"

OPENAI_TOOLS = [
    {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
    for t in TOOL_SCHEMAS
]


class OpenRouterError(Exception):
    pass


class OpenRouterAgent:
    name = "openrouter"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or os.environ["OPENROUTER_API_KEY"]
        # Meeting actions are simple; low reasoning effort keeps replies fast.
        self.effort = os.getenv("SIDEKICK_EFFORT") or "low"

    def _post(self, body: dict) -> httpx.Response:
        """One request, retried once on a timeout or a temporary server error."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            # Optional attribution headers OpenRouter shows in its dashboard.
            "HTTP-Referer": os.getenv("PUBLIC_URL") or "http://localhost:8000",
            "X-Title": "Sidekick",
        }
        for attempt in (1, 2):
            try:
                resp = httpx.post(API_URL, headers=headers, json=body, timeout=30)
            except httpx.TransportError as e:
                if attempt == 2:
                    raise OpenRouterError(f"OpenRouter did not answer: {type(e).__name__}") from e
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt == 1:
                continue
            return resp
        return resp

    def _chat(self, messages: list[dict], tools: bool = True, max_tokens: int = 2048) -> dict:
        # No temperature: current Claude models reject non-default sampling settings.
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                "reasoning": {"effort": self.effort}}
        if tools:
            body["tools"] = OPENAI_TOOLS
        resp = self._post(body)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", {}).get("message", resp.text)
            except ValueError:
                detail = resp.text
            raise OpenRouterError(f"OpenRouter {resp.status_code}: {detail}")
        data = resp.json()
        if not data.get("choices"):
            raise OpenRouterError(f"OpenRouter returned no answer: {data.get('error') or data}")
        return data["choices"][0]["message"]

    def handle(self, meeting: Meeting, tools: ToolBox, u: Utterance) -> list[str]:
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": meeting_context(meeting, u)},
        ]
        called: list[str] = []
        for _ in range(MAX_STEPS):
            msg = self._chat(messages)
            calls = msg.get("tool_calls") or []
            if not calls:
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                called.append(fn.get("name", ""))
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    result = {"error": "arguments were not valid JSON"}
                else:
                    result = tools.execute(fn.get("name", ""), args if isinstance(args, dict) else {})
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, default=str)})
        return called

    def summarize(self, meeting: Meeting, facts: dict) -> str:
        msg = self._chat(
            [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": summary_input(meeting, facts)}],
            tools=False,
        )
        return msg.get("content") or ""
