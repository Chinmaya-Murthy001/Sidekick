"""Meeting archive in a local SQLite file, so transcripts, tasks and
summaries survive a restart and past meetings can be looked up."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .meeting import Meeting

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    started_at TEXT,
    ended INTEGER NOT NULL DEFAULT 0,
    agent TEXT,
    provider TEXT,
    state TEXT NOT NULL
)
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(SCHEMA)
        self._db.commit()
        self._lock = threading.Lock()

    def save(self, meeting: Meeting, agent: str) -> None:
        state = meeting.state_dict()
        with self._lock:
            self._db.execute(
                "INSERT INTO meetings (id, title, started_at, ended, agent, provider, state) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, ended=excluded.ended, state=excluded.state",
                (meeting.id, meeting.title, state["started_at"], int(meeting.ended), agent, state["provider"],
                 json.dumps(state, default=str)),
            )
            self._db.commit()

    def list(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, title, started_at, ended, agent, provider, state FROM meetings ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for id_, title, started, ended, agent, provider, state in rows:
            s = json.loads(state)
            out.append({"id": id_, "title": title, "started_at": started, "ended": bool(ended), "agent": agent,
                        "provider": provider, "tasks": len(s.get("tasks", [])), "events": len(s.get("events", [])),
                        "decisions": len(s.get("decisions", []))})
        return out

    def get(self, meeting_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT state FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def search(self, text: str, limit: int = 30) -> list[dict]:
        """Meetings whose transcript, tasks or decisions mention the text."""
        like = f"%{text.lower()}%"
        with self._lock:
            ids = [r[0] for r in self._db.execute(
                "SELECT id FROM meetings WHERE lower(state) LIKE ? ORDER BY started_at DESC LIMIT ?", (like, limit))]
        return [m for m in self.list(200) if m["id"] in ids]
