// Sidekick dashboard: meeting setup, then the live meeting pushed over a WebSocket.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hhmm = (iso) => (iso ? iso.slice(11, 16) : "");
const dayTime = (iso) => {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) + " · " + hhmm(iso);
};

let state = null;   // the running meeting, or null
let status = null;  // /api/status
let speakingTimer = null;

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  if (!res.ok) {
    let msg = await res.text();
    try { msg = JSON.parse(msg).detail; } catch {}
    throw new Error(Array.isArray(msg) ? msg.map((m) => m.msg).join("; ") : msg);
  }
  return res.json();
}

// --- top bar and screens ------------------------------------------------
function renderShell() {
  const live = !!state;
  $("setup").hidden = live;
  $("live").hidden = !live;
  $("live-controls").hidden = !live;
  document.body.classList.toggle("is-live", live);
  const pill = $("status-pill");
  if (!live) {
    pill.className = "seg-item on";
    pill.textContent = "Ready";
  } else {
    pill.className = "seg-item on " + (state.ended ? "" : state.paused ? "paused" : "live");
    pill.textContent = state.ended ? "Ended" : state.paused ? "Paused" : "Live";
    $("btn-end").hidden = state.ended;
    const close = $("btn-close");
    if (state.ended) {
      close.className = "cream";
      close.textContent = "New meeting";
    } else if (close.className !== "circle") {
      close.className = "circle";
      close.innerHTML = '<svg viewBox="0 0 24 24"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5M15 12H3"/></svg>';
    }
  }
  if (status) {
    const agent = (live && status.meeting_agent) || status.agent;
    $("agent-pill").textContent = { claude: "Claude", openrouter: "OpenRouter" }[agent] || "Offline";
    $("google-pill").textContent = status.google.connected ? "Google" : "Local";
  }
}

// --- setup --------------------------------------------------------------
function personRow(container, p = {}, teamDefault = true) {
  const row = document.createElement("div");
  row.className = "person";
  row.innerHTML = `<input class="p-name" placeholder="Name" maxlength="80" value="${esc(p.name || "")}">
    <input class="p-email" type="email" placeholder="email@company.com" value="${esc(p.email || "")}">
    <label><input type="checkbox" class="p-team" ${p.team ?? teamDefault ? "checked" : ""}> team</label>
    <button type="button" class="remove" title="Remove">✕</button>`;
  row.querySelector(".remove").onclick = () => row.remove();
  container.appendChild(row);
}

function readPeople(container) {
  return [...container.querySelectorAll(".person")]
    .map((r) => ({ name: r.querySelector(".p-name").value.trim(), email: r.querySelector(".p-email").value.trim(), team: r.querySelector(".p-team").checked }))
    .filter((p) => p.name || p.email);
}

function rememberSetup() {
  try {
    localStorage.setItem("sidekick.setup", JSON.stringify({ title: $("m-title").value, people: readPeople($("people")), contacts: readPeople($("contacts")) }));
  } catch {}
}

function restoreSetup() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("sidekick.setup") || "null"); } catch {}
  $("people").innerHTML = "";
  $("contacts").innerHTML = "";
  if (saved && saved.people && saved.people.length) {
    $("m-title").value = saved.title || "";
    saved.people.forEach((p) => personRow($("people"), p));
    (saved.contacts || []).forEach((p) => personRow($("contacts"), p, false));
  } else {
    const me = status && status.google.email;
    personRow($("people"), me ? { name: me.split("@")[0], email: me, team: true } : {});
    personRow($("people"));
  }
}

function renderConnections() {
  if (!status) return;
  const g = status.google;
  // Short states; the setup steps live in the README and in the tooltips.
  const google = g.connected
    ? `<span class="ok" title="${esc(g.email || "")}">On</span> <button class="link" id="g-off">Disconnect</button>`
    : g.configured
      ? `<a href="/auth/google/start"><button type="button" class="small">Connect</button></a>`
      : `<span class="off" title="Add data/google_client_secret.json (see README)">Not set up</span>`;
  const agent = status.agent !== "rules"
    ? `<span class="ok">${esc(status.agent_label)}</span>`
    : `<span class="off" title="Set OPENROUTER_API_KEY (or ANTHROPIC_API_KEY) in .env">Offline</span>`;
  const bot = status.recall.ready
    ? `<span class="ok">On</span>`
    : `<span class="off" title="Set RECALL_API_KEY and PUBLIC_URL so Sidekick joins from a link">Off</span>`;
  $("connections").innerHTML = `
    <li class="conn"><span class="main">Google</span>${google}</li>
    <li class="conn"><span class="main">AI agent</span>${agent}</li>
    <li class="conn"><span class="main">Meeting bot</span>${bot}</li>`;
  const off = $("g-off");
  if (off) off.onclick = async () => { await api("/auth/google/disconnect"); await refreshStatus(); };
}

async function loadHistory(q = "") {
  const res = await fetch("/api/meetings" + (q ? "?q=" + encodeURIComponent(q) : ""));
  const items = await res.json();
  $("history").innerHTML = items.map((m) => `<li class="history-item" data-id="${esc(m.id)}">
      <div class="main">${esc(m.title)}</div>
      <div class="small">${m.started_at ? dayTime(m.started_at) : ""} · ${m.tasks} tasks</div>
    </li>`).join("") || `<li class="empty">${q ? "No matches" : "None yet"}</li>`;
}

$("history").addEventListener("click", async (e) => {
  const li = e.target.closest(".history-item");
  if (!li) return;
  const m = await (await fetch("/api/meetings/" + li.dataset.id)).json();
  showSummary(m.summary || { meeting: m.title }, m, true);
});
let searchTimer = null;
$("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadHistory(e.target.value.trim()), 250);
});

$("add-person").onclick = () => personRow($("people"));
$("add-contact").onclick = () => personRow($("contacts"), {}, false);

$("start-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("start-error");
  err.hidden = true;
  const participants = readPeople($("people"));
  const contacts = readPeople($("contacts"));
  const missing = [...participants, ...contacts].find((p) => !p.name || !p.email);
  if (!participants.length || missing) {
    err.textContent = "Give every person a name and an email.";
    err.hidden = false;
    return;
  }
  rememberSetup();
  try {
    await api("/api/meeting/start", { title: $("m-title").value.trim(), participants, contacts, meeting_url: $("m-link").value.trim() });
    $("m-link").value = "";
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

$("btn-demo").onclick = async () => {
  try { await api("/api/demo"); } catch (ex) { alert(ex.message); }
};

// --- live meeting -------------------------------------------------------
function renderLive() {
  if (!state) return;
  $("outbox-hint").textContent = state.delivers ? "Sent" : "Local";
  renderHero();
  renderCallBar();
  renderTiles();
  renderSpeakers();
  renderTranscript();
  renderFeed();
  renderLists();
}

const platformOf = (url) => (/meet\.google\.com/i.test(url) ? "Google Meet" : /zoom\.us/i.test(url) ? "Zoom" : /teams\.(microsoft|live)\.com/i.test(url) ? "Microsoft Teams" : "Call");
const BOT_TEXT = {
  joining: "Sidekick is joining. Admit “Sidekick (AI assistant)” if the call asks.",
  in_call: "Sidekick is in the call and listening.",
  failed: "Sidekick couldn’t join. Keep this page’s 🎤 on instead.",
  left: "Sidekick has left the call.",
  none: "Sidekick listens through this page: pick who’s speaking and turn on 🎤.",
};

// Big numbers and the minutes ring at the top of the live screen.
function setStat(id, value) {
  const el = $(id);
  if (el.textContent !== String(value)) {
    el.textContent = value;
    el.classList.remove("bump");
    void el.offsetWidth; // restart the animation
    el.classList.add("bump");
  }
}
function renderHero() {
  $("hero-title").textContent = state.title;
  setStat("stat-tasks", state.tasks.length);
  setStat("stat-events", state.events.length);
  setStat("stat-pending", state.pending.length);
  renderRing();
}
const clock = { server: null, local: 0 };
function renderRing() {
  if (!state || !state.started_at) return;
  // Use the meeting's own clock (the sample meeting runs on a fixed one).
  const serverNow = Date.parse(state.now);
  if (serverNow !== clock.server) { clock.server = serverNow; clock.local = Date.now(); }
  const now = state.ended ? serverNow : serverNow + (Date.now() - clock.local);
  const mins = Math.max(0, Math.floor((now - Date.parse(state.started_at)) / 60000));
  $("ring-num").textContent = mins;
  const circ = 2 * Math.PI * 42;
  $("ring-arc").style.strokeDasharray = `${Math.max(2, (Math.min(mins, 60) / 60) * circ)} ${circ}`;
}
setInterval(() => { if (state && !state.ended) renderRing(); }, 30000);

function renderCallBar() {
  const has = !!state.link;
  $("call-linked").hidden = !has;
  // Allow a new link only before the bot is in a call.
  $("call-link-form").hidden = state.ended || ["joining", "in_call"].includes(state.bot);
  $("call-link").placeholder = has ? "Use a different link" : "Paste a meeting link";
  if (has) {
    $("call-platform").textContent = platformOf(state.link);
    $("call-open").href = state.link;
  }
  $("call-open").title = BOT_TEXT[state.bot] || "";
}

$("call-link-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("call-link-error");
  err.hidden = true;
  const url = $("call-link").value.trim();
  if (!url) return;
  try {
    await api("/api/meeting/link", { meeting_url: url });
    $("call-link").value = "";
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

// Animate only things that are new since the last render, not every re-render.
const seen = { meeting: null, ids: new Set() };
function isNew(id) {
  if (seen.meeting !== state.id) { seen.meeting = state.id; seen.ids = new Set(); }
  if (seen.ids.has(id)) return false;
  seen.ids.add(id);
  return true;
}
const hue = (name) => [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");

// Stacked avatars in the call panel and name chips under the hero.
function renderTiles() {
  const speaking = new Set([...document.querySelectorAll(".av.speaking")].map((t) => t.dataset.name));
  $("tiles").innerHTML = state.participants.map(
    (p) => `<div class="av${speaking.has(p.name) ? " speaking" : ""}" data-name="${esc(p.name)}" style="--h:${hue(p.name)}" title="${esc(p.name)}">${esc(initials(p.name))}</div>`
  ).join("") + `<div class="av bot" title="Sidekick (AI assistant)">${document.querySelector(".brand svg.logo").outerHTML}</div>`;
  $("chips").innerHTML = state.participants.map(
    (p) => `<span class="chip${p.internal ? "" : " ext"}${speaking.has(p.name) ? " speaking" : ""}" data-name="${esc(p.name)}" title="${p.internal ? "Team" : "External"} · ${esc(p.email)}">${esc(p.name)}</span>`
  ).join("");
}

function renderSpeakers() {
  const sel = $("speaker");
  const current = sel.value;
  sel.innerHTML = state.participants.map((p) => `<option>${esc(p.name)}</option>`).join("");
  if (current && state.participants.some((p) => p.name === current)) sel.value = current;
}

function renderTranscript() {
  const box = $("transcript");
  const actedQuotes = new Set((state.feed || []).map((f) => f.data && f.data.quote).filter(Boolean));
  const lines = (state.transcript || []).map((u) => {
    const acted = actedQuotes.has(`${u.speaker}: "${u.text}"`);
    return `<div class="line${acted ? " acted" : ""}${isNew(u.id) ? " enter" : ""}"><span class="t">${hhmm(u.at)}</span><span class="who">${esc(u.speaker)}</span><span class="txt">${esc(u.text)}</span></div>`;
  });
  if (state.paused) lines.push(`<div class="line system">Paused. Say “Sidekick, resume”.</div>`);
  box.innerHTML = lines.join("") || `<div class="line system">Waiting for someone to speak</div>`;
  box.scrollTop = box.scrollHeight;
}

function renderFeed() {
  const items = [...(state.feed || [])].reverse().map((f) => {
    const pending = f.kind === "approval" && f.status === "pending";
    const lat = f.latency_ms != null
      ? `<span class="lat">${f.latency_ms < 1000 ? Math.max(1, f.latency_ms) + " ms" : (f.latency_ms / 1000).toFixed(1) + " s"}</span>`
      : "";
    const badge = f.kind === "approval" ? `<span class="badge">${esc(f.status)}</span>` : "";
    const id = pending ? esc(f.data.approval_id) : "";
    const buttons = pending && !state.ended
      ? `<div class="decide"><span class="ask">Send?</span><span class="toggle">
           <button data-reject="${id}" title="Reject" aria-label="Reject"><svg viewBox="0 0 24 24" data-reject="${id}"><path d="M6 6l12 12M18 6 6 18" data-reject="${id}"/></svg></button>
           <button class="yes" data-approve="${id}" title="Approve and send" aria-label="Approve and send"><svg viewBox="0 0 24 24" data-approve="${id}"><path d="m5 12 5 5L20 7" data-approve="${id}"/></svg></button>
         </span></div>`
      : "";
    const link = f.data && f.data.link
      ? ` <a href="${esc(f.data.link)}" target="_blank" rel="noopener">${/calendar\.google/.test(f.data.link) ? "Open in Calendar" : "Open call"}</a>` : "";
    return `<li class="${esc(f.kind)} ${esc(f.status)}${isNew(f.id) ? " enter" : ""}">
      <div class="title">${esc(f.title)}</div>
      <div class="meta"><span>${hhmm(f.at)}</span>${badge}${lat}</div>
      ${f.detail ? `<div class="detail">${esc(f.detail)}${link}</div>` : ""}
      ${buttons}
    </li>`;
  });
  $("feed").innerHTML = items.join("");
}

function renderLists() {
  const empty = (msg) => `<li class="empty">${msg}</li>`;
  $("events").innerHTML =
    state.events.map((e) => `<li${isNew(e.id) ? ' class="enter"' : ""}><div class="main">${e.link ? `<a href="${esc(e.link)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title)}</div><div class="small">${dayTime(e.start)}–${hhmm(e.end)} · ${e.attendees.length} invited</div></li>`).join("") ||
    empty("—");
  $("events-count").textContent = state.events.length || "";
  $("tasks").innerHTML =
    state.tasks.map((t) => `<li${isNew(t.id) ? ' class="enter"' : ""}><div class="main">${esc(t.description)}</div><div class="small">${esc(t.owner)}${t.due ? " · due " + esc(t.due) : ""}</div></li>`).join("") ||
    empty("—");
  $("tasks-count").textContent = state.tasks.length || "";
  $("decisions").innerHTML = state.decisions.map((d) => `<li>${esc(d)}</li>`).join("") || empty("—");
  $("outbox").innerHTML =
    state.outbox.map((m) => `<li${isNew(m.id) ? ' class="enter"' : ""}><div class="main">${esc(m.subject)}</div><div class="small">to ${esc(m.to.join(", "))} · ${hhmm(m.sent_at)}${m.delivered ? " · sent" : " · recorded"}</div></li>`).join("") ||
    empty("—");
  $("chat").innerHTML = (state.chat || []).map((c) => `<li><b>Sidekick:</b> ${esc(c.text)}</li>`).join("") || empty("—");
}

function flashSpeaker(name) {
  const all = () => document.querySelectorAll(".av[data-name], .chip");
  all().forEach((t) => t.classList.toggle("speaking", t.dataset.name === name));
  clearTimeout(speakingTimer);
  speakingTimer = setTimeout(() => all().forEach((t) => t.classList.remove("speaking")), 1800);
}

function showSummary(s, meeting, fromHistory = false) {
  const dlg = $("summary-dialog");
  if (!fromHistory && (dlg.open || dlg.dataset.shown === (meeting && meeting.id))) return;
  if (!fromHistory) dlg.dataset.shown = meeting && meeting.id;
  $("summary-title").textContent = fromHistory ? s.meeting || "Meeting" : "Meeting summary";
  let lines;
  if (s.recap) {
    lines = [s.recap];
  } else if (s.decisions) {
    lines = [
      `${s.meeting} · ${s.duration_minutes} min`, "",
      "Decisions:", ...(s.decisions.length ? s.decisions.map((d) => "  • " + d) : ["  • none"]),
      "Action items:", ...(s.tasks.length ? s.tasks.map((t) => `  • ${t.owner}: ${t.description}${t.due ? " (due " + t.due + ")" : ""}`) : ["  • none"]),
      "Meetings scheduled:", ...(s.meetings_scheduled.length ? s.meetings_scheduled.map((e) => `  • ${e.title} — ${dayTime(e.start)}`) : ["  • none"]),
      "Open questions:", ...(s.open_questions.length ? s.open_questions.map((q) => "  • " + q) : ["  • none"]),
    ];
  } else {
    lines = ["This meeting was not finished, so there is no summary.", "", "Transcript:",
      ...((meeting && meeting.transcript) || []).map((u) => `${hhmm(u.at)} ${u.speaker}: ${u.text}`)];
  }
  $("summary-text").textContent = lines.join("\n");
  dlg.showModal();
}

$("say").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  $("text").value = "";
  await api("/api/utterance", { speaker: $("speaker").value, text });
});

$("btn-end").addEventListener("click", () => api("/api/end"));
$("btn-close").addEventListener("click", async () => {
  if (state && !state.ended && !confirm("Leave and end this meeting? The summary will be sent.")) return;
  await api("/api/close");
});

$("feed").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-approve], button[data-reject]");
  if (!btn) return;
  const approve = btn.dataset.approve;
  const reject = btn.dataset.reject;
  try {
    if (approve) await api(`/api/approvals/${approve}`, { approve: true });
    if (reject) await api(`/api/approvals/${reject}`, { approve: false });
  } catch (ex) { alert(ex.message); }
});

// Live speech-to-text from the browser microphone (Chrome / Edge).
(function setupMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = $("btn-mic");
  if (!SR) {
    btn.disabled = true;
    btn.title = "Live transcription needs Chrome or Edge";
    return;
  }
  let rec = null;
  let wanted = false;
  const start = () => {
    rec = new SR();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = navigator.language || "en-US";
    rec.onresult = (ev) => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) {
          $("text").value = "";
          if (r[0].transcript.trim()) api("/api/utterance", { speaker: $("speaker").value, text: r[0].transcript }).catch(() => {});
        } else {
          $("text").value = r[0].transcript;
        }
      }
    };
    // Browsers stop recognition after a pause; keep listening until turned off.
    rec.onend = () => { if (wanted && state && !state.ended) start(); else { rec = null; btn.classList.remove("listening"); } };
    rec.onerror = (ev) => { if (ev.error === "not-allowed") { wanted = false; alert("Microphone access was blocked."); } };
    rec.start();
    btn.classList.add("listening");
  };
  btn.addEventListener("click", () => {
    wanted = !wanted;
    if (wanted) start(); else if (rec) rec.stop();
  });
})();

// --- spatial motion on the start page (tilt, parallax, sheen, gaze) ------
(function spatial() {
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fine = matchMedia("(pointer: fine)").matches;
  if (calm || !fine) return;
  const grid = document.querySelector(".setup-grid");
  const scene = document.querySelector(".scene");
  const gaze = $("gaze");
  const target = { x: 0, y: 0, gx: -100, gy: -100 };
  const cur = { x: 0, y: 0, gx: -100, gy: -100 };
  const onSetup = () => !$("setup").hidden;

  window.addEventListener("pointermove", (e) => {
    target.x = e.clientX / innerWidth - 0.5;
    target.y = e.clientY / innerHeight - 0.5;
    target.gx = e.clientX;
    target.gy = e.clientY;
    gaze.classList.toggle("on", onSetup());
    gaze.classList.toggle("hover", !!e.target.closest("button, a, input, select, summary, label"));
    const panel = e.target.closest("#setup .panel");
    if (panel) {
      const r = panel.getBoundingClientRect();
      panel.style.setProperty("--mx", `${e.clientX - r.left}px`);
      panel.style.setProperty("--my", `${e.clientY - r.top}px`);
    }
  });
  document.addEventListener("pointerleave", () => gaze.classList.remove("on"));

  (function frame() {
    const k = 0.08; // easing: the windows drift after the pointer, they don't snap
    cur.x += (target.x - cur.x) * k;
    cur.y += (target.y - cur.y) * k;
    cur.gx += (target.gx - cur.gx) * 0.25;
    cur.gy += (target.gy - cur.gy) * 0.25;
    if (onSetup()) {
      grid.style.setProperty("--rx", `${(-cur.y * 5).toFixed(2)}deg`);
      grid.style.setProperty("--ry", `${(cur.x * 7).toFixed(2)}deg`);
      scene.style.setProperty("--px", `${(-cur.x * 30).toFixed(1)}px`);
      scene.style.setProperty("--py", `${(-cur.y * 20).toFixed(1)}px`);
    } else {
      gaze.classList.remove("on");
      scene.style.setProperty("--px", "0px");
      scene.style.setProperty("--py", "0px");
    }
    gaze.style.transform = `translate(${cur.gx}px, ${cur.gy}px)`;
    requestAnimationFrame(frame);
  })();
})();

// --- data flow ----------------------------------------------------------
function applyMessage(msg) {
  if (msg.type === "reset") {
    const wasLive = !!state;
    state = msg.state;
    if (!state && wasLive) { restoreSetup(); loadHistory(); refreshStatus(); }
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
    if (msg.state.summary && msg.state.ended) showSummary(msg.state.summary, state);
  }
  renderShell();
  renderLive();
}

function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onmessage = (ev) => applyMessage(JSON.parse(ev.data));
  ws.onclose = () => setTimeout(connect, 1000);
}

async function refreshStatus() {
  status = await (await fetch("/api/status")).json();
  renderShell();
  renderConnections();
}

(async () => {
  await refreshStatus();
  state = status.meeting;
  restoreSetup();
  loadHistory();
  renderShell();
  renderLive();
  if (state && state.ended && state.summary) showSummary(state.summary, state);
  connect();
})();
