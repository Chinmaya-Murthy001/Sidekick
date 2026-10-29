# Sidekick

**A live AI meeting agent that listens to a video call and acts while people talk.**

Someone says *"let's meet Thursday"*: Sidekick checks everyone's calendar, books the first free slot and sends the invite before the next person speaks. Someone says *"I'll send the deck by Wednesday"*: it becomes a task with an owner and a due date. When the call ends, everyone gets a summary.

![Sidekick dashboard during a call](docs/images/dashboard-live.png)

## What it does

| During the call | Safety and control | After the call |
|---|---|---|
| Live transcript with speakers | Announces itself when it joins | Summary emailed to the team |
| "Let's meet <day/time>" → checks calendars → books and sends invites | Anything going **outside the team** waits for the host's approval | Decisions, action items (owner + due date), meetings booked |
| Exact time taken → no booking, posts free alternatives in chat | "Sidekick, stop recording" pauses it; nothing is stored until "resume" | Open questions nobody answered |
| "I'll do X by Friday" / "Sam, can you X?" → task | Live feed of every action, the words that triggered it, and how fast | |
| "We decided to…" → decision log | Refuses duplicate actions and calendar conflicts | |
| "Hey Sidekick, email Dana…" → email | | |

## How it works

![Workflow](docs/images/workflow.png)

1. **Join**: a meeting bot joins Google Meet as "Sidekick" and posts an introduction in chat. In the prototype the dashboard plays the bot's role: you can speak into the browser microphone (Chrome/Edge speech recognition), type lines as any participant, or play a scripted demo meeting. `sidekick/recall.py` connects a real Recall.ai bot when a key is provided.
2. **Listen**: each finished utterance (speaker + text) enters the pipeline in order.
3. **Safety check**: pause/resume commands are handled by fixed rules before any AI sees the text.
4. **Decide**: the agent reads the rolling transcript, participants, current time and the actions already taken, and decides whether to act.
   - **Claude agent** (`sidekick/agents/claude_agent.py`): Claude with tool calling in a think → call tool → read result → repeat loop.
   - **Offline planner** (`sidekick/agents/rule_agent.py`): pattern-based intent detection driving the same tools. It runs when no API key is set, and serves as the evaluation baseline.
5. **Act**: tools in `sidekick/tools.py` check availability, schedule meetings, send invites and emails, create tasks, log decisions and post in the meeting chat.
6. **Guardrails** (in code, not in the prompt): external recipients → held for approval; conflicts → refused; duplicates → refused; the agent cannot set the approval flag itself.
7. **Show and summarise**: every action streams to the dashboard over a WebSocket. Ending the call builds the summary and emails it to the team.

Calendar and email go through one of two backends with the same interface:

- **Google** (`sidekick/google_workspace.py`), once you connect your account: Sidekick reads real free/busy from Google Calendar, creates real events (Google emails the invitations to every guest), and sends email and the summary through Gmail.
- **Local** (`sidekick/workspace.py`), until then: everything is recorded on the page and nothing is sent. The page says so with a banner.

Every meeting (transcript, actions, tasks, summary) is saved to a local SQLite archive (`data/sidekick.db`), and you can search it from the start screen.

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python -m sidekick.server
```

Open http://localhost:8000.

1. **Start a meeting.** Give it a name, add the people on the call (name and email), and tick **team** for your teammates. Messages to anyone not ticked always wait for your approval.
   - **Got a meeting link?** Paste the Google Meet, Zoom or Teams link into **Meeting link**, or into the bar at the top of a running meeting. With Recall.ai set up (`RECALL_API_KEY` and `PUBLIC_URL`), Sidekick's bot joins the call by itself, transcribes everyone with their names, and leaves when you end the meeting. Without it, you get an **Open call** button, and Sidekick listens through this page's 🎤 while you're in the call.
2. **During the call**, pick who is speaking and click 🎤 for live transcription in Chrome or Edge, or type what was said. Sidekick books meetings, records tasks and decisions, and takes voice commands ("Hey Sidekick, email Ben the notes").
3. **Approve or reject** anything held in the feed. Click **End call & send summary** when you're done.
4. Past meetings appear on the start screen, with search across transcripts, tasks and decisions.

To see it without setting anything up, use **Play a sample meeting** at the bottom of the start screen. It plays a scripted meeting with a fictional team and a sandbox calendar.

**Full setup for all keys and sign-ins: [docs/SETUP.md](docs/SETUP.md).**

### Connect Google Calendar and Gmail (one-time)

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the **Google Calendar API** and **Gmail API**.
2. Under **APIs & Services → OAuth consent screen**, configure the app. While it's in testing, add your own Google account as a test user.
3. Under **Credentials → Create credentials → OAuth client ID**, choose **Web application** and add the redirect URI `http://localhost:8000/auth/google/callback`.
4. Download the JSON and save it as `data/google_client_secret.json`.
5. Restart the server and click **Connect Google** on the start screen.

Sidekick asks for these permissions: create calendar events, read free/busy, and send email. The sign-in token stays in `data/` on your machine. **Disconnect** removes it.

Free/busy is only visible for calendars your account can see (your own organisation, or calendars shared with you). For anyone else, Sidekick still books the slot but says in the meeting chat that it couldn't check their calendar.

### Optional configuration (copy `.env.example` to `.env`)

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Uses the Claude agent instead of the offline planner |
| `SIDEKICK_AGENT` | `auto` (default), `claude` or `rules` |
| `SIDEKICK_MODEL`, `SIDEKICK_EFFORT` | Claude model (default `claude-opus-5-5`) and effort (default `low`, for speed) |
| `GOOGLE_CLIENT_SECRET_FILE` | A different path for the Google OAuth client file |
| `RECALL_API_KEY`, `PUBLIC_URL` | Lets Sidekick send its own bot into a Meet link via Recall.ai. `PUBLIC_URL` must reach this server, for example through an ngrok tunnel |
| `SIDEKICK_DATA` | Where the archive and Google token are stored (default `data/`) |

## Tests and evaluation

```bash
python -m pytest -q          # unit tests: parsing, approvals, pause, conflicts, full demo
python -m eval.run_eval      # 16 scenario cases → eval/results_rules.md
python -m eval.run_eval --agent claude   # same cases with Claude (needs an API key)
```

Current offline-planner result: **13/16 cases pass** (11/16 before two fixes). The details and the failure analysis are in [docs/EVALUATION.md](docs/EVALUATION.md).

## Project layout

```
sidekick/
  server.py        FastAPI app: dashboard, WebSocket feed, approvals, webhooks
  session.py       join → hear → act → end pipeline, pause/resume, summary
  meeting.py       meeting state: participants, transcript, feed, approvals
  tools.py         tool schemas + guardrails (approval, conflicts, duplicates)
  workspace.py     local backend: records calendar, outbox, tasks, decisions
  google_workspace.py  Google backend: real free/busy, Calendar events, Gmail
  google_auth.py   "Connect Google" sign-in and token storage
  store.py         SQLite meeting archive and search
  timeparse.py     "next Thursday at 3pm" → datetime
  agents/          Claude agent and offline planner
  recall.py        optional Recall.ai bot + transcript webhook
  demo.py          demo team, contacts and scripted meeting
web/               dashboard (HTML/CSS/JS, no build step)
eval/              evaluation cases, runner and results
tests/             unit tests
docs/              architecture, evaluation, diagrams, screenshots
```

## Tech stack

Python 3.12 · FastAPI + WebSockets · Anthropic Claude API (tool calling) · browser Web Speech API for live speech-to-text · Recall.ai (optional meeting bot) · vanilla HTML/CSS/JS dashboard · pytest.

## Known limitations

- The Google integration is tested against a fake Google API that checks the exact requests (`tests/test_google_workspace.py`). It has not yet been run against a live Google account, because that needs your own OAuth client.
- The Recall.ai integration is written but has not been run end to end. It needs a paid key and a public webhook URL. Without it, Sidekick listens through the page's microphone while you're in the call.
- Browser speech recognition works in Chrome and Edge, and you pick who is speaking. Automatic speaker identification comes from the meeting bot.
- The evaluation numbers are for the offline planner. The Claude agent is implemented, but it hasn't been scored because no API key was available at build time.
- The offline planner misses indirect phrasing ("circle back") and third-person assignments ("Marcus will…"), and can mistake some small talk for a commitment. See the evaluation for details.
- One meeting runs at a time per server, for one host. The archive is a local SQLite file, not a shared database.
