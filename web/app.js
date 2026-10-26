// Sidekick dashboard: renders the live meeting state pushed over a WebSocket.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hhmm = (iso) => (iso ? iso.slice(11, 16) : "");
const dayTime = (iso) => {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) + " · " + hhmm(iso);
};

let state = null;
let speakingTimer = null;

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// --- rendering ----------------------------------------------------------
function renderAll() {
  if (!state) return;
  $("meeting-title").textContent = state.title + " · " + state.participants.length + " people";
  const pill = $("status-pill");
  pill.className = "pill " + (state.ended ? "ended" : state.paused ? "paused" : "live");
  pill.textContent = state.ended ? "Call ended" : state.paused ? "❚❚ Paused (not recording)" : "● Live";
  $("clock").textContent = state.now ? "Meeting time " + dayTime(state.now) : "";
  renderTiles();
  renderSpeakers();
  renderTranscript();
  renderFeed();
  renderLists();
}

function renderTiles() {
  const people = state.participants.map(
    (p) => `<div class="tile" data-name="${esc(p.name)}"><div class="avatar">${esc(p.name[0])}</div><div class="name">${esc(p.name)}</div><div class="tag">${p.internal ? "team" : "external"}</div></div>`
  );
  people.push(`<div class="tile bot"><div class="avatar">S</div><div class="name">Sidekick</div><div class="tag">AI assistant</div></div>`);
  $("tiles").innerHTML = people.join("");
}

function renderSpeakers() {
  const sel = $("speaker");
  const current = sel.value;
  sel.innerHTML = state.participants.map((p) => `<option>${esc(p.name)}</option>`).join("");
  if (current) sel.value = current;
}

function renderTranscript() {
  const box = $("transcript");
  const actedQuotes = new Set((state.feed || []).map((f) => f.data && f.data.quote).filter(Boolean));
  const lines = (state.transcript || []).map((u) => {
    const acted = actedQuotes.has(`${u.speaker}: "${u.text}"`);
    return `<div class="line${acted ? " acted" : ""}"><span class="t">${hhmm(u.at)}</span><span class="who">${esc(u.speaker)}</span>${esc(u.text)}</div>`;
  });
  if (state.paused) lines.push(`<div class="line system">Recording paused. Nothing is transcribed until someone says “Sidekick, resume”.</div>`);
  box.innerHTML = lines.join("") || `<div class="line system">Waiting for someone to speak… Press “Play demo meeting”, type below, or use the mic.</div>`;
  box.scrollTop = box.scrollHeight;
}

function renderFeed() {
  const items = [...(state.feed || [])].reverse().map((f) => {
    const pending = f.kind === "approval" && f.status === "pending";
    const lat = f.latency_ms != null
      ? `<span class="lat">${f.latency_ms < 1000 ? Math.max(1, f.latency_ms) + " ms" : (f.latency_ms / 1000).toFixed(1) + " s"} after it was said</span>`
      : "";
    const badge = f.kind === "approval" ? `<span class="badge">${esc(f.status)}</span>` : "";
    const buttons = pending
      ? `<div class="actions"><button class="primary" data-approve="${esc(f.data.approval_id)}">Approve &amp; send</button><button data-reject="${esc(f.data.approval_id)}">Reject</button></div>`
      : "";
    return `<li class="${esc(f.kind)} ${esc(f.status)}">
      <div class="title">${esc(f.title)}</div>
      <div class="meta"><span>${hhmm(f.at)}</span>${badge}${lat}</div>
      ${f.detail ? `<div class="detail">${esc(f.detail)}</div>` : ""}
      ${f.data && f.data.quote ? `<div class="quote">${esc(f.data.quote)}</div>` : ""}
      ${buttons}
    </li>`;
  });
  $("feed").innerHTML = items.join("");
}

function renderLists() {
  const empty = (msg) => `<li class="empty">${msg}</li>`;
  $("events").innerHTML =
    state.events.map((e) => `<li><div class="main">${esc(e.title)}</div><div class="small">${dayTime(e.start)}–${hhmm(e.end)} · ${e.attendees.length} invited</div></li>`).join("") ||
    empty("No meetings booked yet");
  $("events-count").textContent = state.events.length ? `${state.events.length} booked` : "";
  $("tasks").innerHTML =
    state.tasks.map((t) => `<li><div class="main">${esc(t.description)}</div><div class="small">${esc(t.owner)}${t.due ? " · due " + esc(t.due) : ""}</div></li>`).join("") ||
    empty("No action items yet");
  $("tasks-count").textContent = state.tasks.length ? `${state.tasks.length} open` : "";
  $("decisions").innerHTML = state.decisions.map((d) => `<li>${esc(d)}</li>`).join("") || empty("No decisions yet");
  $("outbox").innerHTML =
    state.outbox.map((m) => `<li><div class="main">${esc(m.subject)}</div><div class="small">to ${esc(m.to.join(", "))} · ${hhmm(m.sent_at)}</div></li>`).join("") ||
    empty("Nothing sent yet");
  $("chat").innerHTML = (state.chat || []).map((c) => `<li><b>Sidekick:</b> ${esc(c.text)}</li>`).join("") || empty("Quiet");
}

function flashSpeaker(name) {
  document.querySelectorAll(".tile").forEach((t) => t.classList.toggle("speaking", t.dataset.name === name));
  clearTimeout(speakingTimer);
  speakingTimer = setTimeout(() => document.querySelectorAll(".tile").forEach((t) => t.classList.remove("speaking")), 1800);
}

// --- live updates -------------------------------------------------------
function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "reset") {
      state = msg.state;
    } else if (!state) {
      return;
    } else if (msg.type === "utterance") {
      state.transcript.push(msg.utterance);
      flashSpeaker(msg.utterance.speaker);
    } else if (msg.type === "feed") {
      state.feed.push(msg.event);
    } else if (msg.type === "feed_update") {
      const i = state.feed.findIndex((f) => f.id === msg.event.id);
      if (i >= 0) state.feed[i] = msg.event;
    } else if (msg.type === "chat") {
      state.chat.push(msg.message);
    } else if (msg.type === "state") {
      Object.assign(state, msg.state);
      if (msg.state.summary && msg.state.ended) showSummary(msg.state.summary);
    }
    renderAll();
  };
  ws.onclose = () => setTimeout(connect, 1000);
}

function showSummary(s) {
  const dlg = $("summary-dialog");
  if (dlg.open || dlg.dataset.shown === "1") return;
  dlg.dataset.shown = "1";
  const lines = s.recap
    ? [s.recap]
    : [
        `${s.meeting} · ${s.duration_minutes} min`,
        "",
        "Decisions:", ...(s.decisions.length ? s.decisions.map((d) => "  • " + d) : ["  • none"]),
        "Action items:", ...(s.tasks.length ? s.tasks.map((t) => `  • ${t.owner}: ${t.description}${t.due ? " (due " + t.due + ")" : ""}`) : ["  • none"]),
        "Meetings scheduled:", ...(s.meetings_scheduled.length ? s.meetings_scheduled.map((e) => `  • ${e.title} — ${dayTime(e.start)}`) : ["  • none"]),
        "Open questions:", ...(s.open_questions.length ? s.open_questions.map((q) => "  • " + q) : ["  • none"]),
      ];
  $("summary-text").textContent = lines.join("\n");
  dlg.showModal();
}

// --- controls -----------------------------------------------------------
$("say").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  $("text").value = "";
  await api("/api/utterance", { speaker: $("speaker").value, text });
});

$("btn-demo").addEventListener("click", async () => {
  $("btn-demo").disabled = true;
  await api("/api/demo/play", { delay: 2.5 });
});
$("btn-end").addEventListener("click", () => api("/api/end"));
$("btn-new").addEventListener("click", async () => {
  $("summary-dialog").dataset.shown = "0";
  $("btn-demo").disabled = false;
  await api("/api/meeting/new", {});
});

$("feed").addEventListener("click", async (e) => {
  const approve = e.target.dataset.approve;
  const reject = e.target.dataset.reject;
  if (approve) await api(`/api/approvals/${approve}`, { approve: true });
  if (reject) await api(`/api/approvals/${reject}`, { approve: false });
});

// Live speech-to-text from the browser microphone (Chrome / Edge).
(function setupMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = $("btn-mic");
  if (!SR) {
    btn.disabled = true;
    btn.title = "Speech recognition needs Chrome or Edge";
    return;
  }
  let rec = null;
  btn.addEventListener("click", () => {
    if (rec) { rec.stop(); return; }
    rec = new SR();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";
    rec.onresult = (ev) => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) {
          $("text").value = "";
          api("/api/utterance", { speaker: $("speaker").value, text: r[0].transcript });
        } else {
          $("text").value = r[0].transcript;
        }
      }
    };
    rec.onend = () => { rec = null; btn.classList.remove("listening"); };
    rec.start();
    btn.classList.add("listening");
  });
})();

fetch("/api/state").then((r) => r.json()).then((s) => {
  state = s;
  $("agent-pill").textContent = s.agent === "claude" ? "Agent: Claude" : "Agent: offline planner";
  renderAll();
  if (s.ended && s.summary) showSummary(s.summary);
  connect();
});
