# Full setup: keys, Google sign-in and the meeting bot

Sidekick runs without any of this, in local mode with the offline agent. Each step below switches on one real capability. Do them in any order.

| What | Where it goes | What it turns on |
|---|---|---|
| Anthropic API key | `.env` → `ANTHROPIC_API_KEY` | Claude decides what to do, instead of the offline planner |
| Google OAuth client | `data/google_client_secret.json`, then **Connect** in the app | Real free/busy, real Calendar invites, email through Gmail |
| Recall.ai key + public URL | `.env` → `RECALL_API_KEY`, `RECALL_REGION`, `PUBLIC_URL` | Sidekick joins a Meet/Zoom/Teams link by itself and transcribes everyone |

Both `.env` and `data/` are excluded by `.gitignore`, so keys and tokens never reach GitHub.

## 0. Install and run

```bash
cd C:\R-D\Sidekick
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m sidekick.server
```

Open http://localhost:8000. Restart the server after every change to `.env` or `data/`. The **Connections** card on the start screen shows what is active.

## 1. Anthropic API key (Claude agent)

1. Go to https://console.anthropic.com, then **Settings → API keys → Create key**.
2. Add billing or credits under **Plans & billing**. Without them, requests fail.
3. In `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
4. Restart. **AI agent** in Connections changes from *Offline* to *Claude*.

Optional settings: `SIDEKICK_MODEL` (default `claude-opus-5-5`) and `SIDEKICK_EFFORT` (default `low`, for speed). To check the agent against the test cases, run `python -m eval.run_eval --agent claude`.

## 2. Google Calendar + Gmail (OAuth)

In https://console.cloud.google.com:

1. **Create a project** (top bar → project picker → New project), for example "Sidekick".
2. **Enable the APIs.** Under **APIs & Services → Library**, enable **Google Calendar API** and **Gmail API**.
3. **Consent screen.** Under **APIs & Services → OAuth consent screen** (or **Google Auth Platform → Branding / Audience**):
   - User type: **External**, unless everyone uses one Google Workspace organisation, in which case use **Internal**.
   - App name **Sidekick**, plus your support email and developer email.
   - **Data access / Scopes:** add `.../auth/calendar.events`, `.../auth/calendar.freebusy`, `.../auth/gmail.send`, `openid` and `.../auth/userinfo.email`.
   - **Audience / Test users:** add every Google account that will click Connect.
4. **Client ID.** Under **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - **Authorized redirect URI:** `http://localhost:8000/auth/google/callback`. It must match exactly; if you change `PORT`, change the port here too.
5. **Download JSON**, then save it as `C:\R-D\Sidekick\data\google_client_secret.json`. Create the `data` folder if it doesn't exist. To keep the file elsewhere, set `GOOGLE_CLIENT_SECRET_FILE` in `.env` instead.
6. Restart, then go to **Connections → Google → Connect** and sign in.
   - Google warns that the app isn't verified, because it's in testing. Click **Advanced → Go to Sidekick**.
   - Approve the calendar and Gmail permissions.
7. Connections shows **Google: On** and the status pill shows **Google**. Invites and emails are now really sent.

Things to know:
- **The token lives in `data/google_token.json`.** **Disconnect** deletes it.
- **Apps in "Testing" get refresh tokens that expire after 7 days.** Click Connect again when that happens, or publish the app (Google reviews apps that send Gmail).
- **School or work accounts may block unverified apps.** If Connect is refused with an admin error, use a personal Gmail account or ask the admin to allow the app.
- **Free/busy is only visible for calendars your account can see.** For other people, Sidekick still books but says in the meeting chat that it couldn't check their calendar.

## 3. Recall.ai meeting bot (join from a link)

1. Sign up at https://www.recall.ai and create an **API key** in the dashboard. Note your **region**, which is part of your dashboard/API address (for example `us-east-1`, `us-west-2`, `eu-central-1`, `ap-northeast-1`).
2. Recall must be able to reach your server to send transcripts, so expose it with a tunnel:
   ```bash
   ngrok http 8000
   ```
   Copy the `https://….ngrok-free.app` address it prints. Free ngrok addresses change every time you run it.
3. In `.env`:
   ```
   RECALL_API_KEY=...
   RECALL_REGION=us-east-1
   PUBLIC_URL=https://your-tunnel.ngrok-free.app
   ```
4. Restart. Connections shows **Meeting bot: On**.
5. Paste a Meet, Zoom or Teams link when starting a meeting, or into the link box during one. The host may need to admit "Sidekick (AI assistant)". The bot leaves when you click **End call**.

Transcripts come from the meeting platform's own captions (`meeting_captions` in `sidekick/recall.py`). If no text arrives, turn captions on in the call. The webhook is protected by a secret token that's generated each time the server starts. To keep it fixed, set `RECALL_WEBHOOK_TOKEN`.

## Without a bot

Open the call in another tab and keep the Sidekick page open in **Chrome or Edge**. Pick who is speaking and press the 🎤 button. Allow microphone access when the browser asks.

## Checklist

- [ ] `pip install -r requirements.txt` done, server starts on http://localhost:8000
- [ ] `.env` created from `.env.example`
- [ ] `ANTHROPIC_API_KEY` set → AI agent: Claude
- [ ] Calendar + Gmail APIs enabled, consent screen with test users, Web OAuth client with the exact redirect URI
- [ ] `data/google_client_secret.json` saved → Connect → Google: On
- [ ] Optional: `RECALL_API_KEY`, `RECALL_REGION`, `PUBLIC_URL` (ngrok running) → Meeting bot: On
