"""FastAPI app: meeting setup, the live dashboard, transcript intake,
approvals, Google sign-in and the meeting archive. Run with:
    python -m sidekick.server"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from . import google_auth, recall  # noqa: E402
from .agents import make_agent  # noqa: E402
from .demo import DEMO_SCRIPT, build_demo_meeting  # noqa: E402
from .meeting import Meeting  # noqa: E402
from .session import SidekickSession, play_script  # noqa: E402
from .store import Store  # noqa: E402
from .workspace import Participant, Workspace  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

WEBHOOK_TOKEN = os.getenv("RECALL_WEBHOOK_TOKEN") or secrets.token_urlsafe(24)

store = Store(google_auth.DATA_DIR / "sidekick.db")
_state: dict = {"session": None, "listeners": set(), "loop": None}

@asynccontextmanager
async def lifespan(_app):
    _state["loop"] = asyncio.get_running_loop()
    yield


app = FastAPI(title="Sidekick", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")



def current() -> SidekickSession:
    s = _state["session"]
    if s is None:
        raise HTTPException(409, "No meeting is running. Start one first.")
    return s


def broadcast(message: dict) -> None:
    for q in list(_state["listeners"]):
        q.put_nowait(message)


def install(meeting: Meeting, agent_kind: str | None = None) -> SidekickSession:
    """Make this the running meeting and connect the dashboards to it."""
    meeting.loop = _state["loop"]
    meeting._listeners = _state["listeners"]
    session = SidekickSession(meeting, make_agent(agent_kind), store=store)
    _state["session"] = session
    session.join()
    broadcast({"type": "reset", "state": meeting.state_dict()})
    return session


def google_workspace(internal: set[str]):
    """A Google-backed workspace when the user has connected Google, else None."""
    try:
        loaded = google_auth.load()
    except Exception:
        return None
    if not loaded:
        return None
    from .google_workspace import GoogleWorkspace
    creds, email = loaded
    return GoogleWorkspace(creds, email, internal_emails=internal | ({email.lower()} if email else set()))


# --- models -------------------------------------------------------------
class Person(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str
    team: bool = True


class StartIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    participants: list[Person] = Field(min_length=1, max_length=50)
    contacts: list[Person] = []
    meeting_url: str = ""
    agent: str | None = None


class UtteranceIn(BaseModel):
    speaker: str
    text: str


class ApprovalIn(BaseModel):
    approve: bool


class LinkIn(BaseModel):
    meeting_url: str


def check_link(url: str) -> str:
    url = url.strip()
    if not recall.platform(url):
        raise HTTPException(422, "That doesn't look like a meeting link. Paste a Google Meet, Zoom or Teams link "
                                 "(for example https://meet.google.com/abc-defg-hij).")
    return url


async def attach_link(session: SidekickSession, url: str) -> None:
    """Remember the call's link and, if Recall is set up, send the bot in."""
    m = session.meeting
    m.link = url
    where = recall.platform(url)
    if not recall.ready():
        m.log("info", f"{where} link saved",
              "Sidekick can't join by itself yet (needs RECALL_API_KEY and PUBLIC_URL). "
              "Open the call from the link above and keep this page's 🎤 on.", link=url)
        return
    m.bot = "joining"
    m.log("info", f"Sending Sidekick into the {where} call", "The host may need to admit “Sidekick (AI assistant)”.", link=url)
    try:
        bot = await asyncio.to_thread(recall.create_bot, url, f"{recall.webhook_url()}?token={WEBHOOK_TOKEN}")
        m.bot_id = bot.get("id", "")
    except Exception as e:
        m.bot = "failed"
        m.log("error", "Sidekick could not join the call", str(e), status="failed")
    m.publish({"type": "state", "state": m.state_dict(include_transcript=False)})


# --- lifecycle ----------------------------------------------------------
@app.get("/")
async def index() -> FileResponse:
    # Always fetch the page fresh so new script versions are picked up.
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/status")
async def status() -> dict:
    s = _state["session"]
    loaded = None
    try:
        loaded = google_auth.load()
    except Exception:
        loaded = None
    agent = make_agent()
    return {
        "meeting": s.meeting.state_dict() if s else None,
        "meeting_agent": s.agent.name if s else None,
        "agent": agent.name,
        "google": {"configured": google_auth.configured(), "connected": bool(loaded), "email": loaded[1] if loaded else None},
        "recall": {"enabled": recall.enabled(), "ready": recall.ready()},
    }


@app.post("/api/meeting/start")
async def meeting_start(body: StartIn) -> dict:
    s = _state["session"]
    if s and not s.meeting.ended:
        raise HTTPException(409, "A meeting is already running. End it first.")
    people = body.participants + body.contacts
    bad = [p.email for p in people if not EMAIL_RE.match(p.email.strip())]
    if bad:
        raise HTTPException(422, f"Not a valid email: {', '.join(bad)}")
    link = check_link(body.meeting_url) if body.meeting_url.strip() else ""
    internal = {p.email.strip().lower() for p in people if p.team}
    workspace = google_workspace(internal) or Workspace(internal_emails=internal)
    meeting = Meeting(
        body.title.strip(),
        [Participant(p.name.strip(), p.email.strip().lower()) for p in body.participants],
        workspace,
        contacts=[Participant(p.name.strip(), p.email.strip().lower()) for p in body.contacts],
    )
    session = install(meeting, body.agent)
    if link:
        await attach_link(session, link)
    return {"ok": True, "id": meeting.id, "agent": session.agent.name, "provider": workspace.provider, "bot": meeting.bot}


@app.post("/api/demo")
async def demo() -> dict:
    """Load the sample meeting (fictional team, sandbox calendar) and play its script."""
    s = _state["session"]
    if s and not s.meeting.ended:
        raise HTTPException(409, "A meeting is already running. End it first.")
    session = install(build_demo_meeting())
    threading.Thread(target=play_script, args=(session, DEMO_SCRIPT, 2.5), daemon=True).start()
    return {"ok": True}


@app.post("/api/utterance")
async def utterance(body: UtteranceIn) -> dict:
    acted = await asyncio.to_thread(current().hear, body.speaker, body.text)
    return {"acted": acted}


@app.post("/api/approvals/{approval_id}")
async def approval(approval_id: str, body: ApprovalIn) -> dict:
    result = await asyncio.to_thread(current().approve, approval_id, body.approve)
    if result.get("error") == "no such pending approval":
        raise HTTPException(404, result["error"])
    return result


async def finish(s: SidekickSession) -> dict:
    """End the meeting and take the bot out of the call."""
    summary = await asyncio.to_thread(s.end)
    m = s.meeting
    if m.bot_id and m.bot in {"joining", "in_call"}:
        try:
            await asyncio.to_thread(recall.leave, m.bot_id)
            m.bot = "left"
        except Exception as e:
            m.log("warning", "Couldn't take the bot out of the call", str(e))
    return summary


@app.post("/api/end")
async def end() -> dict:
    return await finish(current())


@app.post("/api/meeting/link")
async def meeting_link(body: LinkIn) -> dict:
    """Give the running meeting its call link (and send the bot in, if set up)."""
    s = current()
    if s.meeting.ended:
        raise HTTPException(409, "This meeting has ended.")
    if s.meeting.bot in {"joining", "in_call"}:
        raise HTTPException(409, "Sidekick is already in a call for this meeting.")
    await attach_link(s, check_link(body.meeting_url))
    return {"ok": True, "bot": s.meeting.bot}


@app.post("/api/close")
async def close() -> dict:
    """Leave the finished meeting and go back to setup."""
    s = _state["session"]
    if s and not s.meeting.ended:
        await finish(s)
    _state["session"] = None
    broadcast({"type": "reset", "state": None})
    return {"ok": True}


# --- archive ------------------------------------------------------------
@app.get("/api/meetings")
async def meetings(q: str = "") -> list[dict]:
    return store.search(q) if q.strip() else store.list()


@app.get("/api/meetings/{meeting_id}")
async def meeting_detail(meeting_id: str) -> dict:
    found = store.get(meeting_id)
    if not found:
        raise HTTPException(404, "No such meeting")
    return found


# --- Google sign-in -----------------------------------------------------
@app.get("/auth/google/start")
async def google_start(request: Request):
    if not google_auth.configured():
        raise HTTPException(400, "Add data/google_client_secret.json first (see README).")
    return RedirectResponse(google_auth.start(str(request.url_for("google_callback"))))


@app.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request):
    if request.query_params.get("error"):
        return HTMLResponse(f"<p>Google sign-in was cancelled ({request.query_params['error']}). <a href='/'>Back</a></p>")
    try:
        await asyncio.to_thread(google_auth.finish, request.query_params.get("state", ""), str(request.url))
    except Exception as e:
        return HTMLResponse(f"<p>Google sign-in failed: {e}. <a href='/'>Back</a></p>", status_code=400)
    return RedirectResponse("/")


@app.post("/auth/google/disconnect")
async def google_disconnect() -> dict:
    google_auth.disconnect()
    return {"ok": True}


# --- real calls via Recall.ai ------------------------------------------
@app.post("/webhooks/recall")
async def recall_webhook(request: Request) -> dict:
    # Only Recall knows this token (it is in the URL we gave it), so nobody
    # else can inject speech into the meeting through the public URL.
    if not secrets.compare_digest(request.query_params.get("token", ""), WEBHOOK_TOKEN):
        raise HTTPException(403, "Bad token")
    parsed = recall.parse_transcript_event(await request.json())
    s = _state["session"]
    if parsed and s and not s.meeting.ended:
        if s.meeting.bot == "joining":
            s.meeting.bot = "in_call"
            s.meeting.log("info", "Sidekick is in the call and listening")
        await asyncio.to_thread(s.hear, *parsed)
    return {"ok": True}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue()
    _state["listeners"].add(q)
    try:
        s = _state["session"]
        await websocket.send_json({"type": "reset", "state": s.meeting.state_dict() if s else None})
        while True:
            await websocket.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _state["listeners"].discard(q)


def main() -> None:
    import uvicorn

    uvicorn.run("sidekick.server:app", host="127.0.0.1", port=int(os.getenv("PORT") or 8000))


if __name__ == "__main__":
    main()
