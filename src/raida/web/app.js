import { api } from "./api.js";
import { initChat } from "./chat.js";
import { initSkills } from "./skills.js";
import { initSources } from "./sources.js";
import { initStatus } from "./status.js";
import { createStore } from "./store.js";
import { toast } from "./util.js";

const $ = (id) => document.getElementById(id);
const store = createStore();
let eventSource = null;
// The version of the UI files this page was loaded with; the server sends its own on connect.
const UI_VERSION = document.querySelector('meta[name="raida-ui-version"]')?.content || "";

const els = {
  dropzone: $("dropzone"), fileInput: $("file-input"), pickFiles: $("pick-files"), addByPath: $("add-by-path"),
  defaultLanguage: $("default-language"), uploadProgress: $("upload-progress"), sourceList: $("source-list"),
  summary: $("sources-summary"), sessionSourcesEmpty: $("session-sources-empty"),
  libraryList: $("library-list"), libraryFilter: $("library-filter"), librarySummary: $("library-summary"),
  libraryEmpty: $("library-empty"), pathDialog: $("path-dialog"), pathForm: $("path-form"), pathInput: $("path-input"),
  pathCancel: $("path-cancel"), textDialog: $("text-dialog"), textDialogTitle: $("text-dialog-title"),
  textDialogBody: $("text-dialog-body"), textDialogMd: $("text-dialog-md"), textClose: $("text-close"),
  composer: $("composer"), instruction: $("instruction"), readyOnly: $("ready-only"), fullText: $("full-text"), send: $("send"),
  cancelRun: $("cancel-run"), messageList: $("message-list"), chatEmpty: $("chat-empty"),
  sessionSelect: $("session-select"), newSession: $("new-session"), renameSession: $("rename-session"),
  deleteSession: $("delete-session"), statusStrip: $("status-strip"),
  tabs: [$("tab-sources"), $("tab-skills")], tabSourcesCount: $("tab-sources-count"), tabSkillsCount: $("tab-skills-count"),
  skillMenu: $("skill-menu"), skillChip: $("skill-chip"), skillsList: $("skills-list"), skillsProblems: $("skills-problems"),
  skillNew: $("skill-new"), skillImport: $("skill-import"), skillsRestore: $("skills-restore"),
  openSettings: $("open-settings"), settingsDialog: $("settings-dialog"), settingsClose: $("settings-close"),
  settingsVersion: $("settings-version"), settingsModel: $("settings-model"), settingsDataDir: $("settings-data-dir"),
  nukeOpen: $("nuke-open"), nukeDialog: $("nuke-dialog"), nukeForm: $("nuke-form"), nukeInput: $("nuke-input"),
  nukeError: $("nuke-error"), nukeCancel: $("nuke-cancel"), nukeConfirm: $("nuke-confirm"),
  skillImportFile: $("skill-import-file"), skillEditor: $("skill-editor"), skillForm: $("skill-form"),
  skillFormTitle: $("skill-form-title"), skillFormNote: $("skill-form-note"),
  skillFormError: $("skill-form-error"), skillCancel: $("skill-cancel"), skillFormSize: $("skill-form-size"),
};

initSources(store, els);
// Before the chat: the @ menu's key handler must run ahead of the composer's.
const skills = initSkills(store, els);
initChat(store, els);
initStatus(store, els.statusStrip);

// -- sidebar tabs ------------------------------------------------------------------------

function selectTab(tab, focus = false) {
  for (const t of els.tabs) {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute("aria-controls")).hidden = !on;
  }
  if (focus) tab.focus();
  try { localStorage.setItem("raida.tab", tab.id); } catch (_) { /* storage unavailable */ }
}
els.tabs.forEach((tab, i) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    selectTab(els.tabs[(i + (e.key === "ArrowRight" ? 1 : els.tabs.length - 1)) % els.tabs.length], true);
  });
});
try { const saved = $(localStorage.getItem("raida.tab") || ""); if (els.tabs.includes(saved)) selectTab(saved); } catch (_) { /* storage unavailable */ }

// -- a tab left open while raida was updated ---------------------------------------------

// EventSource reconnects by itself after a server restart; if the server now serves other UI
// files, this page's code is out of date (it once kept asking for a session title long after
// that step was removed). Reload, keeping what was typed; once per version, in case a proxy
// or cache serves the old page again.
function checkVersion(serverVersion) {
  if (!UI_VERSION || !serverVersion || serverVersion === UI_VERSION) return;
  if (els.skillEditor.open) {
    // Reload when the editor closes, so an unsaved skill is not lost.
    toast("raida was updated; the page reloads when you close this skill.");
    els.skillEditor.addEventListener("close", () => checkVersion(serverVersion), { once: true });
    return;
  }
  try {
    if (sessionStorage.getItem("raida.reloadedFor") === serverVersion) {
      toast("raida was updated, but this page is still the old one: reload it with Cmd+Shift+R.", true);
      return;
    }
    sessionStorage.setItem("raida.reloadedFor", serverVersion);
    sessionStorage.setItem("raida.draft", els.instruction.value);
  } catch (_) { /* storage unavailable: reload anyway */ }
  location.reload();
}
try {
  const draft = sessionStorage.getItem("raida.draft");
  if (draft) { els.instruction.value = draft; els.instruction.dispatchEvent(new Event("input")); }
  sessionStorage.removeItem("raida.draft");
} catch (_) { /* storage unavailable */ }

// -- sessions ----------------------------------------------------------------------------

function connect(sessionId) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/sessions/${sessionId}/events`);
  eventSource.addEventListener("snapshot", (e) => store.loadSnapshot(JSON.parse(e.data)));
  eventSource.addEventListener("app.version", (e) => checkVersion(JSON.parse(e.data).ui));
  // Another tab nuked Raida: this page's session is gone. The tab that asked reloads itself.
  eventSource.addEventListener("app.reset", () => { if (!nuking) afterReset(null); });
  for (const type of ["source.updated", "source.removed", "session.sources", "job.progress", "message.updated", "message.delta", "message.progress", "artifact.created", "session.updated", "system.status", "skills.updated"]) {
    eventSource.addEventListener(type, (e) => store.apply(type, JSON.parse(e.data)));
  }
  eventSource.onerror = () => { /* EventSource reconnects on its own; the snapshot resyncs state. */ };
}

async function selectSession(id) {
  store.state.sessionId = id;
  try { localStorage.setItem("raida.session", id); } catch (_) { /* storage unavailable */ }
  els.sessionSelect.value = id;
  connect(id);
}

function renderSessionOptions() {
  const sel = els.sessionSelect;
  sel.innerHTML = "";
  for (const s of store.state.sessions) {
    const opt = document.createElement("option");
    opt.value = s.id; opt.textContent = s.title; sel.appendChild(opt);
  }
  if (store.state.sessionId) sel.value = store.state.sessionId;
}

async function refreshSessions() {
  store.state.sessions = await api.listSessions();
  renderSessionOptions();
}

els.sessionSelect.addEventListener("change", () => selectSession(els.sessionSelect.value));
els.newSession.addEventListener("click", async () => {
  // No title to confirm: the session is named after its first answer; Rename is there for later.
  try {
    const s = await api.createSession();
    await refreshSessions();
    await selectSession(s.id);
    els.instruction.focus();
  } catch (err) { toast(err.message, true); }
});
els.renameSession.addEventListener("click", async () => {
  const current = store.state.session?.title || "";
  const title = prompt("New title", current);
  if (!title || title === current) return;
  await api.renameSession(store.state.sessionId, title);
  await refreshSessions();
});
els.deleteSession.addEventListener("click", async () => {
  if (!store.state.sessionId) return;
  if (!confirm("Delete this session and its answers? Its files stay in the library for other sessions.")) return;
  await api.deleteSession(store.state.sessionId);
  try { localStorage.removeItem("raida.session"); } catch (_) { /* storage unavailable */ }
  await boot();
});
store.subscribe((state) => {
  if (state.session && state.sessions.length) {
    const s = state.sessions.find((x) => x.id === state.session.id);
    if (s && s.title !== state.session.title) { s.title = state.session.title; renderSessionOptions(); }
  }
});

// -- settings and Nuke -------------------------------------------------------------------

// Nuke sits behind Settings and a dialog that asks for the word typed out, so neither a stray
// click nor a quick Enter deletes anything.
const NUKE_WORD = "NUKE";
let nuking = false;

els.openSettings.addEventListener("click", () => {
  const h = store.state.health;
  els.settingsVersion.textContent = h?.version || "unknown";
  els.settingsModel.textContent = h?.llm?.extra?.model || h?.llm?.detail || "unknown";
  els.settingsDataDir.textContent = h?.data_dir || "unknown";
  els.settingsDialog.showModal();
});
els.settingsClose.addEventListener("click", () => els.settingsDialog.close());
els.nukeOpen.addEventListener("click", () => {
  els.settingsDialog.close();
  els.nukeForm.reset();
  els.nukeConfirm.disabled = true;
  els.nukeError.hidden = true;
  els.nukeDialog.showModal();
  els.nukeInput.focus();
});
els.nukeInput.addEventListener("input", () => {
  els.nukeConfirm.disabled = els.nukeInput.value.trim() !== NUKE_WORD;
});
els.nukeCancel.addEventListener("click", () => els.nukeDialog.close());
// Escape cannot dismiss the dialog while the reset runs; the page reloads when it is done.
els.nukeDialog.addEventListener("cancel", (e) => { if (nuking) e.preventDefault(); });
els.nukeForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (els.nukeInput.value.trim() !== NUKE_WORD) return;
  nuking = true;
  els.nukeConfirm.disabled = els.nukeCancel.disabled = els.nukeInput.disabled = true;
  els.nukeConfirm.textContent = "Nuking...";
  try {
    afterReset(await api.factoryReset(NUKE_WORD));
  } catch (err) {
    nuking = false;
    els.nukeError.textContent = err.message;
    els.nukeError.hidden = false;
    els.nukeConfirm.textContent = "Nuke";
    els.nukeCancel.disabled = els.nukeInput.disabled = false;
    els.nukeConfirm.disabled = false;
  }
});

function afterReset(summary) {
  // Factory settings in this browser too: forget the saved session, tab and drafts.
  try {
    for (const storage of [localStorage, sessionStorage]) {
      for (const key of Object.keys(storage)) if (key.startsWith("raida.")) storage.removeItem(key);
    }
    if (summary) sessionStorage.setItem("raida.nuked", JSON.stringify(summary));
  } catch (_) { /* storage unavailable */ }
  location.reload();
}

function reportReset() {
  let summary = null;
  try {
    summary = JSON.parse(sessionStorage.getItem("raida.nuked") || "null");
    sessionStorage.removeItem("raida.nuked");
  } catch (_) { /* storage unavailable */ }
  if (!summary) return;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  toast(`Raida is back to factory settings. Deleted ${plural(summary.sessions, "session")}, `
    + `${plural(summary.sources, "library file")} and ${plural(summary.skills, "skill")}.`);
}

async function boot() {
  try {
    await refreshSessions();
    let id = null;
    try { id = localStorage.getItem("raida.session"); } catch (_) { /* storage unavailable */ }
    if (!id || !store.state.sessions.some((s) => s.id === id)) {
      id = store.state.sessions[0]?.id || (await api.createSession()).id;
      await refreshSessions();
    }
    await selectSession(id);
    api.health().then((h) => store.apply("system.status", h)).catch(() => {});
    skills.refresh();
    reportReset();
  } catch (err) {
    toast(`Could not reach the raida server: ${err.message}`, true);
  }
}

boot();
