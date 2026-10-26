"""FastAPI app: the live dashboard, the transcript intake, and the approval
buttons. Run with:  python -m sidekick.server"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from . import recall  # noqa: E402
from .agents import make_agent  # noqa: E402
from .demo import DEMO_SCRIPT, build_demo_meeting  # noqa: E402
from .session import SidekickSession, play_script  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Sidekick")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

_state: dict = {"session": None}


def current() -> SidekickSession:
    if _state["session"] is None:
        new_session()
    return _state["session"]


def new_session(agent_kind: str | None = None) -> SidekickSession:
    meeting = build_demo_meeting()
    try:
        meeting.loop = asyncio.get_running_loop()
    except RuntimeError:
        meeting.loop = None
    old = _state.get("session")
    session = SidekickSession(meeting, make_agent(agent_kind))
    if old is not None:
        # Move connected dashboards over to the new meeting.
        meeting._listeners = old.meeting._listeners
    _state["session"] = session
    session.join()
    meeting.publish({"type": "reset", "state": meeting.state_dict()})
    return session


class UtteranceIn(BaseModel):
    speaker: str
    text: str


class NewMeetingIn(BaseModel):
    agent: str | None = None


class ApprovalIn(BaseModel):
    approve: bool


class DemoIn(BaseModel):
    delay: float = 2.5


class JoinIn(BaseModel):
    meeting_url: str
    webhook_url: str


@app.on_event("startup")
async def _startup() -> None:
    new_session()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/state")
async def state() -> dict:
    s = current()
    return {**s.meeting.state_dict(), "agent": s.agent.name, "demo_script": DEMO_SCRIPT, "recall_enabled": recall.enabled()}


@app.post("/api/meeting/new")
async def meeting_new(body: NewMeetingIn) -> dict:
    s = new_session(body.agent)
    return {"ok": True, "agent": s.agent.name}


@app.post("/api/utterance")
async def utterance(body: UtteranceIn) -> dict:
    acted = await asyncio.to_thread(current().hear, body.speaker, body.text)
    return {"acted": acted}


@app.post("/api/demo/play")
async def demo_play(body: DemoIn) -> dict:
    s = current()
    threading.Thread(target=play_script, args=(s, DEMO_SCRIPT, body.delay), daemon=True).start()
    return {"ok": True, "lines": len(DEMO_SCRIPT)}


@app.post("/api/approvals/{approval_id}")
async def approval(approval_id: str, body: ApprovalIn) -> dict:
    result = await asyncio.to_thread(current().approve, approval_id, body.approve)
    if "error" in result and result["error"] == "no such pending approval":
        raise HTTPException(404, result["error"])
    return result


@app.post("/api/end")
async def end() -> dict:
    return await asyncio.to_thread(current().end)


@app.post("/api/bot/join")
async def bot_join(body: JoinIn) -> dict:
    if not recall.enabled():
        raise HTTPException(400, "Set RECALL_API_KEY to send a bot into a real meeting.")
    return await asyncio.to_thread(recall.create_bot, body.meeting_url, body.webhook_url)


@app.post("/webhooks/recall")
async def recall_webhook(request: Request) -> dict:
    parsed = recall.parse_transcript_event(await request.json())
    if parsed:
        await asyncio.to_thread(current().hear, *parsed)
    return {"ok": True}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    meeting = current().meeting
    q = meeting.subscribe()
    try:
        await websocket.send_json({"type": "reset", "state": meeting.state_dict()})
        while True:
            await websocket.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        current().meeting.unsubscribe(q)


def main() -> None:
    import uvicorn

    uvicorn.run("sidekick.server:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
