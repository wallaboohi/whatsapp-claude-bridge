// Owner 09.11.2026: money in shekels (Hebrew UI), converted at the agent's "usd_ils" rate; English keeps dollars.
let USD_ILS = 3.1;
const CUR = () => (document.documentElement.lang || "he").startsWith("he") ? "₪" : "$";
const M = (usd, d = 2) => { const v = Number(usd) || 0; return CUR() === "₪" ? "₪" + (v * USD_ILS).toFixed(d) : "$" + v.toFixed(d); };
// Dashboard front-end: renders /data, sends actions to /action. Hebrew (default) and English.
const $ = id => document.getElementById(id);
let curPeer = null; try { const v = localStorage.getItem("wa-dash-peer"); curPeer = v === null || v === "" ? null : +v; } catch (e) {}
const base = () => curPeer === null ? "" : `/peer/${curPeer}`;
const baseOf = peer => peer === null || peer === undefined ? "" : `/peer/${peer}`;
const post = a => fetch(base() + "/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(a) }).then(r => r.json());
let curChat = ""; try { curChat = localStorage.getItem("wa-dash-chat") || ""; } catch (e) {}
let peersInfo = null;  // {self: brief of this machine, peers: [{i, name, online, brief}]}
let syncMap = {}; try { syncMap = JSON.parse(localStorage.getItem("wa-dash-sync") || "{}"); } catch (e) {}
const PURPOSE = { dev: "פיתוח הגשר והכלי", general: "כללית", "4m": "עבודת 4M", local: "מקומית (בלי ווצאפ)" };
const MODEL_HE = m => ({ auto: "אוטו" })[m] || m;

// ---- language ----
let lang = "he";
try { lang = localStorage.getItem("wa-dash-lang") || "he"; } catch (e) {}
const L = (he, en) => lang === "en" ? en : he;
const LOCALE = () => lang === "en" ? "en-GB" : "he-IL";
// static texts carry data-en="..."; their Hebrew original is remembered on first run
function translateStatic() {
  document.documentElement.lang = lang; document.documentElement.dir = lang === "en" ? "ltr" : "rtl";
  document.title = L("ווצאפ ↔ Claude", "WhatsApp ↔ Claude");
  document.querySelectorAll("[data-en]").forEach(el => { if (el._he === undefined) el._he = el.textContent; el.textContent = lang === "en" ? el.dataset.en : el._he; });
  const attr = (sel, name, key) => document.querySelectorAll(sel).forEach(el => { const k = "_he_" + name; if (el[k] === undefined) el[k] = el.getAttribute(name) || ""; el.setAttribute(name, lang === "en" ? el.dataset[key] : el[k]); });
  attr("[data-en-ph]", "placeholder", "enPh"); attr("[data-en-title]", "title", "enTitle"); attr("[data-en-aria]", "aria-label", "enAria");
  if ($("uiLangSel")) $("uiLangSel").value = lang;
  $("say").placeholder = L("המשך את השיחה עם הכלי… (Enter לשליחה, Shift+Enter לשורה חדשה)", "Continue the conversation… (Enter to send, Shift+Enter for a new line)");
}

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
// WhatsApp formatting: *bold*, _italic_, ~strike~ (applied after escaping)
const wa = s => esc(s).replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<strong>$2</strong>").replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>").replace(/(^|[\s(])~([^~\n]+)~/g, "$1<s>$2</s>");
const toDate = s => new Date(String(s).replace(" ", "T"));
const fmt = s => s ? toDate(s).toLocaleString(LOCALE(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const hhmm = t => new Date(t).toLocaleTimeString(LOCALE(), { hour: "2-digit", minute: "2-digit" });
const mins = s => s ? Math.max(0, Math.floor((Date.now() - toDate(s)) / 60000)) : null;
function durMin(m) {
  const d = Math.floor(m / 1440), h = Math.floor(m / 60) % 24, mm = Math.round(m % 60);
  if (d) return L(`${d} ימים ו-${h} שעות`, `${d} d ${h} h`); if (h) return L(`${h} שעות ו-${mm} דקות`, `${h} h ${mm} min`); return L(`${mm} דקות`, `${mm} min`);
}
const dur = s => { const m = mins(s); return m === null ? "—" : durMin(m); };
function ago(s) {
  const m = mins(s); if (m === null) return ""; if (m < 1) return L("עכשיו", "now");
  if (m < 60) return L(`לפני ${m} דק'`, `${m} min ago`); if (m < 1440) return L(`לפני ${Math.floor(m / 60)} שע'`, `${Math.floor(m / 60)} h ago`);
  return L(`לפני ${Math.floor(m / 1440)} ימים`, `${Math.floor(m / 1440)} days ago`);
}
function toast(t) { const el = $("toast"); el.textContent = t; el.classList.add("show"); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 2600); }
async function act(a, msg, peer) {
  try {
    const url = (peer === undefined ? base() : baseOf(peer)) + "/action";
    const j = await (await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(a) })).json();
    if (j.offline) { toast(L("המחשב השני לא זמין כרגע", "The other computer is not reachable")); return j; }
    if (j.error) toast(L("שגיאה: ", "Error: ") + j.error); else if (msg) toast(msg);
    await load(); if (setOpen && !setDirty) renderSettings(); return j;
  } catch (e) { toast(L("הפעולה נכשלה", "Action failed")); }
}

// ---- theme ----
const themeKey = "wa-dash-theme";
let charts = [], last = null;
function applyTheme(t) { document.documentElement.dataset.theme = t; try { localStorage.setItem(themeKey, t); } catch (e) {} if (last) drawCharts(last); }
try { const t = localStorage.getItem(themeKey); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
$("themeSel").value = document.documentElement.dataset.theme || "auto";
$("themeSel").onchange = e => applyTheme(e.target.value);
$("uiLangSel").onchange = e => { lang = e.target.value; try { localStorage.setItem("wa-dash-lang", lang); } catch (er) {} translateStatic(); if (last) { render(last); if (setOpen) renderSettings(); } };
translateStatic();

// ---- header popover (sending to others) ----
$("modeBtn").onclick = e => { e.stopPropagation(); const o = $("modePop").hidden; $("modePop").hidden = !o; $("modeBtn").setAttribute("aria-expanded", o); };
document.addEventListener("click", e => { if (!e.target.closest(".pop-wrap")) { $("modePop").hidden = true; $("modeBtn").setAttribute("aria-expanded", false); } });

// ---- controls ----
$("pauseBtn").onclick = () => act({ do: "pause", hours: 6 }, L("השליחה לאחרים הושהתה ל-6 שעות", "Sending to others paused for 6 hours"));
$("resumeBtn").onclick = () => act({ do: "resume" }, L("השליחה חודשה", "Sending resumed"));
$("backupBtn").onclick = () => act({ do: "backup" }, L("הגיבוי התחיל ברקע", "Backup started"));
document.querySelectorAll("[data-restart]").forEach(b => b.onclick = () => {
  const what = { bridge: L("הגשר", "the bridge"), agent: L("הסוכן", "the agent"), all: L("את הגשר ואת הסוכן", "the bridge and the agent") }[b.dataset.restart];
  if (confirm(L(`להפעיל מחדש את ${what}? זה לוקח כ-20 שניות.`, `Restart ${what}? It takes about 20 seconds.`))) act({ do: "restart", what: b.dataset.restart }, L(`${what} מופעל מחדש…`, `Restarting ${what}…`));
});
$("updBtn").onclick = async () => {
  $("updNote").textContent = L("בודק…", "Checking…");
  const j = await act({ do: "update_check" });
  if (!j) return;
  if (j.available) {
    $("updNote").innerHTML = L(`גרסה ${esc(j.version)} זמינה. `, `Version ${esc(j.version)} is available. `);
    const b = document.createElement("button"); b.className = "btn primary small"; b.textContent = L("עדכן עכשיו", "Update now");
    b.onclick = () => act({ do: "update_apply" }, L("מתעדכן — החלון ייסגר ויפתח מחדש", "Updating — the window will close and reopen")); $("updNote").appendChild(b);
  } else $("updNote").textContent = j.message || L("הגרסה עדכנית", "Up to date");
};
$("toolUpdBtn").onclick = async () => {
  $("toolUpdNote").textContent = L("בודק…", "Checking…");
  const j = await act({ do: "tool_update_check", force: true });
  if (!j) return;
  if (j.error && !j.latest) { $("toolUpdNote").textContent = L("לא ניתן לבדוק כרגע (אין אינטרנט?)", "Can't check right now (offline?)"); return; }
  if (!j.available) { $("toolUpdNote").textContent = L(`הכלי עדכני (${j.current})`, `Up to date (${j.current})`); return; }
  $("toolUpdNote").innerHTML = L(`גרסה ${esc(j.latest)} זמינה. `, `Version ${esc(j.latest)} is available. `);
  const b = document.createElement("button"); b.className = "btn primary small"; b.textContent = L("עדכן עכשיו", "Update now");
  b.onclick = () => { if (confirm(L("העדכון עוצר את הגשר והסוכן לדקה-שתיים, בודק את עצמו, וחוזר לגרסה הקודמת אם משהו נכשל. להמשיך?",
    "The update stops the bridge and the agent for a minute or two, tests itself, and rolls back if anything fails. Continue?")))
    act({ do: "tool_update_apply" }, L("העדכון התחיל בחלון נפרד", "Update started in a separate window")); };
  $("toolUpdNote").appendChild(b);
};

// ---- maintainer: save to git / release (shown only when config.json has "maintainer": true) ----
async function gitRefresh() {
  const j = await act({ do: "git_status" }); if (!j || j.error) return;
  $("gitInfo").textContent = L(`ענף ${j.branch} · גרסה ${j.version} · ${j.changed} קבצים לא שמורים`, `Branch ${j.branch} · version ${j.version} · ${j.changed} unsaved files`);
  if (!$("pubVer").value) $("pubVer").value = j.next;
}
$("gitCommitBtn").onclick = async () => { await act({ do: "git_commit", notes: $("pubNotes").value }, L("נשמר בגיט", "Saved to git")); gitRefresh(); };
$("releaseBtn").onclick = () => {
  const v = $("pubVer").value;
  if (confirm(L(`לשחרר את גרסה ${v} לכל המשתמשים? הבדיקות ירוצו בחלון נפרד, ואם יעברו — הגרסה תתפרסם.`, `Release version ${v} to all users? Checks run in a separate window and the release publishes if they pass.`)))
    act({ do: "release", version: v, notes: $("pubNotes").value }, L("השחרור התחיל בחלון נפרד", "Release started in a separate window"));
};
let pubShown = false;

// ---- display settings (saved on this computer) ----
const VIEW_KEY = "wa-dash-view", STEPS = [0.85, 0.92, 1, 1.1, 1.2, 1.32, 1.45];
let view = { chat: 2, ui: 2, font: "", bg: "" };
try { view = Object.assign(view, JSON.parse(localStorage.getItem(VIEW_KEY) || "{}")); } catch (e) {}
function applyView() {
  const r = document.documentElement.style;
  r.setProperty("--chat-scale", STEPS[view.chat]); r.setProperty("--ui-scale", STEPS[view.ui]);
  if (view.font) r.setProperty("--ui-font", view.font); else r.removeProperty("--ui-font");
  $("chat").dataset.bg = view.bg || "";
  $("chatFsOut").textContent = Math.round(100 * STEPS[view.chat]) + "%"; $("uiFsOut").textContent = Math.round(100 * STEPS[view.ui]) + "%";
  $("fontSel").value = view.font || ""; $("bgSel").value = view.bg || "";
  try { localStorage.setItem(VIEW_KEY, JSON.stringify(view)); } catch (e) {}
}
document.querySelectorAll("[data-step]").forEach(b => b.onclick = () => {
  const k = b.dataset.step; view[k] = Math.max(0, Math.min(STEPS.length - 1, view[k] + Number(b.dataset.d))); applyView();
});
$("fontSel").onchange = e => { view.font = e.target.value; applyView(); };
$("bgSel").onchange = e => { view.bg = e.target.value; applyView(); };
$("viewReset").onclick = () => { view = { chat: 2, ui: 2, font: "", bg: "" }; applyView(); };
applyView();

// ---- continue the conversation from here (PC chat, optionally also posted to the WhatsApp group) ----
const say = $("say");
const grow = () => { say.style.height = "auto"; say.style.height = Math.min(140, say.scrollHeight) + "px"; };
say.addEventListener("input", grow);
// ---- drafts survive a restart / reset of the dashboard (owner 10.11: a long message was lost when the app restarted).
// Saved on every keystroke (this PC's WebView2 storage), per conversation; restored when the box is empty.
const draftKey = () => "wa-dash-draft:" + (curChat || "");
const store = (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) {} };
const stored = k => { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } };
function saveDraft() { store(draftKey(), say.value); if (say.value.trim()) store("wa-dash-draft-last", say.value); }
function restoreDraft(quiet) {
  if (say.value.trim()) return false;
  const t = stored(draftKey()); if (!t) return false;
  say.value = t; grow(); if (!quiet) toast(L("שוחזרה הטיוטה שלא נשלחה", "Unsent draft restored")); return true;
}
say.addEventListener("input", saveDraft);
window.addEventListener("beforeunload", saveDraft);
setTimeout(() => restoreDraft(false), 300);
say.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); } });
let outbox = [];  // messages typed here and not yet seen in the conversation: shown at once as "sending…"
let sending = false, attached = [];
const baseName = pth => String(pth).split(/[\\/]/).pop();
function drawAttach() {
  const row = $("attachRow"); row.hidden = !attached.length;
  row.innerHTML = attached.map((f, i) => `<span class="attach"><span title="${esc(f.name)}">📎 ${esc(f.name)}</span><button type="button" data-i="${i}" aria-label="${L("הסר קובץ", "Remove file")}">✕</button></span>`).join("");
  row.querySelectorAll("button").forEach(btn => btn.onclick = () => { attached.splice(+btn.dataset.i, 1); drawAttach(); });
}
async function upload(file) {
  try {
    const j = await (await fetch(base() + "/upload?name=" + encodeURIComponent(file.name || "image.png"), { method: "POST", body: file })).json();
    if (j.ok) attached.push(j); else toast(L("העלאה נכשלה: ", "Upload failed: ") + (j.error || ""));
  } catch (e) { toast(L("העלאה נכשלה", "Upload failed")); }
  drawAttach();
}
say.addEventListener("paste", e => { const fs = [...((e.clipboardData && e.clipboardData.files) || [])]; if (fs.length) { e.preventDefault(); fs.forEach(upload); } });
$("attachBtn").onclick = () => $("fileInp").click();
$("fileInp").onchange = e => { [...e.target.files].forEach(upload); e.target.value = ""; };
const dropZone = document.querySelector(".chat-card");
["dragenter", "dragover"].forEach(ev => dropZone.addEventListener(ev, e => { if ([...(e.dataTransfer.types || [])].includes("Files")) { e.preventDefault(); dropZone.classList.add("composer-drop"); } }));
["dragleave", "drop"].forEach(ev => dropZone.addEventListener(ev, () => dropZone.classList.remove("composer-drop")));
dropZone.addEventListener("drop", e => { if (e.dataTransfer.files.length) { e.preventDefault(); [...e.dataTransfer.files].forEach(upload); } });
$("syncChk").onchange = e => { syncMap[curChat] = e.target.checked; try { localStorage.setItem("wa-dash-sync", JSON.stringify(syncMap)); } catch (er) {} sendHint(); };
$("stopBtn").onclick = () => { $("stopBtn").disabled = true; act({ do: "stop", chat: curChat }, L("עוצר את העבודה…", "Stopping…")).then(() => setTimeout(() => { $("stopBtn").disabled = false; load(); }, 2500)); };
function sendHint() {
  const local = curChat === "local", sync = $("syncChk").checked;
  $("syncLbl").hidden = local;
  $("sendHint").textContent = local ? L("שיחה מקומית — התשובות מוצגות כאן בלבד, בלי ווצאפ.", "Local chat — answers show here only, no WhatsApp.")
    : sync ? L("ההודעה והתשובה נשלחות גם לקבוצה בווצאפ.", "Message and answer are also posted to the WhatsApp group.")
    : L("נשאר כאן בלבד. אפשר להמשיך מהנייד מאותה נקודה.", "Stays here only. You can continue from the phone from this point.");
}
$("composer").onsubmit = async e => {
  e.preventDefault();
  const text = say.value.trim(); if ((!text && !attached.length) || sending) return;
  const isLocal = curChat === "local", sync = !isLocal && $("syncChk").checked;
  if (sync && attached.length > 1) { toast(L("בשליחה לווצאפ אפשר לצרף קובץ אחד", "Only one file when posting to WhatsApp")); return; }
  sending = true;
  const files = attached, shown = (text + files.map(f => "\n📎 " + baseName(f.path)).join("")).trim();
  const item = { text: shown, at: new Date().toISOString(), state: "sending", chat: curChat };
  store("wa-dash-sent-last", text); outbox.push(item); say.value = ""; store(draftKey(), ""); attached = []; drawAttach(); grow(); drawOutbox(true);
  let ok = false;
  try {
    const body = (isLocal || !sync) ? { do: "say_pc", chat: curChat, text, sync: 0, attach: files.map(f => f.path) }
                                    : { do: "say", chat: curChat, text, media_path: files[0] && files[0].path };
    const j = await post(body);
    ok = !!j && !j.error; item.state = ok ? "sent" : "failed"; item.err = j && j.error;
    if (ok && j.agent_alive === false) toast(L("הסוכן לא פעיל כרגע — ההודעה תטופל כשיחזור", "The agent is not running — it will be handled when it is back"));
  } catch (err) { item.state = "failed"; item.err = String(err); }
  sending = false;
  if (!ok) { toast(L("השליחה נכשלה — הטקסט חזר לתיבה", "Send failed — text is back in the box")); outbox = outbox.filter(x => x !== item); if (!say.value) { say.value = text; grow(); } attached = files; drawAttach(); }
  else if (sync && files.length) outbox = outbox.filter(x => x !== item);
  drawOutbox(true); setTimeout(load, 1200); setTimeout(load, 5000);
};
function drawOutbox(scroll) {
  const chat = $("chat"); chat.querySelectorAll(".bubble.outbox").forEach(x => x.remove());
  const shown = new Set((last && last.conversation || []).filter(m => !m.tool).map(m => (m.content || "").trim()));
  outbox = outbox.filter(x => !(x.state === "sent" && shown.has(x.text)) && Date.now() - Date.parse(x.at) < 5 * 60000);
  outbox.filter(x => x.chat === curChat).forEach(x => chat.insertAdjacentHTML("beforeend", `<div class="bubble me outbox ${x.state}"><span class="who">${L("אתה", "You")}</span>${esc(x.text)}<span class="meta">${x.state === "sending" ? L("⏳ שולח…", "⏳ sending…") : L("✓ נשלח", "✓ sent")}</span></div>`));
  if (scroll) chat.scrollTop = chat.scrollHeight;
}

// ---- today panel (collapsed by default; opens at the expense of the chat height) ----
let todayOpen = false;
try { todayOpen = localStorage.getItem("wa-dash-today") === "1"; } catch (e) {}
function setToday(v) {
  todayOpen = v; $("todayPanel").hidden = !v; $("todayBtn").setAttribute("aria-expanded", v);
  try { localStorage.setItem("wa-dash-today", v ? "1" : "0"); } catch (e) {}
  const c = $("chat"); c.scrollTop = c.scrollHeight;
}
$("todayBtn").onclick = () => setToday(!todayOpen);
setToday(todayOpen);

// ---- detail drawer ----
let recvFocus = false;
let drawerKind = null, sentAll = false, recvQ = "", recvChat = "";
const DRAWERS = () => ({
  drafts: L("📝 טיוטות", "📝 Drafts"), sent: L("📤 נשלחו", "📤 Sent"), received: L("📥 התקבלו", "📥 Received"),
  tr: L("🎙️ תמלולים", "🎙️ Transcripts"), cost: L("💲 עלות", "💲 Cost"),
  queue: L("📋 תור", "📋 Queue"), sched: L("🕗 מתוזמן", "🕗 Scheduled"),
});
function openDrawer(kind) {
  drawerKind = kind; $("scrim").hidden = false; $("drawer").hidden = false; renderDrawer(true); $("drawerClose").focus();
}
function closeDrawer() { drawerKind = null; $("scrim").hidden = true; $("drawer").hidden = true; }
document.querySelectorAll("[data-detail]").forEach(b => b.onclick = () => { if (setOpen && b.closest("#settings")) closeSettings(); openDrawer(b.dataset.detail); });
$("scrim").onclick = closeDrawer; $("drawerClose").onclick = closeDrawer;
document.addEventListener("keydown", e => { if (e.key === "Escape" && drawerKind) closeDrawer(); });

const waPhone = r => { const x = String(r || ""); return /^\d{8,15}$/.test(x) ? x : /^(\d{8,15})@s\.whatsapp\.net$/.test(x) ? x.split("@")[0] : null; };
const STATUS = () => ({ pending: L("ממתינה", "pending"), sending: L("נשלחת…", "sending…"), sent: L("נשלחה", "sent"), discarded: L("נמחקה", "deleted"), cancelled: L("בוטלה", "cancelled"), failed: L("נכשלה", "failed") });
const empty = t => `<p class="empty">${t}</p>`;

function draftCard(r) {
  const st = STATUS(), ph = waPhone(r.recipient), pend = r.status === "pending";
  return `<article class="item ${pend ? "pending" : r.status === "sent" ? "ok-i" : r.status === "failed" ? "bad-i" : ""}">
    <div class="item-head"><span class="avatar">${esc(String(r.name || r.recipient || "?").trim().charAt(0))}</span><div class="who-col"><strong>${esc(r.name || r.recipient)}</strong><span class="muted" style="font-size:.78rem">${fmt(r.created)} · ${ago(r.created)}${ph ? " · +" + esc(ph) : ""}</span></div><span class="pill ${esc(r.status)}">${st[r.status] || esc(r.status)}</span></div>
    <div class="item-body">${esc(r.message)}</div>
    ${pend ? `<div class="item-actions">
      <button class="btn small primary" data-approve="${r.id}">✅ ${L("אשר ושלח", "Approve & send")}</button>
      <button class="btn small danger" data-del="${r.id}">🗑 ${L("מחק", "Delete")}</button>
      ${ph ? `<a class="btn small" target="_blank" rel="noopener" href="https://wa.me/${ph}?text=${encodeURIComponent(r.message)}">↗ ${L("פתח בווצאפ", "Open in WhatsApp")}</a>` : ""}
    </div>` : ""}
  </article>`;
}

function drawerContent(kind, d) {
  if (kind === "drafts") {
    const all = d.drafts || [], pend = all.filter(x => x.status === "pending"), rest = all.filter(x => x.status !== "pending");
    return (pend.length ? `<div class="sum-row"><span class="stat warn">${L(`${pend.length} ממתינות לאישור שלך`, `${pend.length} awaiting your approval`)}</span></div>` + pend.map(draftCard).join("") : empty(L("אין טיוטות ממתינות 🎉", "No pending drafts 🎉")))
      + (rest.length ? `<h3 class="muted" style="font-size:.82rem;margin:8px 0 0">${L("היסטוריה", "History")}</h3>` + rest.map(draftCard).join("") : "");
  }
  if (kind === "sent") {
    const all = d.sent || [], others = all.filter(r => !r.own), s = sentAll ? all : others;
    const chips = `<div class="sum-row"><button class="btn small${sentAll ? "" : " primary"}" data-sent="0">${L("לאנשים אחרים", "To others")} · ${others.length}</button>
      <button class="btn small${sentAll ? " primary" : ""}" data-sent="1">${L("הכול (כולל תשובות בקבוצה)", "All (incl. group replies)")} · ${all.length}</button></div>`;
    return chips + (s.length ? s.map(r => `<article class="item ${r.ok ? "ok-i" : "bad-i"}"><div class="item-head"><span class="avatar">${esc(String(r.recipient || "?").trim().charAt(0))}</span><div class="who-col"><strong>${esc(r.recipient)}</strong><span class="muted" style="font-size:.78rem">${fmt(r.timestamp)} · ${ago(r.timestamp)}</span></div>
      <span class="pill ${r.ok ? "sent" : "failed"}">${r.ok ? L("נשלחה", "sent") : L("נכשלה", "failed")}</span></div>
      ${r.text ? `<div class="item-body">${esc(r.text)}</div>` : ""}${r.ok ? "" : `<span class="bad" style="font-size:.8rem">${esc(String(r.detail).slice(0, 160))}</span>`}</article>`).join("")
      : empty(sentAll ? L("אין שליחות", "Nothing sent") : L("לא נשלחה אף הודעה לאנשים אחרים — הכול טיוטות 👍", "Nothing sent to others — drafts only 👍")));
  }
  if (kind === "received") {
    const MT = { audio: L("🎙️ הודעה קולית", "🎙️ voice note"), image: L("🖼️ תמונה", "🖼️ image"), document: L("📎 קובץ", "📎 file"), video: L("🎬 וידאו", "🎬 video") };
    const all = d.received || [], q = recvQ.trim().toLowerCase();
    const txt = r => r.content || (r.media_type ? MT[r.media_type] || r.media_type : "");
    const hit = r => !q || [r.sender, r.chat, txt(r)].some(x => String(x || "").toLowerCase().includes(q));
    const chats = {}; all.forEach(r => { const k = r.chat || "?"; (chats[k] = chats[k] || { n: 0, group: r.group, last: r.timestamp, items: [] }).n++; if (hit(r) && (!recvChat || recvChat === k)) chats[k].items.push(r); });
    const order = Object.entries(chats).sort((x, y) => String(y[1].last).localeCompare(String(x[1].last)));
    const chips = `<div class="sum-row"><button class="btn small${recvChat ? "" : " primary"}" data-chat="">${L("כל השיחות", "All chats")} · ${all.length}</button>` +
      order.slice(0, 12).map(([k, v]) => `<button class="btn small${recvChat === k ? " primary" : ""}" data-chat="${esc(k)}">${v.group ? "👥 " : ""}${esc(k)} · ${v.n}</button>`).join("") + `</div>`;
    const search = `<input id="recvSearch" class="search" type="search" value="${esc(recvQ)}" placeholder="${L("חיפוש לפי שם, שיחה או מילה…", "Search name, chat or word…")}" aria-label="${L("חיפוש בהודעות שהתקבלו", "Search received")}">`;
    const groups = order.filter(([, v]) => v.items.length).map(([k, v]) => `<details class="item" open><summary class="item-head"><span class="avatar">${v.group ? "👥" : esc(String(k).charAt(0))}</span><div class="who-col"><strong>${esc(k)}</strong><span class="muted" style="font-size:.78rem">${v.items.length} ${L("הודעות", "messages")} · ${L("אחרונה", "last")} ${hhmm(toDate(v.last))}</span></div></summary>
      ${v.items[0] && v.items[0].chat_jid ? `<div class="row tight"><button type="button" class="btn small" data-hist="${esc(v.items[0].chat_jid)}" title="${L("מבקש מהטלפון הודעות ישנות של השיחה הזו", "Ask the phone for older messages of this chat")}">📥 ${L("השלם היסטוריה", "Complete history")}</button></div>` : ""}
      ${v.items.map(r => `<div class="msg-row${v.group ? "" : " solo"}"><span class="muted">${hhmm(toDate(r.timestamp))}</span>${v.group ? `<strong>${esc(r.sender)}</strong>` : ""}<span>${esc(txt(r))}</span></div>`).join("")}</details>`).join("");
    return search + chips + (groups || empty(all.length ? L("אין תוצאות לחיפוש", "No matches") : L("לא התקבלו הודעות היום", "No messages received today")));
  }
  if (kind === "queue") return queueContent(d);
  if (kind === "sched") return schedContent(d);
  if (kind === "tr") {
    const t = d.transcripts || [], gap = trGapHtml(d);
    return (gap ? `<div class="item">${gap}</div>` : "") + (t.length ? t.map(r => `<article class="item"><div class="item-head"><strong>${esc(r.sender)}</strong><span class="muted" style="font-size:.78rem">${fmt(r.ts)}</span></div><div class="item-body">${esc(r.text)}</div></article>`).join("") : empty(L("אין תמלולים", "No transcripts")));
  }
  if (kind === "cost") {
    const a = d.agent || {}, c = d.costs || [], runs = d.runs_today || [], max = Math.max(0.01, ...c.map(x => x.cost));
    const ms = [{ ...(d.cost_self || {}), here: true }].concat(((peersInfo && peersInfo.peers) || []).map(p => p.brief && p.brief.cost ? { ...p.brief.cost, machine: p.brief.cost.machine || p.name } : { machine: p.name, off: true }));
    const machBlock = `<div class="item"><div class="item-head"><strong>${L("לפי מחשב / חשבון Claude", "By computer / Claude account")}</strong></div>
      <div class="bar-row" style="grid-template-columns:1fr 70px 70px 70px"><span class="muted">${L("מחשב · חשבון", "Computer · account")}</span><span class="muted">${L("היום", "Today")}</span><span class="muted">7 ${L("ימים", "days")}</span><span class="muted">30 ${L("ימים", "days")}</span></div>
      ${ms.map(m => `<div class="bar-row" style="grid-template-columns:1fr 70px 70px 70px"><span>🖥️ ${esc(m.machine || "?")}${m.account ? ` · <span class="muted">${esc(m.account)}</span>` : ""}${m.here ? ` <span class="muted">(${L("כאן", "here")})</span>` : ""}</span>${m.off ? `<span class="muted" style="grid-column:span 3">${L("לא זמין כרגע", "not reachable")}</span>` : `<span>${M(m.today, 2)}</span><span>${M(m.d7, 2)}</span><span>${M(m.d30, 2)}</span>`}</div>`).join("")}
      <p class="note tight">${L("שם המחשב והחשבון: הגדרות ← זהות המחשב", "Names: Settings → This computer")}</p></div>`;
    const cc = d.cost_by_chat || [], cmax = Math.max(0.01, ...cc.map(x => x.d7 || 0));
    const chatBlock = cc.length ? `<div class="item"><div class="item-head"><strong>${L("לפי שיחה (7 ימים)", "By conversation (7 days)")}</strong></div>${cc.map(r => `<div class="bar-row" style="grid-template-columns:120px 1fr 150px"><span>${esc(r.name)}</span><span class="meter"><span style="width:${100 * (r.d7 || 0) / cmax}%"></span></span><span>${M(r.d7, 2)} · ${L("היום", "today")} ${M(r.today, 2)}</span></div>`).join("")}</div>` : "";
    const byModel = {}; runs.forEach(r => { const m = r.model || "?"; byModel[m] = byModel[m] || { n: 0, cost: 0 }; byModel[m].n++; byModel[m].cost += r.cost || 0; });
    const mmax = Math.max(0.01, ...Object.values(byModel).map(x => x.cost)), pct = Math.min(100, 100 * (a.spent_today || 0) / (a.budget || 1));
    return `<div class="sum-row"><span class="stat">${L("היום", "Today")}: ${M(a.spent_today, 2)}</span><span class="stat">${L("תקרת שימוש", "Cap")}: ${M(a.budget, 0)}</span><span class="stat">${L("ריצות היום", "Runs today")}: ${runs.length}</span></div>
      <div class="item"><div class="item-head"><strong>${L("ניצול תקרת השימוש היומית", "Daily cap used")}</strong><span class="muted">${Math.round(pct)}%</span></div><span class="meter${pct > 80 ? " hot" : ""}"><span style="width:${pct}%"></span></span></div>
      ${machBlock}${chatBlock}
      ${Object.keys(byModel).length ? `<div class="item"><div class="item-head"><strong>${L("היום לפי מודל", "Today by model")}</strong></div>${Object.entries(byModel).sort((x, y) => y[1].cost - x[1].cost).map(([m, v]) => `<div class="bar-row" style="grid-template-columns:90px 1fr 120px"><span>${m === "?" ? L("לא נרשם", "not recorded") : esc(m)}</span><span class="meter"><span style="width:${100 * v.cost / mmax}%"></span></span><span>${M(v.cost, 2)} · ${v.n} ${L("ריצות", "runs")}</span></div>`).join("")}</div>` : ""}
      <div class="item"><div class="item-head"><strong>${L("לפי שיחה — היום", "By conversation — today")}</strong></div>${(d.groups || []).map(g => `<div class="conv-row"><span>${g.jid === "local" ? "💻" : "👥"} ${esc(g.name)} · <span class="muted">${esc(MODEL_HE(g.model))}</span></span><span>${M(g.spent)} / ${M(g.budget, 0)}</span><span class="meter${g.spent >= g.budget ? " hot" : ""}"><span style="width:${Math.min(100, 100 * g.spent / (g.budget || 1))}%"></span></span></div>`).join("")}<p class="note tight">${L("תקרת שימוש לכל שיחה בנפרד — לשינוי: ⚙️ הגדרות ← עלות", "Each conversation has its own cap — change it in ⚙️ Settings → Cost")}</p></div>
      <div class="item"><div class="item-head"><strong>${L("לפי יום", "By day")}</strong></div>${c.slice().reverse().map(r => `<div class="bar-row"><span class="muted">${r.day.slice(8, 10)}.${r.day.slice(5, 7)}</span><span class="meter"><span style="width:${100 * r.cost / max}%"></span></span><span>${M(r.cost)}</span></div>`).join("")}</div>
      <div class="item"><div class="item-head"><strong>${L("ריצות Claude היום", "Claude runs today")}</strong></div>${runs.length ? runs.map(r => `<div class="bar-row" style="grid-template-columns:52px 70px 1fr 70px"><span class="muted">${hhmm(toDate(r.at))}</span><span class="muted">${esc(r.model || "")}</span><span class="${r.ok ? "" : "bad"}">${r.ok ? "" : "✖ "}${Math.round(r.seconds)} ${L("שנ'", "s")}</span><span>${M(r.cost, 3)}</span></div>`).join("") : `<span class="muted">—</span>`}</div>`;
  }
  return "";
}
function renderDrawer(reset) {
  if (!drawerKind || !last || drawerKind === "ctx") return;
  const names = DRAWERS(), body = $("drawerBody"), top = body.scrollTop;
  $("drawerTitle").textContent = names[drawerKind];
  $("drawerTabs").innerHTML = Object.entries(names).map(([k, n]) => `<button role="tab" aria-selected="${k === drawerKind}" data-k="${k}">${n}</button>`).join("");
  $("drawerTabs").querySelectorAll("button").forEach(b => b.onclick = () => { drawerKind = b.dataset.k; renderDrawer(true); });
  body.innerHTML = drawerContent(drawerKind, last);
  body.querySelectorAll("[data-chat]").forEach(b => b.onclick = () => { recvChat = b.dataset.chat; renderDrawer(true); });
  const rs = $("recvSearch"); if (rs) { if (recvFocus) { rs.focus(); rs.setSelectionRange(rs.value.length, rs.value.length); }
    rs.oninput = () => { recvQ = rs.value; recvFocus = true; renderDrawer(false); }; rs.onblur = () => { recvFocus = false; }; }
  body.querySelectorAll("[data-sent]").forEach(b => b.onclick = () => { sentAll = b.dataset.sent === "1"; renderDrawer(true); });
  body.scrollTop = reset ? 0 : top;
  body.querySelectorAll("[data-approve]").forEach(b => b.onclick = () => {
    const r = (last.drafts || []).find(x => x.id == b.dataset.approve); if (!r) return;
    if (!confirm(L(`לשלוח עכשיו הודעה אחת אל ${r.name || r.recipient}?\n\n${r.message}`, `Send exactly one message to ${r.name || r.recipient} now?\n\n${r.message}`))) return;
    b.disabled = true; act({ do: "draft_decide", id: r.id, approve: true }, L("נשלח (אישור אחד = הודעה אחת)", "Sent (one approval = one message)")).then(() => setTimeout(load, 1500));
  });
  body.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { b.disabled = true; act({ do: "draft_decide", id: +b.dataset.del, approve: false }, L("הטיוטה נמחקה", "Draft deleted")); });
  wireDrawer(body);
}

// ---- connection strip: exact segments, hover tooltips, summary ----
function timeline(d) {
  const el = $("timeline"), now = Date.now(), span = 24 * 3600000, start = now - span;
  const ev = (d.conn_log || []).map(e => ({ t: toDate(e.at).getTime(), on: e.event === "connected" })).sort((a, b) => a.t - b.t);
  let state = null, cur = start; const segs = [];
  const push = (to, st) => { if (to > cur) { const l = segs[segs.length - 1]; if (l && l.st === st) l.e = to; else segs.push({ s: cur, e: to, st }); cur = to; } };
  for (const e of ev) { if (e.t <= start) { state = e.on; continue; } push(e.t, state); state = e.on; }
  if (d.bridge && d.bridge.connected && state === false) state = true; // the log lags behind the live status
  push(now, state);
  const label = st => st === true ? L("מחובר", "Connected") : st === false ? L("מנותק", "Disconnected") : L("אין נתונים", "No data");
  el.innerHTML = segs.map((g, i) => `<span class="seg ${g.st === true ? "on" : g.st === false ? "off" : "na"}" data-i="${i}" style="width:${100 * (g.e - g.s) / span}%"></span>`).join("");
  const tot = {}, cnt = {}; for (const g of segs) { tot[g.st] = (tot[g.st] || 0) + g.e - g.s; cnt[g.st] = (cnt[g.st] || 0) + 1; }
  const tip = $("tlTip");
  el.onmousemove = e => {
    const s = e.target.closest(".seg"); if (!s) { tip.hidden = true; return; }
    const g = segs[+s.dataset.i], m = (g.e - g.s) / 60000;
    tip.innerHTML = `<b>${g.st === true ? "🟢 " : g.st === false ? "🔴 " : "⚪ "}${label(g.st)}</b>${hhmm(g.s)} – ${g.e >= now - 1000 ? L("עכשיו", "now") : hhmm(g.e)}<br>${L("משך", "Duration")}: ${durMin(m)}<hr>${L("סה״כ ב-24 שעות", "24 h total")}: ${durMin(tot[g.st] / 60000)} · ${cnt[g.st]} ${L("מקטעים", "segments")}`;
    tip.hidden = false;
    const w = tip.offsetWidth; let x = e.clientX - w / 2; x = Math.max(8, Math.min(innerWidth - w - 8, x));
    tip.style.left = x + "px"; tip.style.top = (el.getBoundingClientRect().bottom + 8) + "px";
  };
  el.onmouseleave = () => tip.hidden = true;
  // axis: a tick every 4 hours on the clock
  let ax = ""; const first = new Date(start); first.setMinutes(0, 0, 0); first.setHours(first.getHours() + 1);
  for (let t = first.getTime(); t < now; t += 3600000) if (new Date(t).getHours() % 2 === 0) ax += `<span style="left:${100 * (t - start) / span}%">${String(new Date(t).getHours()).padStart(2, "0")}:00</span>`;
  $("tlAxis").innerHTML = ax;
  // summary
  let on = 0, known = 0, off = 0, longest = 0;
  for (const g of segs) if (g.st !== null) { known += g.e - g.s; if (g.st) on += g.e - g.s; else { off++; longest = Math.max(longest, g.e - g.s); } }
  $("connStats").innerHTML = known ? `<span class="stat ok" title="${L("זמינות ב-24 השעות האחרונות", "Availability, last 24 h")}">${Math.round(100 * on / known)}% ${L("זמינות", "uptime")}</span>` +
    `<span class="stat ${off ? "bad" : "ok"}">${off ? L(`${off} ניתוקים`, `${off} outages`) : L("ללא ניתוקים", "no outages")}${off ? ` · ${L("הארוך", "longest")} ${durMin(longest / 60000)}` : ""}</span>` : "";
  el.setAttribute("aria-label", known ? L(`זמינות ${Math.round(100 * on / known)}% ב-24 השעות האחרונות`, `${Math.round(100 * on / known)}% availability in the last 24 hours`) : L("אין עדיין נתוני חיבור", "No connection data yet"));
}

function drawCharts(d) {
  const css = getComputedStyle(document.documentElement), muted = css.getPropertyValue("--muted").trim(), line = css.getPropertyValue("--line").trim(), brand = css.getPropertyValue("--brand").trim();
  Chart.defaults.font.family = "Heebo, Segoe UI, sans-serif"; Chart.defaults.color = muted;
  const hours = [...Array(24).keys()].map(i => (new Date().getHours() - 23 + i + 24) % 24);
  const byH = Object.fromEntries((d.hourly || []).map(r => [parseInt(r.h, 10), r.n]));
  const base = { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } },
    scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } }, y: { beginAtZero: true, grid: { color: line }, ticks: { precision: 0 } } } };
  charts.forEach(c => c && c.destroy());
  charts = [
    new Chart($("cHourly"), { type: "bar", data: { labels: hours.map(h => String(h).padStart(2, "0") + ":00"), datasets: [{ data: hours.map(h => byH[h] || 0), backgroundColor: brand, borderRadius: 4, label: L("הודעות", "messages") }] }, options: base }),
    new Chart($("cCost"), { type: "bar", data: { labels: (d.costs || []).map(r => r.day.slice(8, 10) + "." + r.day.slice(5, 7)), datasets: [{ data: (d.costs || []).map(r => r.cost), backgroundColor: "#5b8def", borderRadius: 4, label: CUR() }] },
      options: { ...base, scales: { ...base.scales, y: { ...base.scales.y, ticks: { callback: v => M(v, 0) } } } } }),
  ];
}

function render(d) {
  last = d;
  if (d.maintainer && !pubShown) { pubShown = true; $("pubBox").hidden = false; gitRefresh(); }
  $("verChip").textContent = "v" + d.version; $("verChip").title = L(`לוח בקרה ${d.version} · כלי ${d.tool_version || "?"}`, `Dashboard ${d.version} · tool ${d.tool_version || "?"}`);
  $("toolVer").textContent = d.tool_version || "—";
  $("updated").textContent = L("עודכן ", "Updated ") + new Date().toLocaleTimeString(LOCALE(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const b = d.bridge || {}, p = d.policy || {}, a = d.agent || {};
  if (a.usd_ils) USD_ILS = a.usd_ils;
  // connection strip
  const up = !b.error && b.connected;
  $("connDot").className = "pulse " + (up ? "ok" : "bad");
  $("hero").dataset.state = up ? "ok" : "bad";
  $("connText").textContent = b.error ? L("הגשר לא פועל", "Bridge is not running") : up ? L(`מחובר ${dur(b.connected_since)}`, `Connected for ${dur(b.connected_since)}`) : L("מנותק", "Disconnected");
  $("connSub").textContent = b.error ? L("הנתונים מוצגים מהמחשב. נסה \"הפעל מחדש גשר\".", "Showing local data. Try \"Restart bridge\".") :
    L("מאז ", "Since ") + fmt(b.connected_since) + (b.disconnects ? " · " + L(`אחרון נותק ${fmt(b.last_disconnect)}`, `last disconnect ${fmt(b.last_disconnect)}`) : "");
  timeline(d);
  // header status chips
  const hb = mins(a.heartbeat), chip = (ok, t, tip) => `<span class="schip ${ok ? "ok" : "bad"}" title="${esc(tip || "")}"><span class="dot"></span>${t}</span>`;
  const allOk = !b.error && up && hb !== null && hb < 3;
  $("sysChips").innerHTML = `<span class="schip big ${allOk ? "ok" : "bad"}"><span class="dot"></span>${allOk ? L("המערכת תקינה", "System healthy") : L("דורש תשומת לב", "Needs attention")}</span>` + chip(!b.error, L("גשר", "Bridge"), b.error ? "" : L("פועל מאז ", "running since ") + fmt(b.started)) +
    chip(hb !== null && hb < 3, L("סוכן", "Agent"), ago(a.heartbeat)) +
    chip(mins(a.events_connected) !== null, L("עדכון חי", "Live"), "") +
    `<span class="schip" title="${L("גיבוי אחרון", "Last backup")}">💾 ${a.backup_done ? ago(a.backup_done) : "—"}</span>`;
  // today tiles
  const pend = (d.drafts || []).filter(x => x.status === "pending").length;
  $("kSent").textContent = b.sent_today ?? "—"; $("kSentFoot").textContent = b.daily_cap ? L(`מתוך תקרת שימוש של ${b.daily_cap}`, `of a ${b.daily_cap} cap`) : "";
  $("kRecv").textContent = d.received_today ?? 0; $("kDrafts").textContent = pend; $("kTr").textContent = d.transcribed_today ?? 0;
  $("kCost").textContent = M(a.spent_today ?? 0);
  $("todaySum").textContent = `📝 ${pend} · 📤 ${b.sent_today ?? 0} · 📥 ${d.received_today ?? 0}`;
  const pct = Math.min(100, 100 * (a.spent_today || 0) / (a.budget || 5));
  $("kCostBar").style.width = pct + "%"; $("kCostMeter").className = "meter" + (pct >= 100 ? " bad" : pct >= 80 ? " warn" : "");
  $("kCostMeter").setAttribute("aria-valuenow", Math.round(pct)); $("kCostMeter").title = L(`תקרת שימוש יומית ${M(a.budget, 0)}`, `daily cap ${M(a.budget, 0)}`);
  // chat
  const chat = $("chat"); const atBottom = chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 40;
  const MT = { audio: L("הודעה קולית", "voice note"), image: L("תמונה", "image"), document: L("קובץ", "file"), video: L("וידאו", "video") };
  renderTabs(d);
  const isStatus = m => m.tool && /^_?(⏳|⌛|✅ (🖥️ [^·]+ · )?סיימתי)/.test(m.content || "");
  const conv = d.conversation || [], lastStatus = conv.map(isStatus).lastIndexOf(true);
  const lv = d.live || null, steps = (lv && lv.steps) || [];
  const statusBody = () => {
    const head = lv.done ? L(`✅ סיימתי · ${Math.floor(lv.secs / 60)}:${String(lv.secs % 60).padStart(2, "0")} · ${esc(lv.model)} · ${steps.length} שלבים`, `✅ done · ${esc(lv.model)}`)
                         : L(`⏳ עובד · ${esc(lv.model)}`, `⏳ working · ${esc(lv.model)}`);
    return `<span class="st-head">${head}</span><ol>${steps.map((x, j) => `<li class="${j === steps.length - 1 && !lv.done ? "now" : ""}" title="${esc(x.text)}">${hhmm(toDate(x.at))} · ${esc(x.text)}</li>`).join("")}</ol>`;
  };
  const statusHtml = (m, i) => (i !== lastStatus || !steps.length)
    ? `<div class="bubble tool status old"><span class="st-head">${wa(m.content)}</span><span class="meta">${fmt(m.timestamp)}</span></div>`
    : `<div class="bubble tool status">${statusBody()}<span class="meta">${fmt(m.timestamp)}</span></div>`;
  const bubbleHtml = m => {
    const txt = m.content || (m.media_type ? "[" + (MT[m.media_type] || m.media_type) + "]" : "");
    const long = (txt.length > 700 || txt.split("\n").length > 14) && !expanded.has(String(m.id));
    const files = (m.files || []).map(f => `<a href="${base()}/lf/${encodeURIComponent(f)}" target="_blank" rel="noopener">📎 ${esc(f.replace(/^\d{8}_\d{6}_/, ""))}</a>`).join("");
    const canFold = txt.length > 700 || txt.split("\n").length > 14;
    return `<div class="bubble ${m.tool ? "tool" : "me"}" data-k="${esc(m.id)}"><span class="who">${m.tool ? L("🤖 הכלי", "🤖 Tool") : L("אתה", "You")}${m.src === "pc" ? `<span class="pc-tag" title="${L("נכתב בלוח הבקרה במחשב", "typed in the dashboard on the PC")}">💻</span>` : ""}</span>` +
      `<div class="bt${long ? " fold" : ""}">${wa(txt)}</div>${canFold ? `<button class="more" type="button">${long ? L("הצג עוד ▾", "Show more ▾") : L("הצג פחות ▴", "Show less ▴")}</button>` : ""}` +
      `${files ? `<div class="files">${files}</div>` : ""}<span class="meta">${fmt(m.timestamp)}</span></div>`;
  };
  let html = conv.map((m, i) => isStatus(m) ? statusHtml(m, i) : bubbleHtml(m)).join("");
  // a PC-only run has no WhatsApp status message: show its steps as a bubble at the end of the conversation
  const lastMe = conv.filter(m => !m.tool).map(m => String(m.timestamp).slice(0, 19)).sort().pop() || "";
  if (lv && steps.length && lastStatus < 0 && (!lv.done || String(lv.started).replace("T", " ") >= lastMe))
    html += `<div class="bubble tool status">${statusBody()}</div>`;
  chat.innerHTML = html || `<p class="empty">${L("עדיין אין שיחה", "No conversation yet")}</p>`;
  drawOutbox(false);
  if (atBottom || !chat._init) { chat.scrollTop = chat.scrollHeight; chat._init = true; }
  const pc = $("pendingChip"); pc.hidden = !a.pending; pc.textContent = a.pending ? L(`${a.pending} ממתינות לתשובה`, `${a.pending} awaiting an answer`) : "";
  // send mode: drafts only, no autonomous sending
  $("modeBadge").textContent = (b.paused ? "⛔ " + L("מושהה", "Paused") : "📝 " + L("טיוטות בלבד", "Drafts only")) + (pend ? " · " + pend : "");
  $("modeNote").textContent = L("הכלי לא שולח לאף אחד חוץ ממך. כל שליחה = טיוטה שאתה מאשר.", "The tool sends to no one but you. Every send is a draft you approve.");
  $("pauseBox").textContent = b.paused ? L(`⛔ שליחה לאחרים מושהית עד ${fmt(p.paused_until)} — `, `⛔ Sending to others is paused until ${fmt(p.paused_until)} — `) + (p.pause_reason || "") : "";
  // system
  $("sys").innerHTML = `
    <dt>${L("הסשן הנוכחי", "Current session")}</dt><dd>${(d.model && d.model.session_runs) || 0} ${L("הודעות", "messages")}</dd>
    <dt>${L("גיבוי", "Backup")}</dt><dd>${fmt(a.backup_done)}</dd>
    <dt>${L("תוספים", "Plugins")}</dt><dd class="muted">${esc((a.plugins || []).join(", ") || L("אין", "none"))}</dd>`;
  // model picker
  const mi = d.model || {}, guide = mi.guide || {};
  $("modelNow").innerHTML = `${L("פעיל:", "Active:")} <strong>${esc((guide[mi.current] || {}).he || mi.current || "אוטו")}</strong>` +
    (mi.until ? ` ${L("עד", "until")} ${fmt(mi.until)}` : "") + (mi.last ? ` · ${L("אחרון:", "last:")} ${esc(mi.last)}` : "");
  const tipOf = k => { const g = guide[k] || {}; return `${g.tip || ""}\n${L("עלות: ", "Cost: ")}${g.cost || ""}\n${L("בווצאפ: ", "WhatsApp: ")}${g.cmd || ""}`; };
  const sel = $("modelSel");
  if (!sel.dataset.busy) {
    sel.innerHTML = Object.entries(guide).map(([k, g]) => `<option value="${esc(k)}" title="${esc(tipOf(k))}"${(mi.current || "auto") === k ? " selected" : ""}>${esc(g.he)}</option>`).join("");
    sel.title = tipOf(sel.value); $("modelTip").textContent = (guide[sel.value] || {}).tip || "";
  }
  sel.onfocus = () => sel.dataset.busy = "1"; sel.onblur = () => delete sel.dataset.busy;
  sel.onchange = () => { $("modelTip").textContent = (guide[sel.value] || {}).tip || ""; sel.title = tipOf(sel.value); delete sel.dataset.busy;
    act({ do: "model", chat: d.chat, model: sel.value, hours: 0 }, L("המודל הוחלף", "Model changed")); };
  const cg = (d.groups || []).find(g => g.jid === d.chat) || {};
  $("modelNow").innerHTML = `<strong>${esc(cg.name || "")}</strong><br>` + $("modelNow").innerHTML;
  const rc = mi.recommend;
  $("modelRec").textContent = rc && rc.model ? `💡 ${L("המלצה: ", "Suggestion: ")}${(guide[rc.model] || {}).he || rc.model} (${rc.why})` : "";
  $("modelBox").classList.toggle("working", !!(d.live && !d.live.done));
  $("log").textContent = (d.log || []).join("\n");
  renderControl(d);
  renderDrawer(false);
  drawCharts(d);
}

// ---- current conversation card + tabs ----
const groupOf = jid => ((last && last.groups) || []).find(g => g.jid === jid) || {};
function renderTabs(d) {
  const gs = curPeer === null ? (d.groups || []) : ((peersInfo && peersInfo.self && peersInfo.self.groups) || []);
  if (!d.chat || d.chat !== curChat) { curChat = d.chat; try { localStorage.setItem("wa-dash-chat", curChat); } catch (e) {} }
  const sel = (peer, jid) => peer === curPeer && jid === d.chat;
  const tab = (g, peer, label) => {
    const run = g.live === true || (g.live && !g.live.done);
    return `<button class="tab${peer !== null ? " peer-tab" : ""}" role="tab" aria-selected="${sel(peer, g.jid)}" data-chat="${esc(g.jid)}" data-peer="${peer === null ? "" : peer}" title="${esc(PURPOSE[g.purpose] || "")}${g.budget ? ` · ${L("תקרת שימוש", "cap")} ${M(g.budget, 0)}` : ""}">` +
      `<span aria-hidden="true">${peer !== null ? "🖥️" : g.jid === "local" ? "💻" : "👥"}</span><span class="t-name">${esc(label || g.name)}</span>` +
      `${g.paused ? `<span class="t-paused" title="${L("ההאזנה מושהית", "listening paused")}">⏸</span>` : ""}` +
      `${run ? `<span class="t-dot" title="${L("עובד עכשיו", "working now")}"></span>` : ""}${g.pending ? `<span class="t-badge">${g.pending}</span>` : ""}` +
      `${g.model ? `<span class="t-meta">${esc(MODEL_HE(g.model))}${g.spent !== undefined ? " · " + M(g.spent) : ""}</span>` : ""}</button>`;
  };
  // on a peer tab d.groups are the peer's conversations (full details for the selected one)
  const peerTabs = ((peersInfo && peersInfo.peers) || []).map(p => {
    if (!p.online || !p.brief) return `<button class="tab peer-tab off" role="tab" aria-selected="false" data-chat="" data-peer="${p.i}" title="${esc(p.error || "")}"><span aria-hidden="true">🖥️</span><span class="t-name">${esc(p.name)}</span><span class="t-paused">${L("לא מחובר", "offline")}</span></button>`;
    const list = curPeer === p.i ? (d.groups || []) : (p.brief.groups || []);
    return list.map(g => tab(g, p.i, `${p.name} · ${g.name}`)).join("");
  }).join("");
  $("tabs").innerHTML = gs.map(g => tab(g, null)).join("") + peerTabs;
  $("tabAdd").textContent = "➕ " + L("חבר קבוצה", "Connect a group");
  const pr = d.presence || {}, pcL = pr.pc_last ? toDate(pr.pc_last).getTime() : 0, phL = pr.phone_last ? toDate(pr.phone_last).getTime() : 0, recent = Math.max(pcL, phL);
  $("presence").innerHTML = !recent || Date.now() - recent > 30 * 60000 ? "🕑 " + L("לא פעיל לאחרונה", "idle")
    : pcL >= phL ? `💻 <b>${L("אתה במחשב", "You are at the PC")}</b>` : `📱 <b>${L("אתה בנייד", "You are on the phone")}</b>`;
  const g = groupOf(d.chat), local = d.chat === "local";
  $("grpName").textContent = g.name || "—";
  $("grpMeta").textContent = `${PURPOSE[g.purpose] || ""} · ${L("תקרת שימוש", "cap")} ${g.budget == null ? "—" : M(g.budget, 0)} · ${L("היום", "today")} ${M(g.spent ?? 0)}`;
  $("grpEdit").hidden = local; $("grpOff").hidden = !!g.primary;
  { const n = rulesCount(d); $("rulesSum").textContent = n ? String(n) : ""; }
  $("h-chat").textContent = local ? L("שיחה מקומית עם Claude", "Local chat with Claude") : L("השיחה בקבוצה", "Conversation in group") + " · " + (g.name || "");
  $("syncChk").checked = !!syncMap[d.chat]; sendHint();
  const run = !!(d.live && !d.live.done); $("stopBtn").hidden = !run;
  fitChat();
}
$("tabAdd").onclick = e => { e.stopPropagation(); toggleAdd(); };
// base rules of the current conversation (agent/home/groups/*.md): shown read-only; added with /כלל בסיס so Claude checks them first
const RULE_LINE = /^\s*(?:[-*•]|\d+[.)])\s+(\S.*)$/;
function rulesList(d) {
  // each rule: "- YYYY-MM-DD HH:MM · text" (older ones may have no time); ordered by the time it was taken in
  return ((d && d.rules && d.rules.text) || "").split("\n").map(l => (l.match(RULE_LINE) || [])[1]).filter(Boolean).map((t, i) => {
    const m = t.match(/^\(?(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)\)?\s*[·:\-–]?\s*(.*)$/);
    return { at: m ? m[1].replace("T", " ") : "", text: m ? m[2] : t, i };
  }).sort((a, b) => (a.at || "") < (b.at || "") ? -1 : (a.at || "") > (b.at || "") ? 1 : a.i - b.i);
}
function rulesCount(d) { return rulesList(d).length; }
function showRules() {
  const r = (last && last.rules) || {}, g = groupOf(last && last.chat);
  drawerKind = "ctx"; $("scrim").hidden = false; $("drawer").hidden = false;
  $("drawerTitle").textContent = L("📜 כללי בסיס · ", "📜 Base rules · ") + (g.name || "");
  $("drawerTabs").innerHTML = "";
  const list = rulesList(last);
  $("drawerBody").innerHTML = `<p class="note tight"><b>${L(`יש ${list.length} כללים`, `${list.length} rules`)}</b></p>` +
    (list.length ? `<ol class="item" style="padding-inline-start:1.6em">${list.map(x => `<li style="margin:.35em 0">${x.at ? `<span class="muted">${esc(fmtRuleAt(x.at))}</span> · ` : ""}${esc(x.text)}</li>`).join("")}</ol>`
      : `<p class="empty">${L("עוד אין כללי בסיס לשיחה הזו", "No base rules for this conversation yet")}</p>`) +
    `<p class="note tight">${L("להוספת כלל: כתוב בשיחה (כאן או בווצאפ) <b>/כלל בסיס</b> ואחריו הכלל. Claude בודק שהוא ברור ולא סותר כללים קיימים, מציע ניסוח או טיפ, ושואל לפני שהוא מוסיף כשמשהו לא ברור. הכללים נטענים בכל סשן של השיחה הזו בלבד; הכללים לכל השיחות נמצאים ב-agent/home/CLAUDE.md.",
      "To add a rule, write <b>/rule</b> and the rule in this conversation (here or in WhatsApp). Claude checks it is clear and does not contradict existing rules, suggests wording, and asks first when unclear. Loaded in every session of this conversation only; rules for all conversations are in agent/home/CLAUDE.md.")}</p>` +
    "";
  $("drawerClose").focus();
}
function fmtRuleAt(s) { const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?: (\d{2}:\d{2}))?/); return m ? `${m[3]}.${m[2]}.${m[1].slice(2)}${m[4] ? " " + m[4] : ""}` : s; }
$("rulesBtn").onclick = showRules;
$("tabs").addEventListener("click", e => {
  const t = e.target.closest(".tab"); if (!t) return;
  curChat = t.dataset.chat; curPeer = t.dataset.peer === "" ? null : +t.dataset.peer;
  try { localStorage.setItem("wa-dash-chat", curChat); localStorage.setItem("wa-dash-peer", curPeer === null ? "" : String(curPeer)); } catch (er) {}
  const c = $("chat"); c._init = false; c.innerHTML = ""; load();
});
$("grpRename").onclick = async () => {
  const n = $("grpInput").value.trim(); if (!n) { toast(L("כתוב שם", "Type a name")); return; }
  if (!confirm(L(`לשנות את שם הקבוצה ל: ${n}?`, `Rename the group to: ${n}?`))) return;
  const j = await act({ do: "group_rename", jid: curChat, name: n }, L("השם שונה", "Renamed")); if (j && !j.error) $("grpInput").value = "";
};
$("grpBudget").onclick = async () => {
  const g = groupOf(curChat), v = prompt(L(`תקרת שימוש יומית (דולר) ל"${g.name}":`, `Daily cost cap (USD) for "${g.name}":`), g.budget);
  if (v === null) return; const j = await act({ do: "group_budget", jid: curChat, budget: v }, L("תקרת השימוש עודכנה", "Cap updated")); if (j && j.error) toast(j.error);
};
$("grpOff").onclick = async () => {
  const g = groupOf(curChat);
  if (!confirm(L(`לנתק את ההאזנה לקבוצה "${g.name}"? הסוכן יפסיק לענות בה (הקבוצה והיסטוריה נשארות).`, `Stop listening to "${g.name}"?`))) return;
  const j = await act({ do: "group_disconnect", jid: curChat }, L("ההאזנה נותקה", "Disconnected")); if (j && !j.error) { curChat = ""; load(); }
};

// ---- connect a WhatsApp group ----
function toggleAdd(force) {
  const pop = $("addPop"), show = force === undefined ? pop.hidden : force; pop.hidden = !show;
  if (show) loadAvail();
}
async function loadAvail() {
  const ul = $("availList"); ul.innerHTML = `<li class="empty">${L("טוען קבוצות…", "Loading groups…")}</li>`;
  const j = await post({ do: "groups_available" });
  const gs = (j.groups || []).filter(g => g.name).sort((x, y) => x.name.localeCompare(y.name, "he"));
  ul.innerHTML = gs.length ? gs.map((g, i) => `<li><span>${esc(g.name)}</span><button class="btn small" data-i="${i}">${L("חבר", "Connect")}</button></li>`).join("") : `<li class="empty">${esc(j.error || L("אין קבוצות פנויות", "No groups available"))}</li>`;
  ul.querySelectorAll("button").forEach(b => b.onclick = async () => {
    const g = gs[+b.dataset.i]; b.disabled = true;
    let r = await post({ do: "group_connect", jid: g.jid, name: g.name, purpose: $("addPurpose").value });
    if (r.needs_confirm) {
      if (!confirm(L(`בקבוצה "${g.name}" יש ${r.members} חברים. התשובות שלי יהיו גלויות לכולם (אענה רק להודעות שלך). לחבר בכל זאת?`, `The group has ${r.members} members; my answers will be visible to all. Connect anyway?`))) { b.disabled = false; return; }
      r = await post({ do: "group_connect", jid: g.jid, name: g.name, purpose: $("addPurpose").value, confirm_members: true });
    }
    if (r.error) { toast(r.error); b.disabled = false; return; }
    toast(L("מחובר — מעכשיו אענה בקבוצה", "Connected")); curChat = r.jid; toggleAdd(false); load();
  });
}
$("newGrpBtn").onclick = async () => {
  const n = $("newGrpName").value.trim(); if (!n) { toast(L("כתוב שם לקבוצה", "Type a group name")); return; }
  $("newGrpBtn").disabled = true;
  const r = await post({ do: "group_create", name: n, purpose: $("addPurpose").value });
  $("newGrpBtn").disabled = false;
  if (r.error) { toast(r.error); return; }
  $("newGrpName").value = ""; toast(L("הקבוצה נוצרה ומחוברת", "Group created and connected")); curChat = r.jid; toggleAdd(false); load();
};
$("addClose").onclick = () => toggleAdd(false);
document.addEventListener("keydown", e => { if (e.key === "Escape") toggleAdd(false); });

// ---- long bubbles: show more / less (remembered across refreshes) ----
const expanded = new Set();
$("chat").addEventListener("click", e => {
  const b = e.target.closest(".more"); if (!b) return;
  const k = b.closest(".bubble").dataset.k; if (expanded.has(k)) expanded.delete(k); else expanded.add(k);
  if (last) render(last);
});

// ---- search the whole history ----
const sBtn = $("searchBtn"), sBox = $("searchBox"), sInp = $("searchInp"), sList = $("searchList");
let sTimer = null, sItems = [], sSel = -1, sSeq = 0;
function openSearch(v) { sBox.hidden = !v; sBtn.setAttribute("aria-expanded", v); if (v) { sInp.focus(); sInp.select(); } }
sBtn.onclick = e => { e.stopPropagation(); openSearch(sBox.hidden); };
document.addEventListener("click", e => { if (!e.target.closest(".search-wrap")) openSearch(false); });
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(true); }
  if (e.key === "Escape" && !sBox.hidden) { openSearch(false); sBtn.focus(); }
});
const hl = (t, q) => { const x = esc(t); if (!q) return x; try { return x.replace(new RegExp("(" + esc(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi"), "<mark>$1</mark>"); } catch (e) { return x; } };
function drawSearch(q) {
  sInp.setAttribute("aria-expanded", !!q);
  sList.innerHTML = !q ? "" : sItems.length ? sItems.map((r, i) => `<li role="option" id="so${i}" aria-selected="${i === sSel}" data-i="${i}"><span class="s-head"><b>${esc(r.chat_name)}${r.kind === "voice" ? " 🎙️" : ""}</b><span>${esc(r.sender)} · ${fmt(r.ts)}</span></span><span class="s-snip">${hl(r.snippet, q)}</span></li>`).join("")
    : `<li class="s-empty">${L("לא נמצא — נסה חלק אחר מהמילה", "Nothing found — try part of the word")}</li>`;
  sInp.setAttribute("aria-activedescendant", sSel >= 0 ? "so" + sSel : "");
}
async function runSearch() {
  const q = sInp.value.trim(); if (q.length < 2) { sItems = []; drawSearch(""); return; }
  const my = ++sSeq; const j = await post({ do: "search", q }); if (my !== sSeq) return;
  sItems = j.results || []; sSel = sItems.length ? 0 : -1; drawSearch(q);
}
sInp.addEventListener("input", () => { clearTimeout(sTimer); sTimer = setTimeout(runSearch, 180); });
sInp.addEventListener("keydown", e => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (sItems.length) { sSel = (sSel + (e.key === "ArrowDown" ? 1 : -1) + sItems.length) % sItems.length; drawSearch(sInp.value.trim()); const el = $("so" + sSel); if (el) el.scrollIntoView({ block: "nearest" }); } }
  if (e.key === "Enter" && sSel >= 0) { e.preventDefault(); pickResult(sItems[sSel]); }
});
sList.addEventListener("click", e => { const li = e.target.closest("li[data-i]"); if (li) pickResult(sItems[+li.dataset.i]); });
async function pickResult(r) {
  openSearch(false);
  const j = await post({ do: "context", chat: r.chat, ts: r.ts });
  const q = sInp.value.trim();
  drawerKind = "ctx"; $("scrim").hidden = false; $("drawer").hidden = false; $("drawerTitle").textContent = L("הקשר: ", "Context: ") + (j.chat_name || r.chat_name);
  $("drawerTabs").innerHTML = "";
  const conn = ((last && last.groups) || []).find(g => g.jid === r.chat);
  $("drawerBody").innerHTML = ((j.messages || []).map(m => `<div class="ctx-msg${m.hit ? " hit" : ""}"><span class="who">${esc(m.who)} · ${fmt(m.timestamp)}</span>${hl(m.content || (m.media_type ? "[" + m.media_type + "]" : ""), m.hit ? q : "")}</div>`).join("") || `<p class="empty">${L("אין הקשר זמין", "No context")}</p>`) +
    (conn ? `<div class="row tight"><button class="btn small" id="ctxOpen">${L("פתח את השיחה בלשונית", "Open this conversation tab")}</button></div>` : "");
  const ob = $("ctxOpen"); if (ob) ob.onclick = () => { closeDrawer(); curChat = r.chat; load(); };
  $("drawerClose").focus();
}

async function load() {
  fetch("/peers", { cache: "no-store" }).then(r => r.json()).then(j => { if (!j.error) { peersInfo = j; renderMachines(); if (last) renderTabs(last); } }).catch(() => {});
  try {
    const d = await (await fetch(base() + "/data?chat=" + encodeURIComponent(curChat || ""), { cache: "no-store" })).json();
    if (d.offline) { renderOffline(d); return; }
    if (d.error) throw new Error(d.error); render(d);
  }
  catch (e) { $("connText").textContent = L("אין גישה לנתונים", "No access to data"); $("connSub").textContent = String(e); }
}
function renderOffline(d) {
  const p = peersInfo && (peersInfo.peers || []).find(x => x.i === curPeer);
  $("connDot").className = "pulse bad"; $("hero").dataset.state = "bad";
  $("connText").textContent = L(`המחשב "${p ? p.name : curPeer}" לא זמין כרגע`, `Computer "${p ? p.name : curPeer}" is not reachable`);
  $("connSub").textContent = L("המנהרה או הסוכן שם לא פועלים. אפשר לחזור ללשונית של המחשב הזה, או להפעיל אותו מכרטיס \"מחשבים\".", "Tunnel or agent is down there.") + (d.error ? " · " + d.error : "");
  $("chat").innerHTML = `<p class="empty">${L("אין חיבור למחשב השני", "No connection to the other computer")}</p>`;
}
load(); setInterval(load, 10000);

// ---- right-click menu: copy / cut / paste / select all, copy a whole message, move it to the input box ----
let ctxTarget = null, ctxBubble = null;
const ctx = $("ctx");
function hideCtx() { ctx.hidden = true; }
document.addEventListener("contextmenu", e => {
  const inBox = e.target.closest("#say"), bubble = e.target.closest(".bubble");
  if (!inBox && !bubble) return;
  e.preventDefault();
  ctxTarget = inBox ? say : null; ctxBubble = bubble;
  const sel = String(window.getSelection() || "");
  ctx.querySelector('[data-cmd="cut"]').hidden = !inBox;
  ctx.querySelector('[data-cmd="paste"]').hidden = !inBox;
  ctx.querySelector('[data-cmd="draft"]').hidden = !inBox || !(stored(draftKey()) || stored("wa-dash-draft-last"));
  ctx.querySelector('[data-cmd="lastsent"]').hidden = !inBox || !stored("wa-dash-sent-last");
  ctx.querySelector('[data-cmd="copymsg"]').hidden = ctx.querySelector('[data-cmd="quote"]').hidden = !bubble;
  ctx.querySelector('[data-cmd="copy"]').disabled = !sel && !(inBox && say.selectionStart !== say.selectionEnd);
  ctx.hidden = false;
  const w = ctx.offsetWidth, h = ctx.offsetHeight;
  ctx.style.left = Math.min(e.clientX, innerWidth - w - 6) + "px"; ctx.style.top = Math.min(e.clientY, innerHeight - h - 6) + "px";
});
document.addEventListener("click", e => { if (!e.target.closest("#ctx")) hideCtx(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") hideCtx(); });
window.addEventListener("blur", hideCtx);
const msgText = b => { const c = b.cloneNode(true); c.querySelectorAll(".who,.meta").forEach(x => x.remove()); return c.innerText.trim(); };
async function copyText(t) { try { await navigator.clipboard.writeText(t); } catch (e) { const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); } }
ctx.addEventListener("click", async e => {
  const cmd = e.target.closest("button") && e.target.closest("button").dataset.cmd; if (!cmd) return;
  hideCtx();
  const boxSel = () => say.value.slice(say.selectionStart, say.selectionEnd);
  if (cmd === "copy") { await copyText(ctxTarget ? boxSel() : String(window.getSelection())); toast(L("הועתק", "Copied")); }
  if (cmd === "cut") { const t = boxSel(); await copyText(t); say.setRangeText("", say.selectionStart, say.selectionEnd, "start"); grow(); }
  if (cmd === "paste") {
    say.focus();
    let t = null;
    // WebView2 blocks navigator.clipboard.readText (the menu's paste did nothing, owner 10.11): the dashboard server
    // reads this PC's clipboard; the browser API stays as the fallback (e.g. the dashboard opened in a browser)
    try { const j = await (await fetch("/clipboard", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json(); if (typeof j.text === "string") t = j.text; } catch (err) {}
    if (t == null) { try { t = await Promise.race([navigator.clipboard.readText(), new Promise((_, no) => setTimeout(() => no(), 1500))]); } catch (err) {} }
    if (t != null) { say.setRangeText(t, say.selectionStart, say.selectionEnd, "end"); grow(); }
    else if (!document.execCommand("paste")) toast(L("להדבקה: Ctrl+V", "To paste: Ctrl+V"));
  }
  if (cmd === "draft" || cmd === "lastsent") {  // put the text back without losing what is already typed
    const t = cmd === "draft" ? (stored(draftKey()) || stored("wa-dash-draft-last")) : stored("wa-dash-sent-last");
    if (t && say.value.trim() !== t.trim()) { say.value = say.value.trim() ? say.value + "\n" + t : t; grow(); saveDraft(); }
    say.focus();
  }
  if (cmd === "selectall") {
    if (ctxTarget) { say.focus(); say.select(); }
    else if (ctxBubble) { const r = document.createRange(); r.selectNodeContents(ctxBubble); const s2 = getSelection(); s2.removeAllRanges(); s2.addRange(r); }
  }
  if (cmd === "copymsg" && ctxBubble) { await copyText(msgText(ctxBubble)); toast(L("ההודעה הועתקה", "Message copied")); }
  if (cmd === "quote" && ctxBubble) { const t = msgText(ctxBubble); say.value = (say.value ? say.value + "\n" : "") + t; grow(); say.focus(); }
});

// ---- model box: minimize (remembered); chat card height = window below its top, so the input box is always visible ----
try { if (localStorage.getItem("wa-dash-mb-min") === "1") $("modelBox").classList.add("min"); } catch (e) {}
const mbLabel = () => { const m = $("modelBox").classList.contains("min"); $("mbToggle").textContent = m ? L("▸ הרחב", "▸ Expand") : L("▾ מזער", "▾ Minimize"); $("mbToggle").setAttribute("aria-expanded", !m); };
$("mbToggle").onclick = () => { $("modelBox").classList.toggle("min"); try { localStorage.setItem("wa-dash-mb-min", $("modelBox").classList.contains("min") ? "1" : "0"); } catch (e) {} mbLabel(); };
mbLabel();
function fitChat() {
  const card = document.querySelector(".chat-card"); if (!card) return;
  const top = card.getBoundingClientRect().top + window.scrollY;
  document.documentElement.style.setProperty("--chat-top", Math.round(top) + "px");
}
window.addEventListener("resize", fitChat); setTimeout(fitChat, 300); setTimeout(fitChat, 1500);

// ================= control: listening, sessions, queue, scheduled, settings, computers =================
const groupName = (d, jid) => (((d && d.groups) || []).find(g => g.jid === jid) || {}).name || (jid === "local" ? L("שיחה מקומית", "Local") : String(jid).split("@")[0]);
const QSTATUS = () => ({ pending: L("ממתינה", "pending"), running: L("רצה עכשיו", "running"), waiting_limit: L("מחכה למכסה", "waiting for limit"), interrupted: L("נקטעה", "interrupted") });
function renderControl(d) {
  const allP = !!d.listen_paused, g = (d.groups || []).find(x => x.jid === d.chat) || {};
  $("pausedChip").hidden = !allP && !g.paused; $("pausedChip").textContent = allP ? L("⏸ האזנה מושהית", "⏸ paused") : L("⏸ שיחה מושהית", "⏸ chat paused");
  $("listenAllBtn").textContent = allP ? L("▶ חדש האזנה (הכול)", "▶ Resume listening (all)") : L("⏸ השהה האזנה (הכול)", "⏸ Pause listening (all)");
  $("listenOneBtn").textContent = g.paused ? L("▶ חדש שיחה זו", "▶ Resume this chat") : L("⏸ השהה שיחה זו", "⏸ Pause this chat");
  const q = (d.queue || []).filter(x => x.chat === d.chat), host = d.machine ? `🖥️ ${d.machine}` : "";
  $("sessMeta").innerHTML = `${esc(host)} · ${esc(g.name || "")} · ${L("סשן", "session")} ${g.session ? `<code>${esc(g.session)}</code>` : L("— (חדש בהודעה הבאה)", "— (new on next message)")}` +
    `${g.session_started ? ` · ${L("התחיל", "started")} ${fmt(g.session_started)}` : ""} · ${g.session_runs || 0} ${L("הודעות", "msgs")}`;
  $("ctlMeta").innerHTML = `${g.session_runs || 0} ${L("הודעות בסשן", "messages in session")}` +
    `${q.length ? ` · <b>${q.length} ${L("בתור", "queued")}</b>` : ""}${d.limit_until ? `<br>⏳ ${L("מכסת Claude נגמרה — ממשיך ב-", "Claude limit reached — resumes at ")}${fmt(d.limit_until)}` : ""}` +
    `${allP ? `<br><b class="warn">${L("ההאזנה מושהית בכל השיחות — הודעות נשמרות ומחכות", "Listening paused everywhere — messages wait")}</b>` : g.paused ? `<br><b class="warn">${L("השיחה הזו מושהית", "This chat is paused")}</b>` : ""}`;
  const b = d.bridge || {}, p = d.policy || {};
  $("sendMode").textContent = b.paused ? L(`⛔ שליחה לאחרים מושהית עד ${fmt(p.paused_until)} — ${p.pause_reason || ""}`, `⛔ Sending to others paused until ${fmt(p.paused_until)} — ${p.pause_reason || ""}`)
    : L("📝 טיוטות בלבד: הכלי לא שולח לאף אחד חוץ ממך בלי אישור שלך.", "📝 Drafts only: nothing goes to anyone but you without your approval.");
  $("listenAllBtn").onclick = () => act({ do: allP ? "listen_resume" : "listen_pause" }, allP ? L("ההאזנה חודשה", "Resumed") : L("ההאזנה הושהתה — הודעות יחכו בתור", "Paused — messages will wait"));
  $("listenOneBtn").onclick = () => act({ do: g.paused ? "listen_resume" : "listen_pause", chat: d.chat }, g.paused ? L("השיחה חודשה", "Chat resumed") : L("השיחה הושהתה", "Chat paused"));
  $("sessNewBtn").onclick = () => { if (confirm(L(`לסגור את הסשן של "${g.name}" ולפתוח חדש (עם סיכום)?`, `Close the session of "${g.name}" and start a new one?`))) act({ do: "session_new", chat: d.chat }, L("נשלח לסוכן — הסשן ייסגר עם סיכום", "Sent — the session will close with a summary")); };
}

function queueContent(d) {
  const q = d.queue || [], st = QSTATUS();
  if (!q.length) return empty(L("התור ריק — אין פקודות שממתינות", "The queue is empty"));
  const by = {}; q.forEach(r => (by[r.chat] = by[r.chat] || []).push(r));
  return (d.limit_until ? `<div class="sum-row"><span class="stat warn">⏳ ${L("מכסת Claude נגמרה — ממשיך ב-", "Limit — resumes at ")}${fmt(d.limit_until)}</span></div>` : "") +
    Object.entries(by).map(([chat, rs]) => `<div class="item"><div class="item-head"><strong>${esc(groupName(d, chat))}</strong>
      <button class="btn small danger-soft" data-qclear="${esc(chat)}">🗑 ${L("בטל את כל הממתינות", "Cancel all waiting")}</button></div>
      ${rs.map(r => `<div class="q-row"><span class="pill ${r.status === "running" ? "sending" : r.status === "interrupted" ? "failed" : "pending"}">${st[r.status] || esc(r.status)}</span>
        <span class="q-text" title="${esc(r.text)}">${esc(r.text)}</span><span class="muted">${esc(String(r.at || "").slice(5, 16))}${r.source === "pc" ? " 💻" : ""}</span>
        <span class="q-act">${r.status === "running" ? `<button class="btn small" data-qstop="${esc(chat)}">⛔ ${L("עצור", "Stop")}</button>` :
          `${r.status !== "pending" ? `<button class="btn small" data-qretry="${r.id}">↻ ${L("הרץ שוב", "Retry")}</button>` : ""}<button class="btn small danger-soft" data-qcancel="${r.id}">✕ ${L("בטל", "Cancel")}</button>`}</span></div>`).join("")}</div>`).join("");
}

function schedContent(d) {
  const sc = d.scheduled || [], rm = d.reminders || [];
  return `<h3 class="muted" style="font-size:.82rem;margin:4px 0">${L("טיוטות מתוזמנות", "Scheduled drafts")}</h3>` +
    (sc.length ? sc.map(r => `<div class="item q-row"><span>📝 #${r.draft_id}</span><span class="q-text">${L("תישלח ב-", "sends at ")}${fmt(r.send_at)}</span><span class="q-act"><button class="btn small danger-soft" data-scancel="${r.id}">✕ ${L("בטל תזמון", "Cancel")}</button></span></div>`).join("") : empty(L("אין טיוטות מתוזמנות", "None"))) +
    `<h3 class="muted" style="font-size:.82rem;margin:10px 0 4px">${L("תזכורות פעילות", "Active reminders")}</h3>` +
    (rm.length ? rm.map(r => `<div class="item q-row"><span>⏰ #${r.id}${r.kind === "noreply" ? " 🔕" : ""}</span><span class="q-text" title="${esc(r.text)}">${esc(r.text)} · <span class="muted">${fmt(r.due)}</span></span>
      <span class="q-act"><button class="btn small" data-rsnooze="${r.id}">💤 ${L("דחה", "Snooze")}</button><button class="btn small danger-soft" data-rcancel="${r.id}">✕ ${L("בטל", "Cancel")}</button></span></div>`).join("") : empty(L("אין תזכורות פעילות", "None")));
}

const SET_LABEL = () => ({
  backup_time: L("גיבוי יומי", "Daily backup"), update_check_time: L("בדיקת עדכון (הודעה)", "Update check (notice)"), group_check_time: L("בדיקת שם קבוצה", "Group name check"),
  selftest_time: L("בדיקה עצמית לילית", "Nightly self-test"), morning_summary_time: L("סיכום בוקר", "Morning summary"), signin_check_time: L("בדיקת התחברות Claude", "Claude sign-in check"),
  auto_update_time: L("עדכון אוטומטי", "Auto-update"), auto_release_time: L("שחרור לילי ל-GitHub (מחשב מפתח)", "Nightly release to GitHub (maintainer)"), daily_budget_usd: L("תקרת שימוש יומית לשיחה ($)", "Daily cap per chat ($)"), total_daily_budget_usd: L("תקרת שימוש כוללת ליום ($)", "Total daily cap ($)"),
  light_model: L("מודל קל (אוטו)", "Light model (auto)"), claude_model: L("מודל רגיל (אוטו)", "Normal model (auto)"), deep_model: L("מודל חזק (!! / תכנון)", "Strong model (!!)"),
  fresh_sessions: L("סשן נקי לכל הודעה", "Fresh session per message"), session_max_runs: L("הודעות לסשן (4M) לפני החלפה", "Messages per session (4M)"), claude_timeout_minutes: L("תקרת זמן לפקודה (דקות; לקבוצה: groups.json timeout_minutes)", "Time limit per command (minutes)"),
  permission_mode: L("הרשאות Claude", "Claude permissions"), voice_replies: L("תשובה קולית להקלטה", "Voice reply to voice notes"), voice_name: L("קול", "Voice"), voice_style: L("סגנון קול", "Voice style"), group_name_model: L("שם הקבוצה לפי המודל (גשר אופוס ווצאפ)", "Group name shows the model"),
  transcribe: L("תמלול הקלטות שלי", "Transcribe my voice notes"), transcribe_all_chats: L("תמלול הקלטות בכל השיחות", "Transcribe all chats"),
  reply_italic: L("תשובות בנטוי", "Italic replies"), quote_replies: L("ציטוט ההודעה בתשובה", "Quote in replies"), strict_mcp: L("בלי שרתי MCP נוספים", "Strict MCP"),
  disconnect_alert_minutes: L("התראת ניתוק אחרי (דק')", "Disconnect alert after (min)"), backup_keep_days: L("שמירת גיבויים (ימים)", "Keep backups (days)"),
  backup_media_days: L("גיבוי מדיה של (ימים)", "Back up media of (days)"), reconnect_settle_seconds: L("המתנה אחרי חזרת חיבור (שנ')", "Wait after reconnect (s)"),
  auto_update: L("עדכון אוטומטי", "Auto-update"), morning_summary: L("סיכום בוקר", "Morning summary"),
  peer_watch: L("שמירה הדדית — התראה כשמחשב אחר נופל", "Watch the other computers"), peer_alert_minutes: L("התראה על גשר מנותק שם אחרי (דק')", "Alert: their bridge down after (min)"),
  peer_offline_minutes: L("התראה על מחשב שלא עונה אחרי (דק')", "Alert: computer unreachable after (min)"), peer_backup: L("גיבוי לילי גם למחשב השני", "Nightly backup to the other computer"),
  peer_backup_max_mb: L("מדיה לגיבוי בלילה, עד (MB)", "Media per night, up to (MB)"), host_label: L("שם המחשב הזה (תצוגה)", "This computer's name (display)"),
  claude_account: L("חשבון Claude במחשב הזה", "Claude account on this computer"), language: L("שפת התשובות של הסוכן", "Agent reply language"),
  auto_release: L("שחרור לילי", "Nightly release"),
});
const JOBS = [  // [time key, toggle key, kv job, run-now action]
  ["auto_release_time", "auto_release", "auto_release", null], ["auto_update_time", "auto_update", "auto_update", "tool_update_apply"], ["morning_summary_time", "morning_summary", "morning_summary", null],
  ["backup_time", null, "backup", "backup"], ["selftest_time", null, "selftest", "selftest_now"], ["update_check_time", null, "update_check", "tool_update_check"],
  ["signin_check_time", null, "signin_check", null], ["group_check_time", null, "group_check", null]];
function trGapHtml(d) {
  const g = d.transcribe_gap || {}, pr = g.progress || {};
  if (pr.state === "running") return `<div class="set-row"><span>🎙️ ${L("מתמלל חסרים", "Transcribing the gap")}: ${pr.done || 0}/${pr.total || 0}${pr.failed ? ` · ${pr.failed} ${L("נכשלו", "failed")}` : ""}</span></div>`;
  if (!g.missing) return pr.state === "done" ? `<p class="note tight">✓ ${L("כל ההקלטות תומללו", "All voice notes transcribed")} (${esc(String(pr.at || "").slice(5, 16))})</p>` : "";
  return `<div class="set-row"><span>🎙️ ${g.missing} ${L("הקלטות לא תומללו מאז", "voice notes not transcribed since")} ${fmt(g.since)}${g.requested ? ` · ${L("בתור…", "queued…")}` : ""}</span>
    <button type="button" class="btn small" data-trfill="1"${g.on ? "" : ` disabled title="${L("התמלול כבוי", "transcription is off")}"`}>${L("תמלל את החסרים", "Transcribe the missing")}</button></div>`;
}

function peerBackupHtml(d) {
  const pb = d.peer_backups || {}, names = Object.keys(pb);
  if (!names.length) return `<p class="note tight">${L("לגיבוי למחשב אחר צריך שם ssh או מנהרה ברשימת המחשבים למטה.", "Backup to another computer needs an ssh alias or tunnel in the list below.")}</p>`;
  return names.map(n => { const r = pb[n];
    const st = !r ? L("עוד לא רץ", "not run yet") : r.ok ? `✓ ${esc(String(r.at).slice(5, 16))} · ${r.dbs} ${L("מסדי נתונים", "databases")} · ${r.media_copied} ${L("קבצי מדיה", "media files")} (${r.media_mb} MB)${r.media_left ? ` · ${r.media_left} ${L("ימשיכו מחר", "left for tomorrow")}` : ""}`
      : `✖ ${esc(String(r.at).slice(5, 16))} · ${esc(r.error || "")}`;
    return `<div class="set-row"><span>💾 ${L("גיבוי ל-", "Backup to ")}${esc(n)}: <span class="${r && !r.ok ? "bad" : "muted"}">${st}</span></span></div>`; }).join("") +
    `<div class="row tight"><button type="button" class="btn small" data-now="peer_backup_now">💾 ${L("גבה עכשיו לשני", "Back up to the other now")}</button></div>`;
}

// one-line explanation of every setting: what it does and how to turn it off / on
const HELP = () => ({
  backup_time: L("גיבוי של ההודעות והמדיה לתיקיית הגיבוי, כל יום בשעה הזו.", "Backs up messages and media every day at this time."),
  morning_summary_time: L("סיכום של מה שקרה בלילה ומי מחכה לך. כיבוי: הסר את הסימון.", "Night summary and who is waiting for you. Untick to turn off."),
  selftest_time: L("בדיקה של כל הכלי; הודעה רק אם משהו נכשל.", "Checks the whole tool; tells you only if something failed."),
  signin_check_time: L("מזהיר כמה ימים לפני שההתחברות של Claude במחשב פגה.", "Warns a few days before Claude's sign-in on this PC expires."),
  group_check_time: L("בודק שהשם של כל קבוצה מחוברת תקין.", "Checks each connected group's name."),
  update_check_time: L("בודק אם יש גרסה חדשה ומודיע (בלי להתקין).", "Checks for a new version and tells you (does not install)."),
  auto_release_time: L("מפרסם ל-GitHub כל לילה את כל השינויים של היום (רק במחשב המפתח). כיבוי: הסר את הסימון.", "Publishes the day's changes to GitHub every night (maintainer PC only). Untick to turn off."),
  auto_update_time: L("מתקין גרסה חדשה אם יש, ומפעיל מחדש את הגשר והסוכן; חוזר לגרסה קודמת בכשל. כיבוי: הסר את הסימון.", "Installs a new version if there is one and restarts; rolls back on failure. Untick to turn off."),
  daily_budget_usd: L("מעליה Claude לא רץ באותה שיחה עד חצות. עקיפה: ! להודעה אחת, !!! לשעה.", "Above it Claude stops in that chat until midnight. Bypass: ! for one message, !!! for an hour."),
  total_daily_budget_usd: L("תקרת שימוש לכל השיחות יחד, בכל היום.", "Cap for all conversations together per day."),
  light_model: L("במצב אוטו: הודעות קצרות ופשוטות.", "Auto mode: short simple messages."), claude_model: L("במצב אוטו: רוב העבודה.", "Auto mode: most work."),
  deep_model: L("ל-!! (הודעה אחת) ולתכנון/ניתוח לעומק.", "For !! (one message) and deep planning."),
  group_name_model: L("שם הקבוצה בווצאפ מראה את המודל שעונה (גשר אופוס ווצאפ).", "The WhatsApp group name shows the answering model."),
  fresh_sessions: L("מסומן = כל הודעה מתחילה מאפס (זול, בלי זיכרון). לא מסומן = שיחה רציפה.", "Ticked = every message starts fresh. Unticked = continuous chat."),
  session_max_runs: L("אחרי כמה הודעות (עבודת 4M) הסשן מתחלף עם סיכום.", "After how many messages (4M work) the session rotates with a summary."),
  claude_timeout_minutes: L("כמה זמן פקודה אחת יכולה לרוץ לפני שנעצרת (לכל קבוצה אפשר אחרת).", "How long one command may run before it is stopped."),
  permission_mode: L("מה Claude רשאי לעשות במחשב בלי לשאול.", "What Claude may do on the PC without asking."),
  language: L("השפה שבה הסוכן עונה בווצאפ (דורש הפעלה מחדש).", "The language the agent answers in on WhatsApp (needs a restart)."),
  voice_replies: L("כשאתה מקליט — התשובה מגיעה גם כהודעה קולית. כיבוי: הסר את הסימון.", "When you send a voice note the answer also comes as voice. Untick to turn off."),
  voice_name: L("הקול של ההודעות הקוליות וההקראה. ▶ משמיע דוגמה.", "Voice for voice replies and read-aloud. ▶ plays a sample."),
  voice_style: L("רך = איטי ונמוך מעט.", "Soft = a little slower and lower."),
  transcribe: L("תמלול ההקלטות שלך לטקסט. כיבוי: הסר את הסימון (ואז \"שמור והפעל מחדש\").", "Transcribes your voice notes. Untick (then Save & restart) to turn off."),
  transcribe_all_chats: L("גם הקלטות של אחרים בכל השיחות.", "Also other people's voice notes in all chats."),
  reply_italic: L("התשובות בווצאפ בכתב נטוי.", "Replies in italics."), quote_replies: L("התשובה מצטטת את ההודעה שלך.", "Replies quote your message."),
  strict_mcp: L("Claude רץ רק עם הכלים של הגשר, בלי שרתי MCP אחרים.", "Claude runs with the bridge tools only."),
  disconnect_alert_minutes: L("התראת Windows אם הגשר מנותק יותר מזה.", "Windows alert if the bridge is down longer than this."),
  reconnect_settle_seconds: L("כמה לחכות אחרי חזרת חיבור לפני הדוח (כדי שהודעות יגיעו).", "Wait after reconnect before the report."),
  backup_keep_days: L("כמה ימים שומרים גיבויים.", "How many days backups are kept."), backup_media_days: L("גיבוי מדיה מכמה ימים אחורה.", "Back up media from how many days back."),
  peer_watch: L("כל מחשב בודק את השני ומודיע לך אם נפל. כיבוי: הסר את הסימון.", "Each computer watches the other and tells you if it fails."),
  peer_alert_minutes: L("אחרי כמה דקות של גשר מנותק במחשב השני להודיע.", "Minutes of the other bridge being down before alerting."),
  peer_offline_minutes: L("אחרי כמה דקות שהמחשב השני לא עונה להודיע.", "Minutes of the other computer not answering before alerting."),
  peer_backup: L("אחרי הגיבוי הלילי — עותק גם במחשב השני.", "After the nightly backup, a copy also on the other computer."),
  peer_backup_max_mb: L("כמה מדיה להעתיק בלילה אחד.", "How much media to copy per night."),
  host_label: L("שם המחשב הזה בלוח ובכרטיס העלות.", "This computer's name in the dashboard."), claude_account: L("איזה חשבון Claude מחובר כאן (לתצוגה).", "Which Claude account is signed in here."),
  auto_update: "", auto_release: "", morning_summary: "",
});
let setOpen = false, setTab = "general", setDirty = false;
function openSettings(tab) {
  setOpen = true; if (tab) setTab = tab; try { localStorage.setItem("wa-dash-settab", setTab); } catch (e) {}
  $("setScrim").hidden = false; $("settings").hidden = false; renderSettings(); $("setClose").focus();
}
function closeSettings() {
  if ((setDirty || auDirty()) && !confirm(L("יש שינויים שלא נשמרו. לסגור בלי לשמור?", "There are unsaved changes. Close without saving?"))) return;
  setOpen = false; setDirty = false; $("setScrim").hidden = true; $("settings").hidden = true;
}
try { setTab = localStorage.getItem("wa-dash-settab") || "general"; } catch (e) {}
$("settingsBtn").onclick = () => openSettings();
document.querySelectorAll("[data-settings]").forEach(b => b.onclick = () => openSettings(b.dataset.settings));
$("machLine").onclick = () => openSettings("machines");
$("setClose").onclick = closeSettings; $("setScrim").onclick = closeSettings;
document.addEventListener("keydown", e => { if (e.key === "Escape" && setOpen && !drawerKind) closeSettings(); });
$("setTabs").addEventListener("click", e => { const b = e.target.closest("[data-tab]"); if (!b) return; setTab = b.dataset.tab; try { localStorage.setItem("wa-dash-settab", setTab); } catch (er) {} showSetTab(); });
$("settings").addEventListener("input", e => { if (e.target.closest("[data-set]") || e.target.matches("[data-set], [data-pf]")) { setDirty = true; $("setDirtyNote").textContent = L("● יש שינויים שלא נשמרו", "● Unsaved changes"); } });
$("settings").addEventListener("change", e => { if (e.target.matches("[data-set], [data-pf]")) { setDirty = true; $("setDirtyNote").textContent = L("● יש שינויים שלא נשמרו", "● Unsaved changes"); } });
$("setConnect").onclick = () => { closeSettings(); if (!setOpen) toggleAdd(true); };
$("sendPause").onclick = () => act({ do: "pause", hours: 6 }, L("השליחה לאחרים הושהתה ל-6 שעות", "Sending to others paused for 6 hours"));
$("sendResume").onclick = () => act({ do: "resume" }, L("השליחה חודשה", "Sending resumed"));
function showSetTab() {
  document.querySelectorAll("#setTabs [data-tab]").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === setTab));
  document.querySelectorAll("#setPanes .set-pane").forEach(p => p.hidden = p.dataset.pane !== setTab);
  $("settings").classList.toggle("au-mode", setTab === "auto");  // automations save per card: no global save footer
  if (setTab === "auto" && setOpen) { loadAuto(!auData); auStartTimer(); }
}
function renderSettings() {
  if (!last) return;
  const panes = settingsPanes(last);
  Object.entries(panes).forEach(([k, html]) => { const el = $("dyn-" + k); if (el) el.innerHTML = html; });
  $("setMachine").textContent = L("נשמר במחשב: ", "Saved on: ") + (last.machine || "") + (curPeer !== null ? L(" (המחשב השני)", " (the other computer)") : "");
  setDirty = false; $("setDirtyNote").textContent = "";
  showSetTab(); translateStatic(); wireDrawer($("settings"));
}

function settingsPanes(d) {
  const S = d.settings || {}, v = S.values || {}, t = S.types || {}, lab = SET_LABEL(), jobs = d.jobs || {};
  const VOICE_NAMES = { "pt-BR-ThalitaMultilingualNeural": L("תליטה", "Thalita"), "en-US-AvaMultilingualNeural": L("אווה", "Ava"),
    "en-US-EmmaMultilingualNeural": L("אמה", "Emma"), "fr-FR-VivienneMultilingualNeural": L("ויויאן", "Vivienne"),
    "de-DE-SeraphinaMultilingualNeural": L("סרפינה", "Seraphina"), "he-IL-HilaNeural": L("הילה", "Hila"),
    "he-IL-AvriNeural": L("אבי (גבר)", "Avri (male)"), "en-US-AndrewMultilingualNeural": L("אנדרו (גבר)", "Andrew (male)") };
  const inp = k => {
    const ty = t[k], val = v[k];
    if (ty === "bool") return `<input type="checkbox" data-set="${k}"${val ? " checked" : ""} aria-label="${esc(lab[k] || k)}">`;
    if (ty === "time") return `<input type="time" data-set="${k}" value="${esc(val)}" aria-label="${esc(lab[k] || k)}">`;
    if (ty === "model") return `<select class="btn small sel" data-set="${k}" aria-label="${esc(lab[k] || k)}">${(S.models || []).map(m => `<option${m === val ? " selected" : ""}>${esc(m)}</option>`).join("")}</select>`;
    if (ty === "voice") return `<select class="btn small sel" data-set="${k}" aria-label="${esc(lab[k] || k)}">${(S.voices || []).map(m => `<option value="${esc(m)}"${m === val ? " selected" : ""}>${esc(VOICE_NAMES[m] || m)}</option>`).join("")}</select>
      <button type="button" class="btn small" data-voicetest="1" title="${L("השמע דוגמה בקול שנבחר", "Play a sample in the chosen voice")}">▶</button>`;
    if (ty === "vstyle") return `<select class="btn small sel" data-set="${k}" aria-label="${esc(lab[k] || k)}">${["normal", "soft"].map(m => `<option value="${m}"${m === val ? " selected" : ""}>${m === "soft" ? L("רך (איטי ונמוך מעט)", "Soft") : L("רגיל", "Normal")}</option>`).join("")}</select>`;
    if (ty === "choice") { const opts = (S.choices && S.choices[k]) || S.perm_modes || [], nm = { he: "עברית", en: "English" };
      return `<select class="btn small sel" data-set="${k}" aria-label="${esc(lab[k] || k)}">${opts.map(m => `<option value="${esc(m)}"${m === val ? " selected" : ""}>${esc(nm[m] || m)}</option>`).join("")}</select>`; }
    if (ty === "num" || ty === "int") return `<input type="number" step="${ty === "int" ? 1 : 0.5}" data-set="${k}" value="${esc(val)}" style="width:80px" aria-label="${esc(lab[k] || k)}">`;
    return `<input type="text" data-set="${k}" value="${esc(val)}" style="width:150px" aria-label="${esc(lab[k] || k)}">`;
  };
  const help = HELP(), hl = k => help[k] ? `<small class="help">${esc(help[k])}</small>` : "";
  const row = k => `<label class="set-row"><span>${esc(lab[k] || k)}${hl(k)}</span><span class="set-ctl">${inp(k)}</span></label>`;
  const jobRow = ([tk, bk, job, now]) => `<div class="set-row"><span>${esc(lab[tk] || tk)}${hl(tk)}</span><span class="set-ctl">${bk ? inp(bk) : ""}${inp(tk)}
    <span class="muted" title="${L("הרצה אחרונה", "last run")}">${jobs[job] ? "✓ " + esc(String(jobs[job]).slice(5)) : ""}</span>
    ${now ? `<button type="button" class="btn small" data-now="${now}">${now === "tool_update_check" ? L("בדוק", "Check") : L("עכשיו", "Now")}</button>` : ""}</span></div>`;
  const sec = (title, body) => `<details class="item set-sec" open><summary><strong>${title}</strong></summary>${body}</details>`;
  const peersRows = (S.peers || []).map((p, i) => `<div class="set-row peer-row" data-pi="${i}"><input type="text" data-pf="name" value="${esc(p.name)}" placeholder="${L("שם", "name")}" style="width:80px">
    <input type="text" data-pf="url" value="${esc(p.url)}" placeholder="http://127.0.0.1:8781" style="width:150px"><input type="text" data-pf="tunnel" value="${esc(p.tunnel || "")}" placeholder="${L("מנהרה", "tunnel")} (${L("לא חובה", "optional")})" style="width:90px" title="${L("שם ssh שהסוכן כאן מחזיק דרכו את החיבור לשני", "ssh alias this agent keeps the tunnel through")}">
    <input type="text" data-pf="ssh" value="${esc(p.ssh || "")}" placeholder="ssh (${L("לא חובה", "optional")})" style="width:80px" title="${L("שם ssh להפעלת הסוכן שם ולגיבוי — אם אין מנהרה", "ssh alias for starting the agent there and backups")}">
    <button type="button" class="btn small danger-soft" data-prm="${i}">✕</button></div>`).join("");
  const tun = (peersInfo && peersInfo.self && peersInfo.self.tunnels) || {};
  const groupsCost = `<details class="item set-sec" open><summary><strong>${L("תקרת שימוש לכל שיחה", "Cap per conversation")}</strong></summary>${(d.groups || []).map(g => `<div class="set-row"><span>${g.jid === "local" ? "💻" : "👥"} ${esc(g.name)} · <span class="muted">${L("היום", "today")} ${M(g.spent ?? 0)}</span></span>
    <button type="button" class="btn small" data-gbudget="${esc(g.jid)}">💲 ${g.budget == null ? "—" : M(g.budget, 0)}</button></div>`).join("")}</details>`;
  return {
    general: sec(L("הסוכן", "The agent"), ["language", "reply_italic", "quote_replies", "strict_mcp"].map(row).join("")),
    voice: sec(L("קול", "Voice"), ["voice_replies", "voice_name", "voice_style"].map(row).join("") +
        `<label class="set-row"><span>🛞 ${L("הקראה קבועה בשיחה הנוכחית (כל תשובה גם קולית; בווצאפ: /🛞)", "Read every answer aloud in this conversation (WhatsApp: /🛞)")}</span><input type="checkbox" data-readall="1"${(d.read_all || {})[curChat || Object.keys(d.read_all || {})[0]] ? " checked" : ""}></label>` +
        `<p class="note tight help">🛞 ${L("הקראה: שלח 🛞 בקבוצה (מקריא את התשובה האחרונה שלי) או הגב 🛞 על כל הודעה (מקריא אותה). לא מפעיל את Claude ועובד גם כשהתשובה הקולית כבויה.", "Read-aloud: send 🛞 (reads my last answer) or react 🛞 on any message (reads it). No Claude run; works even with voice replies off.")}</p>`) +
      sec(L("תמלול", "Transcription"), ["transcribe", "transcribe_all_chats"].map(row).join("") + trGapHtml(d)),
    models: sec(L("מודלים וסשן", "Models & sessions"), ["light_model", "claude_model", "deep_model", "group_name_model", "fresh_sessions", "session_max_runs", "claude_timeout_minutes", "permission_mode"].map(row).join("")),
    cost: sec(L("תקרות שימוש", "Caps"), ["daily_budget_usd", "total_daily_budget_usd"].map(row).join("") +
        `<p class="note tight help">🔒 ${L("כשמגיעים לתקרת השימוש: ! בתחילת הודעה = הודעה אחת בלי תקרת שימוש; !!! = שעה בלי תקרת שימוש; בחצות מתאפס.", "At the cap: ! at the start = one message without the cap; !!! = one hour; resets at midnight.")}</p>`) + groupsCost,
    groups: sec(L("קבוצות כבויות", "Switched-off groups"), (d.disabled_groups || []).length ? d.disabled_groups.map(g => `<div class="set-row"><span>${esc(g.name)}</span><button type="button" class="btn small" data-genable="${esc(g.jid)}">▶ ${L("הפעל האזנה", "Listen again")}</button></div>`).join("") : empty(L("אין", "None"))),
    send: sec(L("טיוטות מתוזמנות ותזכורות", "Scheduled drafts & reminders"), schedContent(d)),
    sched: sec(L("תזמונים", "Schedules"), JOBS.map(jobRow).join("")) + sec(L("התראות וגיבוי", "Alerts & backup"), ["disconnect_alert_minutes", "reconnect_settle_seconds", "backup_keep_days", "backup_media_days"].map(row).join("")),
    machines: sec(L("המחשבים האחרים — שמירה וגיבוי", "Other computers — watch & backup"), ["peer_watch", "peer_alert_minutes", "peer_offline_minutes", "peer_backup", "peer_backup_max_mb"].map(row).join("") + peerBackupHtml(d)) +
      sec(L("זהות המחשב", "This computer"), ["host_label", "claude_account"].map(row).join("")) +
      sec(L("מחשבים מחוברים", "Connected computers"), peersRows + `<div class="row tight"><button type="button" class="btn small" id="peerAdd">➕ ${L("הוסף מחשב", "Add computer")}</button><button type="button" class="btn small primary" id="peerSave">💾 ${L("שמור מחשבים (מפעיל מחדש)", "Save computers (restarts)")}</button></div>` +
        Object.entries(tun).map(([n, st]) => `<div class="set-row"><span>${L("מנהרה ל-", "Tunnel to ")}${esc(n)}: <b>${esc((st && st.state) || "—")}</b>${st && st.error ? ` <span class="muted">${esc(st.error)}</span>` : ""}</span>
          <button type="button" class="btn small" data-tun="${esc(n)}" data-on="${st && st.state === "off" ? 1 : 0}">${st && st.state === "off" ? L("▶ הפעל", "▶ On") : L("⏹ כבה", "⏹ Off")}</button></div>`).join("") +
        `<p class="note tight help">${L("מנהרה = חיבור ssh שהסוכן כאן שומר לשני, כדי שכל לוח יראה את שני המחשבים. כבה = הלשוניות של השני יוצגו כלא מחוברות.", "Tunnel = the ssh link this agent keeps so each dashboard sees both computers. Off = the other's tabs show as offline.")}</p>`),
    plugins: sec(L("תוספים", "Plugins"), (S.plugins || []).length ? S.plugins.map(p => `<label class="set-row"><span>${esc(p.name)}</span><input type="checkbox" data-plugin="${esc(p.path)}"${p.on ? " checked" : ""}></label>`).join("") + `<p class="note tight help">${L("הפעלה/כיבוי תוסף מפעיל את הסוכן מחדש.", "Switching a plugin restarts the agent.")}</p>` : empty(L("אין תוספים", "No plugins"))),
  };
}

function wireDrawer(body) {
  const q = (sel, fn) => body.querySelectorAll(sel).forEach(b => b.onclick = () => fn(b));
  q("[data-qcancel]", b => act({ do: "cmd_cancel", id: +b.dataset.qcancel }, L("הפקודה בוטלה", "Cancelled")));
  q("[data-qretry]", b => act({ do: "cmd_retry", id: +b.dataset.qretry }, L("הפקודה חזרה לתור", "Re-queued")));
  q("[data-qclear]", b => { if (confirm(L("לבטל את כל הפקודות שממתינות בשיחה הזו?", "Cancel all waiting commands in this chat?"))) act({ do: "cmd_clear", chat: b.dataset.qclear }, L("התור נוקה", "Cleared")); });
  q("[data-qstop]", b => act({ do: "stop", chat: b.dataset.qstop }, L("עוצר…", "Stopping…")));
  q("[data-scancel]", b => act({ do: "sched_cancel", id: +b.dataset.scancel }, L("התזמון בוטל — הטיוטה נשארת ממתינה", "Schedule cancelled")));
  q("[data-rcancel]", b => act({ do: "rem_cancel", id: +b.dataset.rcancel }, L("התזכורת בוטלה", "Reminder cancelled")));
  q("[data-rsnooze]", b => { const m = prompt(L("לדחות בכמה דקות?", "Snooze for how many minutes?"), "60"); if (m) act({ do: "rem_snooze", id: +b.dataset.rsnooze, minutes: m }, L("נדחה", "Snoozed")); });
  q("[data-genable]", b => act({ do: "group_enable", jid: b.dataset.genable, on: true }, L("ההאזנה לקבוצה הופעלה", "Listening again")).then(() => renderDrawer(true)));
  q("[data-plugin]", b => { if (confirm(L("להפעיל/לכבות את התוסף? הסוכן יופעל מחדש.", "Switch the plugin? The agent restarts."))) act({ do: "plugin_toggle", path: b.dataset.plugin, on: b.checked }, L("נשמר — הסוכן עולה מחדש", "Saved — restarting")); else b.checked = !b.checked; });
  q("[data-now]", b => {
    const a = b.dataset.now;
    if (a === "tool_update_apply" && !confirm(L("לעדכן עכשיו? הגשר והסוכן יופעלו מחדש.", "Update now? Bridge and agent restart."))) return;
    act({ do: a, force: true }, { backup: L("הגיבוי התחיל", "Backup started"), selftest_now: L("הבדיקה רצה ברקע", "Self-test running"), tool_update_apply: L("העדכון התחיל", "Update started"),
      peer_backup_now: L("הגיבוי למחשב השני התחיל ברקע", "Backup to the other computer started") }[a])
      .then(j => { if (a === "tool_update_check" && j && !j.error) toast(j.available ? L(`גרסה ${j.latest} זמינה`, `${j.latest} available`) : L(`הכלי עדכני (${j.current})`, `Up to date (${j.current})`)); });
  });
  q("[data-trfill]", b => act({ do: "transcribe_backfill" }, L("התמלול של החסרים יתחיל תוך דקה — ההתקדמות כאן", "Starts within a minute — progress shows here")).then(() => setTimeout(() => renderDrawer(true), 1500)));
  q("[data-hist]", b => { const n = prompt(L("כמה הודעות ישנות לבקש מהטלפון?", "How many older messages to ask the phone for?"), "50"); if (n) act({ do: "history_sync", jid: b.dataset.hist, count: +n || 50 }, L("בקשה נשלחה לטלפון — ההודעות יגיעו תוך דקות", "Request sent to the phone — messages arrive within minutes")); });
  q("[data-tun]", b => act({ do: "tunnel", name: b.dataset.tun, on: b.dataset.on === "1" }, L("נשמר", "Saved")).then(() => setTimeout(() => renderDrawer(true), 1200)));
  q("[data-prm]", b => { b.closest(".peer-row").remove(); });
  const add = body.querySelector("#peerAdd"); if (add) add.onclick = () => add.closest(".row").insertAdjacentHTML("beforebegin",
    `<div class="set-row peer-row"><input type="text" data-pf="name" placeholder="${L("שם", "name")}" style="width:80px"><input type="text" data-pf="url" value="http://127.0.0.1:8781" style="width:150px"><input type="text" data-pf="tunnel" placeholder="${L("מנהרה", "tunnel")}" style="width:90px"><input type="text" data-pf="ssh" placeholder="ssh" style="width:80px"><button type="button" class="btn small danger-soft" onclick="this.parentNode.remove()">✕</button></div>`);
  const ps = body.querySelector("#peerSave"); if (ps) ps.onclick = () => {
    const list = [...body.querySelectorAll(".peer-row")].map(r => Object.fromEntries([...r.querySelectorAll("[data-pf]")].map(x => [x.dataset.pf, x.value.trim()])));
    act({ do: "peers_save", peers: list }, L("נשמר — הסוכן עולה מחדש", "Saved — restarting"));
  };
  q("[data-gbudget]", b => { const g = groupOf(b.dataset.gbudget), v = prompt(L(`תקרת שימוש יומית (דולר) ל"${g.name}":`, `Daily cost cap (USD) for "${g.name}":`), g.budget);
    if (v !== null) act({ do: "group_budget", jid: b.dataset.gbudget, budget: v }, L("תקרת השימוש עודכנה", "Cap updated")).then(() => setOpen && renderSettings()); });
  const save = restart => {
    const vals = {}; body.querySelectorAll("[data-set]").forEach(x => vals[x.dataset.set] = x.type === "checkbox" ? x.checked : x.value);
    act({ do: "settings_save", values: vals, restart }, restart ? L("נשמר — הסוכן עולה מחדש", "Saved — restarting") : L("נשמר (בתוקף אחרי הפעלה מחדש)", "Saved (after restart)"))
      .then(j => { if (j && !j.error) { setDirty = false; $("setDirtyNote").textContent = ""; } });
  };
  const s1 = body.querySelector("#setSave"), s2 = body.querySelector("#setSaveRestart");
  if (s1) s1.onclick = () => save(false); if (s2) s2.onclick = () => save(true);
}

// ---- computers card: this machine + peers, with stop / start / restart routed to each ----
function renderMachLine() {
  if (!peersInfo) return;
  const me = peersInfo.self || {}, items = [{ name: me.machine || L("כאן", "here"), online: true, b: me }].concat((peersInfo.peers || []).map(p => ({ name: p.name, online: p.online, b: p.brief || {} })));
  const d = (ok, t) => `<span class="ml-dot ${ok ? "ok" : "bad"}" title="${t}"></span>`;
  $("machLine").innerHTML = items.map(m => { const br = (m.b || {}).bridge || {};
    return `<span class="ml-item">🖥️ ${esc(m.name)} ${m.online ? d(br.connected && !br.error, L("גשר", "bridge")) + d(m.b.agent_alive !== false, L("סוכן", "agent")) : `<span class="muted">${L("לא מחובר", "offline")}</span>`}</span>`; }).join(" ");
}
function renderMachines() {
  renderMachLine();
  const el = $("machines"); if (!el || !peersInfo) return;
  const me = peersInfo.self || {}, list = [{ peer: null, name: L("המחשב הזה", "This computer") + (me.machine ? ` (${me.machine})` : ""), online: true, b: me }]
    .concat((peersInfo.peers || []).map(p => ({ peer: p.i, name: p.name, online: p.online, b: p.brief || {}, tunnel: p.tunnel, ssh: p.ssh || p.tunnel, err: p.error })));
  const dot = (ok, t) => `<span class="schip ${ok ? "ok" : "bad"}"><span class="dot"></span>${t}</span>`;
  el.innerHTML = list.map(m => {
    const b = m.b || {}, br = b.bridge || {}, sv = b.services || {}, brOk = m.online && br.connected && !br.error;
    const tunSt = m.peer === null ? "" : (((peersInfo.self || {}).tunnels || {})[m.name] || {}).state;
    return `<div class="mach${m.online ? "" : " off"}"><div class="mach-head"><strong>🖥️ ${esc(m.name)}</strong><span class="muted">${m.online ? "v" + esc(b.tool_version || "?") : L("לא מחובר", "offline")}</span></div>
      <div class="mach-st">${m.online ? dot(brOk, L("גשר", "Bridge")) + dot(b.agent_alive, L("סוכן", "Agent")) + (b.listen_paused ? `<span class="schip">⏸</span>` : "") : `<span class="muted">${esc(m.err || "")}</span>`}
        ${m.peer !== null && m.tunnel ? `<span class="schip ${tunSt === "up" ? "ok" : "bad"}" title="${L("מנהרת ssh", "ssh tunnel")}">⇄ ${esc(tunSt || "—")}</span>` : ""}</div>
      <div class="row tight">${m.online ? `
        <button class="btn small" data-svc="bridge" data-op="${sv.bridge_loop === false && !br.connected ? "start" : "stop"}" data-m="${m.peer ?? ""}">${sv.bridge_loop === false && !br.connected ? L("▶ הפעל גשר", "▶ Start bridge") : L("⏹ עצור גשר", "⏹ Stop bridge")}</button>
        <button class="btn small" data-rst="bridge" data-m="${m.peer ?? ""}">↻ ${L("גשר", "Bridge")}</button>
        <button class="btn small" data-svc="agent" data-op="stop" data-m="${m.peer ?? ""}">⏹ ${L("עצור סוכן", "Stop agent")}</button>
        <button class="btn small" data-rst="agent" data-m="${m.peer ?? ""}">↻ ${L("סוכן", "Agent")}</button>` :
        (m.ssh ? `<button class="btn small primary" data-sshstart="${m.peer}" title="${L("מפעיל את הסוכן במחשב השני דרך ssh", "Starts the agent on the other computer over ssh")}">▶ ${L("הפעל סוכן", "Start agent")}</button>` :
          `<span class="muted">${L("להפעלה מכאן: הוסף שם ssh למחשב בהגדרות", "To start it from here: add an ssh alias in Settings")}</span>`)}</div></div>`;
  }).join("");
  const peerOf = b => b.dataset.m === "" ? null : +b.dataset.m;
  el.querySelectorAll("[data-svc]").forEach(b => b.onclick = () => {
    const what = b.dataset.svc, op = b.dataset.op, p = peerOf(b);
    const warn = what === "agent" && op === "stop" ? L("\nשים לב: הסוכן מארח את לוח הבקרה בדפדפן (8770) ואת הגישה מהמחשב השני. אפליקציית הלוח ממשיכה לעבוד; הפעלה מחדש: מכאן (▶) או \"הפעל שם (ssh)\".", "\nNote: the agent hosts the browser dashboard and remote access.") : "";
    if (!confirm(L(`${op === "stop" ? "לעצור" : "להפעיל"} את ${what === "bridge" ? "הגשר" : "הסוכן"}?${op === "stop" ? " הוא לא יעלה לבד עד שתפעיל אותו." : ""}`, `${op} the ${what}?`) + warn)) return;
    act({ do: "service", what, op }, L("בוצע", "Done"), p).then(() => setTimeout(load, 3000));
  });
  el.querySelectorAll("[data-rst]").forEach(b => b.onclick = () => {
    if (confirm(L("להפעיל מחדש? כ-20 שניות.", "Restart? About 20 seconds."))) act({ do: "restart", what: b.dataset.rst }, L("מופעל מחדש…", "Restarting…"), peerOf(b));
  });
  el.querySelectorAll("[data-sshstart]").forEach(b => b.onclick = () => act({ do: "peer_ssh_start", i: +b.dataset.sshstart }, L("ההפעלה נשלחה דרך ssh", "Start sent over ssh"), null).then(() => setTimeout(load, 15000)));
}

// voice preview in the settings drawer: plays a short sample in the selected voice (generated on this machine)
document.addEventListener("click", async e => {
  const b = e.target.closest && e.target.closest("[data-voicetest]");
  if (!b) return;
  const sel = b.parentElement && b.parentElement.querySelector('select[data-set="voice_name"]');
  b.disabled = true;
  try {
    const j = await post({ do: "voice_test", voice: sel ? sel.value : "" });
    if (j.url) new Audio(j.url).play(); else alert(j.error || "error");
  } finally { b.disabled = false; }
});

// 🛞 permanent read-aloud toggle (per conversation)
document.addEventListener("change", e => {
  const c = e.target.closest && e.target.closest("[data-readall]");
  if (c) post({ do: "read_all", chat: curChat || "", on: c.checked });
});

// ---- 🤖 automations tab (owner 10.11.2026): every automation — on/off, parameters, task tree, performance, event log ----
// Loaded on demand from /action {do:"automations"}; each card saves only its own parameters (server: read-modify-write
// of the plugin's JSON + a .bak-ui copy). Cards being edited are never overwritten by a refresh.
let auData = null, auFilter = "all", auTimer = null;
const auOpen = new Set(["lunch:params"]);
const AU_DAYS = () => [[6, L("א׳", "Sun")], [0, L("ב׳", "Mon")], [1, L("ג׳", "Tue")], [2, L("ד׳", "Wed")], [3, L("ה׳", "Thu")], [4, L("ו׳", "Fri")], [5, L("ש׳", "Sat")]];
const AU_DAY_NAMES = () => ({ 6: L("ראשון", "Sunday"), 0: L("שני", "Monday"), 1: L("שלישי", "Tuesday"), 2: L("רביעי", "Wednesday"), 3: L("חמישי", "Thursday"), 4: L("שישי", "Friday"), 5: L("שבת", "Saturday") });
const auDirty = () => !!document.querySelector("#autoPane .au-card.dirty");
const AU_BAD = /FAILED|failed|נכשל|error|שגיאה/;

function auInput(a, p) {
  const id = `au-${a.id}-${p.path.replace(/[^\w]/g, "_")}`, dis = p.readonly ? " disabled" : "", v = p.value;
  const attrs = `id="${id}" data-ap="${esc(p.path)}" data-type="${p.type}"${dis}`;
  let ctl;
  if (p.type === "bool") ctl = `<input type="checkbox" ${attrs}${v ? " checked" : ""}>`;
  else if (p.type === "time") ctl = `<input type="time" ${attrs} value="${esc(v)}">`;
  else if (p.type === "date") ctl = `<input type="date" ${attrs} value="${esc(v)}">`;
  else if (p.type === "int") ctl = `<input type="number" step="1" ${attrs} value="${esc(v)}"${p.min != null ? ` min="${p.min}"` : ""}${p.max != null ? ` max="${p.max}"` : ""} style="width:90px">`;
  else if (p.type === "choice") ctl = `<select class="btn small sel" ${attrs}>${(p.options || []).map(o => `<option${o === v ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  else if (p.type === "weekday") ctl = `<select class="btn small sel" ${attrs}>${AU_DAYS().map(([n]) => `<option value="${n}"${n === v ? " selected" : ""}>${esc(AU_DAY_NAMES()[n])}</option>`).join("")}</select>`;
  else if (p.type === "weekdays") ctl = `<span class="au-days" ${attrs}>${AU_DAYS().map(([n, t]) => `<label class="chk"><input type="checkbox" data-day="${n}"${(v || []).includes(n) ? " checked" : ""}${dis}> ${t}</label>`).join("")}</span>`;
  else if (p.type === "text" || p.type === "phone") ctl = `<input type="text" ${attrs} value="${esc(v)}" class="au-text" dir="auto">`;
  else {
    const lines = String(v ?? "").split("\n").length;
    ctl = `<textarea ${attrs} rows="${Math.min(14, Math.max(p.type === "textarea" ? 4 : 2, lines + 1))}" dir="auto" class="au-area">${esc(v)}</textarea>`;
  }
  const badges = (p.is_default ? `<span class="au-badge" title="${L("לא מוגדר בקובץ — ערך ברירת המחדל של התוסף", "Not set in the file — the plugin default")}">${L("ברירת מחדל", "default")}</span>` : "") +
    (p.restart ? `<span class="au-badge warn" title="${L("נכנס לתוקף אחרי הפעלה מחדש של הסוכן", "Takes effect after an agent restart")}">↻</span>` : "");
  const help = [p.help, p.names ? "👤 " + p.names : "", p.readonly ? L("לשינוי: הגדרות ← תזמונים", "Change it in Settings → Schedules") : ""].filter(Boolean).map(esc).join(" · ");
  const wide = ["textarea", "list", "dates", "map", "maplist", "items"].includes(p.type);
  return `<div class="set-row au-row${wide ? " wide" : ""}"><span><label for="${id}">${esc(p.label)} ${badges}</label>${help ? `<small class="help">${help}</small>` : ""}</span><span class="set-ctl">${ctl}</span></div>`;
}

function auCard(a) {
  const sec = (key, title, body) => `<details class="au-sec" data-sec="${a.id}:${key}"${auOpen.has(a.id + ":" + key) ? " open" : ""}><summary>${title}</summary>${body}</details>`;
  const toggles = a.toggles.map(t => `<label class="chk au-tog" title="${t.restart ? L("נכנס לתוקף אחרי הפעלה מחדש", "After a restart") : L("נכנס לתוקף מיד", "Immediate")}">
      <input type="checkbox" data-autog="${a.id}:${t.idx}"${t.on ? " checked" : ""}> ${esc(t.label)}${t.restart ? " ↻" : ""}</label>`).join("");
  const last = a.last_event ? `${esc(fmt(a.last_event.at))} · <bdi>${esc(a.last_event.text.slice(0, 110))}</bdi>` : L("אין אירועים ביומן", "No events in the log");
  const editable = a.params.some(p => !p.readonly);
  const params = a.params.length ? a.params.map(p => auInput(a, p)).join("") +
    (editable ? `<div class="row tight"><button type="button" class="btn small primary" data-ausave="${a.id}">💾 ${L("שמור", "Save")}</button>
      <button type="button" class="btn small ghost" data-aureset="${a.id}">${L("בטל שינויים", "Discard changes")}</button><span class="note au-dirty-note"></span></div>` : "")
    : `<p class="note tight">${L("אין פרמטרים לעריכה.", "No editable parameters.")}</p>`;
  const perf = a.perf.length ? `<dl class="kv">${a.perf.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : `<p class="note tight">—</p>`;
  const log = a.log.length ? `<ul class="au-log">${a.log.map(e => `<li class="${AU_BAD.test(e.text) ? "bad" : ""}"><time>${esc(fmt(e.at))}</time> <span class="au-src" title="${e.src === "actions" ? "actions.log" : "agent.log"}">${e.src === "actions" ? "📒" : "🤖"}</span> <span dir="auto">${esc(e.text)}</span></li>`).join("")}</ul>`
    : `<p class="note tight">${L("אין אירועים.", "No events.")}</p>`;
  return `<article class="au-card item ${a.on ? "ok-i" : "bad-i"}" data-au="${a.id}">
    <header class="au-head"><span class="au-ico" aria-hidden="true">${a.icon}</span>
      <span class="au-title"><strong>${esc(a.name)}</strong><small class="muted">${esc(a.desc)}</small></span>
      <span class="schip ${a.on ? "ok" : "bad"}"><span class="dot"></span>${a.on ? L("פעיל", "On") : L("כבוי", "Off")}</span>
      ${a.fails_today ? `<span class="schip bad" title="${L("אירועי כשל ביומן היום", "Failures in today's log")}">⚠️ ${a.fails_today}</span>` : ""}
      ${a.pending_restart ? `<span class="chip" title="${L("נכנס לתוקף אחרי הפעלה מחדש", "Takes effect after a restart")}">↻ ${L("ממתין להפעלה מחדש", "Restart pending")}</span>` : ""}</header>
    ${toggles ? `<div class="au-togs">${toggles}</div>` : ""}
    ${a.file_ok ? "" : `<p class="note tight bad-t">⚠️ ${L("קובץ ההגדרות לא נמצא במחשב הזה: ", "Settings file not found on this computer: ")}${esc(a.file)}</p>`}
    <p class="note tight au-last">🕘 ${last}</p>
    ${sec("params", `⚙️ ${L("פרמטרים", "Parameters")} (${a.params.length})`, params)}
    ${sec("tree", `🌳 ${L("עץ משימות", "Task tree")}`, `<ol class="au-tree">${a.tree.map(t => `<li>${esc(t)}</li>`).join("")}</ol>`)}
    ${sec("perf", `📈 ${L("ביצועים", "Performance")}`, perf)}
    ${sec("log", `📜 ${L("יומן אירועים", "Event log")} (${a.log.length})`, log)}
  </article>`;
}

function renderAuto(full) {
  const pane = $("autoPane"); if (!pane || !auData) return;
  if (auData.error) {
    pane.innerHTML = `<p class="note">${esc(auData.error === "unknown action" ? L("הלוח במחשב הזה עוד לא תומך באוטומציות (צריך עדכון).", "This computer's dashboard does not support automations yet (needs an update).") : auData.error)}</p>`;
    return;
  }
  const items = auData.items || [], groups = [...new Set(items.map(a => a.group))];
  const shown = items.filter(a => auFilter === "all" || a.group === auFilter), nOn = items.filter(a => a.on).length;
  if (full || !pane.querySelector(".au-list")) {
    pane.innerHTML = `<div class="au-top">
        <span class="au-count"><b>${items.length}</b> ${L("אוטומציות", "automations")} · ${nOn} ${L("פעילות", "on")} · ${items.length - nOn} ${L("כבויות", "off")}</span>
        <span class="au-filters" role="group" aria-label="${L("סינון", "Filter")}">${[["all", L("הכול", "All")], ...groups.map(g => [g, g])].map(([k, t]) => `<button type="button" class="btn small${auFilter === k ? " primary" : ""}" data-aufilter="${esc(k)}">${esc(t)}</button>`).join("")}</span>
        <button type="button" class="btn small" id="auRefresh">⟳ ${L("רענן", "Refresh")}</button></div>
      <div class="au-restart" id="auRestart" hidden></div>
      <p class="note tight help">${L("שינוי טקסטים ושעות של מקטע D ושל תזכורת הרכש נכנס לתוקף מיד (התוספים קוראים את הקובץ בכל ריצה). פרמטר או מתג עם ↻ נכנס לתוקף רק אחרי הפעלה מחדש של הסוכן. לפני כל שמירה נשמר עותק ‎.bak-ui של הקובץ.", "Segment D texts/times and the procurement reminder apply at once. Parameters or switches marked ↻ apply after an agent restart. A .bak-ui copy is kept before every save.")}</p>
      <div class="au-list"></div>`;
  } else {
    const cnt = pane.querySelector(".au-count");
    if (cnt) cnt.innerHTML = `<b>${items.length}</b> ${L("אוטומציות", "automations")} · ${nOn} ${L("פעילות", "on")} · ${items.length - nOn} ${L("כבויות", "off")}`;
  }
  const rs = $("auRestart"), pend = auData.pending_restart || [];
  rs.hidden = !pend.length;
  rs.innerHTML = pend.length ? `<span>↻ ${L("נכנס לתוקף אחרי הפעלה מחדש", "Takes effect after a restart")}: <b>${esc(pend.join(", "))}</b></span><button type="button" class="btn small primary" id="auRestartBtn">↻ ${L("הפעל את הסוכן מחדש", "Restart the agent")}</button>` : "";
  const list = pane.querySelector(".au-list"), keep = {};
  list.querySelectorAll(".au-card").forEach(c => { if (c.classList.contains("dirty") || c.contains(document.activeElement)) keep[c.dataset.au] = c; });
  list.innerHTML = shown.map(a => keep[a.id] ? `<div data-keep="${a.id}"></div>` : auCard(a)).join("") || empty(L("אין", "None"));
  list.querySelectorAll("[data-keep]").forEach(ph => ph.replaceWith(keep[ph.dataset.keep]));
}

async function loadAuto(full) {
  try { auData = await post({ do: "automations" }); } catch (e) { auData = { error: L("טעינת האוטומציות נכשלה", "Loading automations failed") }; }
  renderAuto(full);
}
function auStartTimer() {
  clearInterval(auTimer);
  auTimer = setInterval(() => {
    if (!setOpen || setTab !== "auto") return clearInterval(auTimer);
    if (!document.hidden) loadAuto(false);
  }, 30000);
}
function auCollect(card) {
  const vals = {};
  card.querySelectorAll("[data-ap]").forEach(x => {
    if (x.hasAttribute("disabled")) return;
    const t = x.dataset.type, k = x.dataset.ap;
    if (t === "weekdays") vals[k] = [...x.querySelectorAll("[data-day]")].filter(c => c.checked).map(c => +c.dataset.day);
    else if (t === "bool") vals[k] = x.checked;
    else if (t === "weekday") vals[k] = +x.value;
    else vals[k] = x.value;
  });
  return vals;
}
function auMarkDirty(card, on) {
  card.classList.toggle("dirty", on);
  const n = card.querySelector(".au-dirty-note"); if (n) n.textContent = on ? L("● יש שינויים שלא נשמרו", "● Unsaved changes") : "";
}
function auRestartNote(j) { if (j && j.restart) setTimeout(() => toast(L("נכנס לתוקף אחרי הפעלה מחדש — הכפתור למעלה", "Takes effect after a restart — button above")), 1800); }

(function wireAuto() {
  const pane = $("autoPane"); if (!pane) return;
  const dirty = e => { const c = e.target.closest(".au-card"); if (c && e.target.closest("[data-ap]")) auMarkDirty(c, true); };
  pane.addEventListener("input", dirty);
  pane.addEventListener("change", dirty);
  pane.addEventListener("toggle", e => { const d = e.target; if (d.dataset && d.dataset.sec) { if (d.open) auOpen.add(d.dataset.sec); else auOpen.delete(d.dataset.sec); } }, true);
  pane.addEventListener("click", async e => {
    const b = e.target.closest("button, input[data-autog]"); if (!b) return;
    if (b.dataset.aufilter) { auFilter = b.dataset.aufilter; renderAuto(true); return; }
    if (b.id === "auRefresh") { loadAuto(false); return; }
    if (b.id === "auRestartBtn") {
      if (!confirm(L("להפעיל את הסוכן מחדש עכשיו? הוא עולה תוך כחצי דקה ומודיע בקבוצה.", "Restart the agent now? Back in about 30 seconds."))) return;
      const j = await post({ do: "auto_restart" });
      toast(j.error ? L("שגיאה: ", "Error: ") + j.error : L("הסוכן יעלה מחדש תוך רגע", "The agent restarts shortly"));
      return;
    }
    if (b.dataset.autog) {
      const [id, idx] = b.dataset.autog.split(":"), a = (auData.items || []).find(x => x.id === id), t = a && a.toggles[+idx];
      const verb = b.checked ? L("להפעיל", "Switch on") : L("לכבות", "Switch off");
      if (!confirm(`${verb}: ${t ? t.label : ""}\n${a ? a.name : ""}${t && t.restart ? "\n" + L("נכנס לתוקף אחרי הפעלה מחדש של הסוכן.", "Takes effect after an agent restart.") : ""}`)) { b.checked = !b.checked; return; }
      const j = await post({ do: "auto_toggle", id, idx: +idx, on: b.checked });
      if (j.error) { toast(L("שגיאה: ", "Error: ") + j.error); b.checked = !b.checked; return; }
      toast(b.checked ? L("הופעל", "Switched on") : L("כובה", "Switched off")); auRestartNote(j);
      loadAuto(false);
      return;
    }
    if (b.dataset.aureset) { auMarkDirty(b.closest(".au-card"), false); b.blur(); loadAuto(false); return; }
    if (b.dataset.ausave) {
      const c = b.closest(".au-card"); b.disabled = true;
      try {
        const j = await post({ do: "auto_save", id: b.dataset.ausave, values: auCollect(c) });
        if (j.error) { toast(L("שגיאה: ", "Error: ") + j.error); return; }
        toast(j.changed && j.changed.length ? L("נשמר: ", "Saved: ") + j.changed.join(", ") : L("אין שינויים", "No changes"));
        auRestartNote(j); auMarkDirty(c, false); b.blur(); loadAuto(false);
      } finally { b.disabled = false; }
    }
  });
})();
