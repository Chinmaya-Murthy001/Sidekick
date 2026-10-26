# Evaluation

**Method.** There are 16 scenario cases in `eval/cases.py`. Each is a short stretch of conversation, and each has a machine-checked expected outcome: the events booked (start time and attendee count), tasks (owner and due date), decisions, pending approvals, emails sent, chat messages and pause state. Every case runs in a fresh meeting with the clock fixed at Monday 2026-10-26 10:00 and the demo team's seeded calendars:

- Priya: standup 09:00–09:30 every day, Thursday review 10:00–11:30.
- Marcus: busy Tuesday 14–16, Thursday 12–13 and Thursday 15–17.
- Alex: busy Wednesday 09–12 and Friday afternoon.
- Dana (external): busy Thursday 09–10.

Run it with `python -m eval.run_eval`. The raw results are in `eval/results_rules*.json` and `.md`.

**Agent evaluated:** the offline planner. The Claude agent uses the same tools and guardrails, and the same harness runs it (`--agent claude`), but it was not run here because no API key was available at build time.

## Results

| ID | Case | Expected | Actual (v2) | Result |
|---|---|---|---|---|
| T1 | "Let's meet Thursday to finalize the launch checklist." | Thu 09:30 booked for all 4 (first common free slot) | Booked Thu 09:30, 4 people | Pass |
| T2 | "Can we meet tomorrow at 11am…" | Tue 11:00 booked | Booked Tue 11:00 | Pass |
| T3 | "Can we also sync on Tuesday at 2pm…" (Marcus busy) | No booking, alternatives posted | Posted "Tue 14:00 doesn't work… free: 09:30, 10:00, 10:30" | Pass |
| T4 | "Let's catch up Thursday afternoon…" | Thu 13:00 (afternoon window) | Booked Thu 13:00 | Pass |
| T5 | "Let's meet with Dana next Tuesday at 10am…" | Held for host approval (external guest) | Held for approval | Pass |
| T6 | "I'll send the updated pricing deck … by Wednesday." | Task → Priya, due Wed | Task → Priya, due Wed | Pass |
| T7 | "Sam, can you draft the press release by Friday?" / "Sure, I'll take that." | One task → Sam, due Fri | One task → Sam, due Fri | Pass |
| T8 | "Marcus will handle the vendor contract renewal before Friday." | Task → Marcus, due Fri | No action | **Fail** |
| T9 | "Okay, we decided to go with the annual pricing plan…" | 1 decision | 1 decision logged | Pass |
| T10 | "Hey Sidekick, email Dana the launch date is November 18th." | Held for approval, nothing sent | Held for approval | Pass |
| T11 | Pause → private remark → resume | Remark not stored, no task, resumed | Not stored, resumed | Pass |
| T12 | Same proposal said twice | Only one booking | One booking (v1: two bookings) | Pass after fix |
| T13 | Small talk incl. "I will never get used to these early mornings." | No action | Created a task for Marcus | **Fail** |
| T14 | "Let's not meet Friday, half the team is out." | No booking | No booking (v1: booked Fri 09:30) | Pass after fix |
| T15 | "Can we circle back on this Wednesday afternoon?" | Wed 13:00 booked | No action | **Fail** |
| T16 | "Sidekick, email Priya and Marcus the notes…" | Sent immediately (team only) | 1 email sent | Pass |

**Score:** 11/16 on the first run (v1), then **13/16** after fixing the two bugs it exposed (v2). The pipeline adds about 1 ms between an utterance arriving and the action completing (median under 1 ms, max 2 ms). That is the offline planner. With the Claude agent, the model's response time is added on top.

## Successful behaviours

- **Scheduling with real calendar checks.** It finds the first common free slot, respects "afternoon", and refuses to book over a conflict. Instead it posts free alternatives in the meeting chat (T1–T4).
- **Human-in-the-loop.** Invites and emails that include anyone outside `acme.io` are always held for approval (T5, T10). A unit test confirms the agent can't skip this by passing the internal approval flag.
- **Task ownership.** "Can you…?" followed by "Sure, I'll take that" goes to the person asked, not the person who accepted aloud, and is recorded once (T7).
- **Privacy.** Speech said while paused is never stored or acted on (T11).

## Failure cases and why

| Case | Root cause | Category |
|---|---|---|
| T12 (v1) double booking | The second "let's meet Thursday" saw 09:30 already taken (by our own invite) and booked the next free slot | Bug, fixed: the tool layer now treats same people + same day + same topic as a duplicate |
| T14 (v1) booked a meeting that was cancelled | Pattern matching saw "let's … meet Friday" and ignored "not" | Bug, fixed: negation check before scheduling |
| T8 third-person assignment | The planner only recognises "I'll…" and "Name, can you…?" | Limitation of pattern matching |
| T13 false positive on small talk | "I will never get used to…" matches the "I will + verb" commitment pattern | Limitation of pattern matching |
| T15 indirect phrasing | "Circle back" is not in the scheduling vocabulary | Limitation of pattern matching |

The three remaining failures are the kind of language understanding an LLM handles well. That is why the Claude agent is the primary decision-maker and the planner is only an offline fallback.

## Limitations

- The evaluation covers the offline planner only. The Claude agent has not been scored yet.
- 16 cases is a small, hand-written set in English, written by the same people who built the system.
- The calendar, email and tasks are a sandbox. Real Google API latency and errors are not measured.
- Speech recognition errors are not modelled, because the cases feed clean text.
- Speaker identity comes from the input, not from voice diarization.

## Possible improvements

1. Run the same 16 cases with `--agent claude`, then add 50+ cases taken from real meeting transcripts, with a second person labelling the expected outcomes.
2. A hybrid design: the fast planner for clear cases (instant), and Claude for anything ambiguous or that the planner skipped.
3. Ask before acting on low-confidence cases. For example: "Did you want me to book Wed 13:00?" in chat, answered by voice.
4. Connect Google Calendar and Gmail through OAuth, then measure end-to-end latency on a real Meet call through Recall.ai.
5. Track precision and recall separately (missed actions vs. wrong actions), since wrong actions cost more.
