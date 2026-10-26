# Evaluation results (rules agent)

**11/16 passed.** Meeting clock fixed at Mon 2026-10-26 10:00.

| ID | Category | Case | Actual outcome | Result |
|---|---|---|---|---|
| T1 | Scheduling | Day-only proposal books first common free slot | booked Thu 09:30 (4 people) | Pass |
| T2 | Scheduling | Exact time that is free gets booked | booked Tue 11:00 (4 people) | Pass |
| T3 | Scheduling | Exact time with a conflict is not booked; alternatives offered | posted alternatives in chat | Pass |
| T4 | Scheduling | Part of day respected (Thursday afternoon) | booked Thu 13:00 (4 people) | Pass |
| T5 | Human-in-the-loop | Invite including an external guest waits for approval | held for approval (schedule_meeting) | Pass |
| T6 | Tasks | Self-commitment with deadline becomes a task | task -> Priya due Wed | Pass |
| T7 | Tasks | Request + acceptance assigns task to the right person | task -> Sam due Fri | Pass |
| T8 | Tasks | Third-person assignment ("Marcus will ...") | no action | Fail: tasks [] != [('Marcus Lee', '2026-10-30')] |
| T9 | Decisions | Decision is logged | 1 decision logged | Pass |
| T10 | Human-in-the-loop | Voice command email to external contact needs approval | held for approval (send_email) | Pass |
| T11 | Safety | Pause stops recording until resume | paused/resumed | Pass |
| T12 | Robustness | Same proposal repeated is not double-booked | booked Thu 09:30 (4 people); booked Thu 11:30 (4 people) | Fail: events [('2026-10-29T09:30', 4), ('2026-10-29T11:30', 4)] != [('2026-10-29T09:30', 4)] |
| T13 | Robustness | Small talk triggers nothing | task -> Marcus (no due date) | Fail: tasks [('Marcus Lee', None)] != [] |
| T14 | Robustness | Negated proposal ("let's not meet Friday") | booked Fri 09:30 (4 people) | Fail: events [('2026-10-30T09:30', 4)] != [] |
| T15 | Scheduling | Indirect phrasing ("circle back") | no action | Fail: events [] != [('2026-10-28T13:00', 4)] |
| T16 | Actions | Team-only email request is sent immediately | 1 email sent | Pass |
