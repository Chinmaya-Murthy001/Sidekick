"""Run the evaluation cases against an agent and write a results table.

    python -m eval.run_eval              # offline planner
    python -m eval.run_eval --agent claude   # needs ANTHROPIC_API_KEY
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from sidekick.agents import make_agent
from sidekick.demo import build_demo_meeting
from sidekick.meeting import stepped_clock
from sidekick.session import SidekickSession

from .cases import CASES

START = datetime(2026, 10, 26, 10, 0)
OUT = Path(__file__).resolve().parent


def check(case: dict, s: SidekickSession) -> tuple[bool, list[str], str]:
    m, ws, exp = s.meeting, s.meeting.workspace, case["expect"]
    problems: list[str] = []

    if "events" in exp:
        got = [(e.start.isoformat(timespec="minutes"), len(e.attendees)) for e in ws.events]
        want = [(e["start"], e["attendees"]) for e in exp["events"]]
        if got != want:
            problems.append(f"events {got} != {want}")
    if "tasks" in exp:
        got = [(t.owner, t.due.isoformat() if t.due else None) for t in ws.tasks]
        want = [(t["owner"], t.get("due")) for t in exp["tasks"]]
        if got != want:
            problems.append(f"tasks {got} != {want}")
    if "decisions" in exp and len(ws.decisions) != exp["decisions"]:
        problems.append(f"decisions {len(ws.decisions)} != {exp['decisions']}")
    if "approvals" in exp and len(m.approvals) != exp["approvals"]:
        problems.append(f"pending approvals {len(m.approvals)} != {exp['approvals']}")
    if "outbox" in exp:
        sent = [e for e in ws.outbox if not e.subject.startswith("Invitation:")]
        if len(sent) != exp["outbox"]:
            problems.append(f"emails sent {len(sent)} != {exp['outbox']}")
    if "chat_contains" in exp and not any(exp["chat_contains"] in c["text"] for c in m.chat[1:]):
        problems.append(f"no chat message containing {exp['chat_contains']!r}")
    # Any message in the meeting chat after the greeting (e.g. offering other times).
    if exp.get("chat_posted") and len(m.chat) < 2:
        problems.append("nothing posted in the meeting chat")
    if "transcript_excludes" in exp and any(exp["transcript_excludes"] in u.text for u in m.transcript):
        problems.append("paused speech was stored")
    if "paused" in exp and m.paused != exp["paused"]:
        problems.append(f"paused={m.paused}")

    actual = describe(s)
    return not problems, problems, actual


def describe(s: SidekickSession) -> str:
    m, ws = s.meeting, s.meeting.workspace
    parts = []
    for e in ws.events:
        parts.append(f"booked {e.start:%a %H:%M} ({len(e.attendees)} people)")
    for t in ws.tasks:
        parts.append(f"task -> {t.owner.split()[0]}" + (f" due {t.due:%a}" if t.due else " (no due date)"))
    if ws.decisions:
        parts.append(f"{len(ws.decisions)} decision logged")
    for a in m.approvals.values():
        parts.append(f"held for approval ({a.tool})")
    sent = [e for e in ws.outbox if not e.subject.startswith("Invitation:")]
    if sent:
        parts.append(f"{len(sent)} email sent")
    if len(m.chat) > 1 and not ws.events:
        parts.append("posted in chat")
    if any(f.title == "Recording paused" for f in m.feed):
        parts.append("paused/resumed" if not m.paused else "paused")
    return "; ".join(parts) or "no action"


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="rules")
    args = ap.parse_args()

    rows, latencies = [], []
    for case in CASES:
        meeting = build_demo_meeting(clock=stepped_clock(START, timedelta(seconds=4)), start=START)
        agent = make_agent(args.agent)
        session = SidekickSession(meeting, agent)
        session.join()
        t0 = time.perf_counter()
        for speaker, text in case["lines"]:
            session.hear(speaker, text)
        elapsed = (time.perf_counter() - t0) * 1000
        latencies += [f.latency_ms for f in meeting.feed if f.latency_ms is not None]
        ok, problems, actual = check(case, session)
        rows.append({
            "id": case["id"], "category": case["category"], "name": case["name"],
            "said": " / ".join(f"{sp.split()[0]}: {tx}" for sp, tx in case["lines"]),
            "pass": ok, "actual": actual, "problems": problems, "ms": round(elapsed, 1),
        })
        print(f"{case['id']:>4} {'PASS' if ok else 'FAIL'}  {case['name']}  ->  {actual}" + ("" if ok else f"   [{'; '.join(problems)}]"))

    passed = sum(r["pass"] for r in rows)
    print(f"\n{passed}/{len(rows)} passed with agent={agent.name}")
    if latencies:
        print(f"utterance-to-action latency: median {statistics.median(latencies)} ms, max {max(latencies)} ms")

    (OUT / f"results_{agent.name}.json").write_text(json.dumps({
        "agent": agent.name, "passed": passed, "total": len(rows),
        "latency_ms": {"median": statistics.median(latencies) if latencies else None, "max": max(latencies) if latencies else None},
        "cases": rows,
    }, indent=2))
    lines = [f"# Evaluation results ({agent.name} agent)", "",
             f"**{passed}/{len(rows)} passed.** Meeting clock fixed at Mon 2026-10-26 10:00.", "",
             "| ID | Category | Case | Actual outcome | Result |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['category']} | {r['name']} | {r['actual']} | {'Pass' if r['pass'] else 'Fail: ' + '; '.join(r['problems'])} |")
    (OUT / f"results_{agent.name}.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
