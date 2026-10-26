# Architecture and agent design

## Agent design at a glance

| Question | Answer |
|---|---|
| Architecture | Single agent with tools, behind a fixed safety layer. One agent sees the whole meeting context, which keeps actions consistent (no two agents booking the same slot). |
| Trigger | Every finished utterance. Utterances are processed one at a time, in order, under a lock. |
| Agent responsibilities | Decide whether the new utterance needs action. Pick tools, read their results, and continue until done. Explain each action in one line. |
| Tools | `check_availability`, `schedule_meeting` (event + invite email), `create_task`, `log_decision`, `send_email`, `post_to_meeting_chat` |
| Context given to the agent | Meeting title, current date/time, participants with emails and team/external status, other known contacts, actions already taken, the last 30 transcript lines, and the new utterance |
| Human-in-the-loop | (1) Any message or invite to someone outside the team waits for Approve/Reject in the feed. (2) Pause/resume by voice. (3) The host can end the call, which triggers the summary. |
| Guardrails (code, not prompt) | No booking over conflicts or in the past, duplicate actions refused, unknown people refused, the approval flag can't be set by the agent, pause handled before the agent sees text |
| Fallback | If no API key is set, the offline planner drives the same tools. If an agent step fails, the error is logged in the feed and the meeting carries on. |

## Components

```
Google Meet ──(Recall.ai bot / browser mic / typed input)──► POST /api/utterance  or  /webhooks/recall
                                                                │
                                                     SidekickSession.hear()
                                                     ├─ pause / resume rules
                                                     └─ agent.handle(meeting, tools, utterance)
                                                           ├─ ClaudeAgent  (tool-calling loop)
                                                           └─ RuleAgent    (pattern planner)
                                                                │
                                                     ToolBox.execute()  ── guardrails ──► Workspace
                                                                │                         (calendar, outbox,
                                                                ▼                          tasks, decisions)
                                                     Meeting.log() ──► WebSocket ──► dashboard feed
                                                                                    Approve / Reject ──► ToolBox.resolve_approval()
```

## Claude agent details

- Model `claude-opus-5-5`, effort `low` by default. Speed matters more here than depth on each step, and both can be changed in `.env`.
- Manual tool loop of up to 8 steps per utterance. Tool errors go back to the model as `is_error` results so it can recover. For example, after a conflict error it calls `check_availability` and posts the alternatives.
- Server-side refusal fallback is enabled (`fallbacks: "default"`). A refusal is logged in the feed rather than crashing.
- The system prompt tells the model to reply `NONE` when nothing is actionable. Actions already taken are listed in the context to avoid repeats, and the tool layer refuses duplicates anyway.

## Data handling

- Nothing said while paused is stored.
- The demo uses fictional people and a sandbox outbox. Nothing leaves the machine unless a real integration is configured.
- The bot introduces itself in the meeting chat when it joins.
