# Full setup, step by step

Sidekick works without any of this, in local mode with the offline planner. Each part below switches on one real capability.

| Part | Where it goes | What it turns on |
|---|---|---|
| 1. OpenRouter API key | `.env` → `OPENROUTER_API_KEY` | The AI agent (Claude Sonnet 5.5 by default) decides what to do and writes the summary |
| 2. Google OAuth client | `data\google_client_secret.json`, then **Connect** in the app | Real free/busy, real calendar invites, email through Gmail |
| 3. Recall.ai (optional) | `.env` → `RECALL_API_KEY`, `RECALL_REGION`, `PUBLIC_URL` | Sidekick joins a Meet, Zoom or Teams link by itself |

`.env` and `data\` are excluded by `.gitignore`: keys and tokens never go to GitHub. **Restart the server after every change.** The **Connections** card on the start screen shows what is on.

---

## Step 0: Install and run (once)

In a terminal:

```bash
cd C:\R-D\Sidekick
py -3.12 -m venv .venv   # needs Python 3.10+; plain "python" here is 3.6
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m sidekick.server
```

Open http://localhost:8000. To stop the server, press `Ctrl+C`. To start it again later, run:

```bash
cd C:\R-D\Sidekick
.venv\Scripts\activate
python -m sidekick.server
```

---

## Step 1: OpenRouter API key (the AI agent)

1. Go to https://openrouter.ai and sign in with the account your teacher's $50 credit is on.
2. Check the balance under **Settings → Credits** (https://openrouter.ai/settings/credits).
3. Open **Keys** (https://openrouter.ai/keys), click **Create key**, name it "Sidekick", and set a credit limit if you like (for example $10). Copy the key; it starts with `sk-or-`.
4. Open `C:\R-D\Sidekick\.env` in a text editor and fill in:
   ```
   OPENROUTER_API_KEY=sk-or-...
   OPENROUTER_MODEL=anthropic/claude-sonnet-5.5
   ```
5. Restart the server. Connections → **AI agent** shows **Sonnet 5.5 · OpenRouter**, and the status pill shows **OpenRouter**.

**Cost:** Claude Sonnet 5.5 is $2 per million input tokens and $10 per million output tokens. One utterance costs roughly $0.005–0.01, so $50 covers thousands of utterances. To spend less, set `OPENROUTER_MODEL=anthropic/claude-haiku-5.5` ($0.10 / $0.50 per million tokens), which is cheaper but less careful. Spending is listed under **Activity** on OpenRouter.

**Check it works:** start a meeting, type "Let's meet tomorrow at 2pm", and an action should appear in Activity. You can also run the 16 test cases against the AI with:
```bash
python -m eval.run_eval --agent openrouter
```

If it fails: the error shows in Activity, for example `OpenRouter 401` (wrong key) or `402` (no credits left).

---

## Step 2: Google Calendar + Gmail (OAuth)

Everything below is at https://console.cloud.google.com, signed in with the Google account whose calendar and Gmail Sidekick should use.

**2a. Create a project**
1. Top bar → project picker → **New project** → name it "Sidekick" → **Create**, then select it.

**2b. Turn on the two APIs**
1. Menu → **APIs & Services → Library**.
2. Search **Google Calendar API** → **Enable**.
3. Search **Gmail API** → **Enable**.

**2c. Consent screen.** This may be called **Google Auth Platform** in the menu.
1. **APIs & Services → OAuth consent screen** → **Get started**.
2. App name **Sidekick**, then your email as the support email → Next.
3. Audience: **External** → Next. Contact email: yours → Next → agree → **Create**.
4. **Data access → Add or remove scopes**, tick or paste these, then **Update → Save**:
   - `https://www.googleapis.com/auth/calendar.events`
   - `https://www.googleapis.com/auth/calendar.freebusy`
   - `https://www.googleapis.com/auth/gmail.send`
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email`
5. **Audience → Test users → Add users**: add your own Gmail and anyone else who will click Connect → **Save**.

**2d. Create the OAuth client**
1. **APIs & Services → Credentials → Create credentials → OAuth client ID** (on newer consoles: **Clients → Create client**).
2. Application type: **Web application**. Name: "Sidekick local".
3. **Authorized redirect URIs → Add URI**: `http://localhost:8000/auth/google/callback`. Type it exactly: `http`, not https, and no slash at the end.
4. **Create**, then **Download JSON**.

**2e. Give the file to Sidekick**
1. Create the folder `C:\R-D\Sidekick\data` if it doesn't exist.
2. Rename the downloaded file to `google_client_secret.json` and move it there, so the path is `C:\R-D\Sidekick\data\google_client_secret.json`.
3. Restart the server.

**2f. Connect**
1. Start screen → Connections → **Google → Connect**.
2. Choose your account. Google says "Google hasn't verified this app", because it's in testing. Click **Advanced → Go to Sidekick (unsafe)**. It's your own app.
3. Allow the calendar and Gmail permissions, and you're back in Sidekick with **Google: On**.

From now on, booked meetings appear in Google Calendar and Google emails the invitations itself. Approved emails and the end-of-call summary are sent from your Gmail.

Good to know:
- **While the app is in "Testing", the sign-in expires after 7 days.** Click **Connect** again.
- **School or work accounts (umb.edu) may block unverified apps.** If you get an admin error, use a personal Gmail account.
- **Free/busy is only visible for calendars your account can see.** For other people, Sidekick books anyway and says so in the meeting chat.
- **Disconnect** removes the saved token (`data\google_token.json`).

---

## Step 3 (optional): Recall.ai, so Sidekick joins a link by itself

Skip this if you're fine opening the call yourself and using the page's 🎤.

**Hear the whole call without Recall.ai (recommended)**
1. Open http://localhost:8000 in **Chrome or Edge**, and join your Meet, Zoom or Teams call in **another tab of the same browser**.
2. In Sidekick, start the meeting and click the **🎧 headphones button** next to the mic.
3. In the browser's share window, choose **Chrome Tab** (or **Edge Tab**), pick the meeting tab, turn on **"Share tab audio"**, then **Share**. Allow the microphone if asked.
4. The line under the input shows "Hearing the whole call…". Everyone in the call (from the tab) and you (from your mic) are cut at natural pauses, transcribed with speaker names by an audio model on your OpenRouter key, and acted on.

Notes:
- **Needs `OPENROUTER_API_KEY`.** The transcription model is `google/gemini-3.5-flash-lite` (about $0.04 per hour of audio); change it with `OPENROUTER_AUDIO_MODEL`.
- **Speaker names are guessed from the conversation** (people addressing each other by name). When it can't tell, it uses "Speaker 1", "Speaker 2". Tasks can only be assigned to names it recognises.
- **Click 🎧 again, or stop sharing, to stop.** Saying "Sidekick, stop recording" also stops anything from being transcribed until "Sidekick, resume".

**Using only the page microphone**
- Open http://localhost:8000 in **Chrome or Edge**. Other browsers, and app-embedded browsers such as the Claude desktop app's preview, block the microphone or have no live transcription.
- Start the meeting, pick who is speaking, click 🎤 and allow the microphone. The line under the input shows "Listening as …", then your words as you speak. Each finished sentence appears in the transcript and goes to the AI.
- The page only hears **your microphone**. Other people in the call are only picked up if their voices come out of your speakers, not headphones, and even then less reliably. Sentences are credited to whoever is selected in "Speaker". To capture everyone with their real names, use Recall.ai.
- You can always type what was said instead of speaking.

1. Sign up at https://www.recall.ai. In the dashboard, create an **API key**. Your **region** is in the dashboard address (for example `us-east-1`, `us-west-2`, `eu-central-1`, `ap-northeast-1`).
2. Recall has to reach your computer, so install ngrok (https://ngrok.com/download), sign in, and run this in a second terminal:
   ```bash
   ngrok http 8000
   ```
   Copy the `https://….ngrok-free.app` address it prints. Leave that terminal open.
3. In `.env`:
   ```
   RECALL_API_KEY=...
   RECALL_REGION=us-east-1
   PUBLIC_URL=https://your-address.ngrok-free.app
   ```
4. Restart. Connections → **Meeting bot: On**.
5. Paste a Meet, Zoom or Teams link in **Link** when starting, or in the link box during the meeting. The bot asks to join, so admit "Sidekick (AI assistant)". It leaves when you click **End call**.

Free ngrok addresses change every time ngrok restarts; update `PUBLIC_URL` and restart Sidekick. If no speech arrives, turn on captions in the call, because Recall reads the platform's captions.

---

## Optional: Anthropic directly instead of OpenRouter

Put `ANTHROPIC_API_KEY=sk-ant-...` in `.env`, from https://console.anthropic.com → API keys, and remove or blank `OPENROUTER_API_KEY`. If both are set, OpenRouter is used unless `SIDEKICK_AGENT=claude`.

## Checklist

- [ ] Step 0: server runs at http://localhost:8000, and `.env` exists
- [ ] Step 1: `OPENROUTER_API_KEY` set → AI agent shows *Sonnet 5.5 · OpenRouter*
- [ ] Step 2: both APIs enabled, consent screen with you as test user, Web client with the exact redirect URI
- [ ] Step 2: `data\google_client_secret.json` saved → **Connect** → *Google: On*
- [ ] Step 3 (optional): Recall key, region, ngrok running, `PUBLIC_URL` → *Meeting bot: On*
