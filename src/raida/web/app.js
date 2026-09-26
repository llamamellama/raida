import { api } from "./api.js";
import { initChat } from "./chat.js";
import { initSources } from "./sources.js";
import { initStatus } from "./status.js";
import { createStore } from "./store.js";
import { toast } from "./util.js";

const $ = (id) => document.getElementById(id);
const store = createStore();
let eventSource = null;

const els = {
  dropzone: $("dropzone"), fileInput: $("file-input"), pickFiles: $("pick-files"), addByPath: $("add-by-path"),
  defaultLanguage: $("default-language"), uploadProgress: $("upload-progress"), sourceList: $("source-list"),
  summary: $("sources-summary"), pathDialog: $("path-dialog"), pathForm: $("path-form"), pathInput: $("path-input"),
  pathCancel: $("path-cancel"), textDialog: $("text-dialog"), textDialogTitle: $("text-dialog-title"),
  textDialogBody: $("text-dialog-body"), textDialogMd: $("text-dialog-md"), textClose: $("text-close"),
  addFromLibrary: $("add-from-library"), libraryDialog: $("library-dialog"), libraryForm: $("library-form"),
  libraryList: $("library-list"), libraryCancel: $("library-cancel"), libraryEmpty: $("library-empty"),
  composer: $("composer"), instruction: $("instruction"), readyOnly: $("ready-only"), fullText: $("full-text"), send: $("send"),
  cancelRun: $("cancel-run"), messageList: $("message-list"), chatEmpty: $("chat-empty"),
  sessionSelect: $("session-select"), newSession: $("new-session"), renameSession: $("rename-session"),
  deleteSession: $("delete-session"), statusStrip: $("status-strip"),
};

initSources(store, els);
initChat(store, els);
initStatus(store, els.statusStrip);

function connect(sessionId) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/sessions/${sessionId}/events`);
  eventSource.addEventListener("snapshot", (e) => store.loadSnapshot(JSON.parse(e.data)));
  for (const type of ["source.updated", "source.removed", "job.progress", "message.updated", "message.delta", "message.progress", "artifact.created", "session.updated", "system.status"]) {
    eventSource.addEventListener(type, (e) => store.apply(type, JSON.parse(e.data)));
  }
  eventSource.onerror = () => { /* EventSource reconnects on its own; the snapshot resyncs state. */ };
}

async function selectSession(id) {
  store.state.sessionId = id;
  localStorage.setItem("raida.session", id);
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
  // Sessions are named automatically after their first answer; Rename is there for later.
  const s = await api.createSession();
  await refreshSessions();
  await selectSession(s.id);
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
  if (!confirm("Delete this session, its sources and its answers?")) return;
  await api.deleteSession(store.state.sessionId);
  localStorage.removeItem("raida.session");
  await boot();
});
store.subscribe((state) => {
  if (state.session && state.sessions.length) {
    const s = state.sessions.find((x) => x.id === state.session.id);
    if (s && s.title !== state.session.title) { s.title = state.session.title; renderSessionOptions(); }
  }
});

async function boot() {
  try {
    await refreshSessions();
    let id = localStorage.getItem("raida.session");
    if (!id || !store.state.sessions.some((s) => s.id === id)) {
      id = store.state.sessions[0]?.id || (await api.createSession()).id;
      await refreshSessions();
    }
    await selectSession(id);
    api.health().then((h) => store.apply("system.status", h)).catch(() => {});
  } catch (err) {
    toast(`Could not reach the raida server: ${err.message}`, true);
  }
}

boot();
