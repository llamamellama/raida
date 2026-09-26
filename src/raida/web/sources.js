// The Sources tab: adding files, the files this session uses, and the library of every file
// added in any session (ADR-0007). Library files are processed once; adding one to a session
// reuses its text and notes.
import { api, uploadFiles } from "./api.js";
import { LANGUAGES, formatBytes, formatDuration, toast } from "./util.js";

const STAGE_LABELS = {
  queued: "Queued", extracting: "Extracting text", transcoding: "Decoding audio",
  detecting_language: "Detecting language", transcribing: "Transcribing", rendering: "Rendering pages",
  ocr: "Recognizing text (OCR)", noting: "Taking notes", ready: "Ready", failed: "Failed", cancelled: "Cancelled",
};
const ACTIVE = new Set(["queued", "extracting", "transcoding", "detecting_language", "transcribing", "rendering", "ocr", "noting"]);
const FILTER_FROM = 6; // library files outside the session before the filter box appears
const PENCIL = '<svg class="rename-icon" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M11 2.5l2.5 2.5L6 12.5 3 13l.5-3z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';

export function initSources(store, els) {
  const langSelect = els.defaultLanguage;
  for (const [code, label] of LANGUAGES) {
    const opt = document.createElement("option");
    opt.value = code; opt.textContent = label; langSelect.appendChild(opt);
  }

  const sessionId = () => store.state.sessionId;
  const source = (id) => store.state.library.get(id);

  async function upload(files) {
    if (!files.length || !sessionId()) return;
    const accepted = [], rejected = [];
    for (const f of files) (isSupported(f.name) ? accepted : rejected).push(f);
    if (rejected.length) toast(`Skipped unsupported: ${rejected.map((f) => f.name).join(", ")}`, true);
    if (!accepted.length) return;
    els.uploadProgress.hidden = false;
    els.uploadProgress.innerHTML = `<span>Uploading ${accepted.length} file(s)</span><div class="bar"><div></div></div>`;
    const bar = els.uploadProgress.querySelector(".bar > div");
    try {
      await uploadFiles(sessionId(), accepted, langSelect.value, (p) => { bar.style.width = `${Math.round(p * 100)}%`; });
    } catch (err) {
      toast(err.message, true);
    } finally {
      els.uploadProgress.hidden = true;
    }
  }

  // Drag and drop + picker
  const dz = els.dropzone;
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("dragover"); }));
  dz.addEventListener("drop", (e) => upload([...e.dataTransfer.files]));
  document.body.addEventListener("dragover", (e) => e.preventDefault());
  document.body.addEventListener("drop", (e) => { e.preventDefault(); if (!dz.contains(e.target)) upload([...e.dataTransfer.files]); });
  dz.addEventListener("keydown", (e) => { if (e.target === dz && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); els.fileInput.click(); } });
  els.pickFiles.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", () => { upload([...els.fileInput.files]); els.fileInput.value = ""; });

  // Add by path
  els.addByPath.addEventListener("click", () => { els.pathInput.value = ""; els.pathDialog.showModal(); });
  els.pathCancel.addEventListener("click", () => els.pathDialog.close());
  els.pathForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const paths = els.pathInput.value.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!paths.length) return;
    try {
      await api.addByPath(sessionId(), paths, langSelect.value);
      els.pathDialog.close();
    } catch (err) { toast(err.message, true); }
  });
  els.textClose.addEventListener("click", () => els.textDialog.close());

  async function showText(s) {
    const text = await api.sourceText(s.id);
    els.textDialogTitle.textContent = s.title;
    els.textDialogBody.textContent = text;
    els.textDialogBody.hidden = false; els.textDialogMd.hidden = true;
    els.textDialog.showModal();
  }

  async function showNotes(s) {
    const md = await api.sourceNotes(s.id);
    els.textDialogTitle.textContent = `Notes: ${s.title}`;
    els.textDialogMd.innerHTML = DOMPurify.sanitize(marked.parse(md, { gfm: true }));
    els.textDialogBody.hidden = true; els.textDialogMd.hidden = false;
    els.textDialog.showModal();
  }

  async function deleteFromLibrary(s) {
    const used = s.sessions === 1 ? "the session that uses it" : `the ${s.sessions} sessions that use it`;
    const where = s.sessions ? ` It is removed from ${used}.` : "";
    const kept = s.managed ? "Its processed text, notes and uploaded copy are deleted."
      : "Its processed text and notes are deleted; the file itself stays where it is.";
    if (!confirm(`Delete "${s.title}" from the library?${where} ${kept}`)) return;
    await api.deleteSource(s.id);
    toast(`Deleted ${s.title}`);
  }

  // One handler per list; items are redrawn in place, so listeners on them would be lost.
  async function act(button, action, s) {
    button.disabled = true;
    try {
      if (action === "view") await showText(s);
      else if (action === "notes") await showNotes(s);
      else if (action === "cancel") await api.cancelSource(s.id);
      else if (action === "retry") await api.retrySource(s.id);
      else if (action === "remove") await api.stopUsingSource(sessionId(), s.id);
      else if (action === "add") await api.useSource(sessionId(), s.id);
      else if (action === "delete") await deleteFromLibrary(s);
    } catch (err) { toast(err.message, true); } finally { button.disabled = false; }
  }
  for (const list of [els.sourceList, els.libraryList]) {
    list.addEventListener("click", (e) => {
      const button = e.target.closest("button[data-action]");
      const li = button?.closest("[data-id]");
      const s = li && source(li.dataset.id);
      if (!s) return;
      if (button.dataset.action === "rename") startRename(li, s);
      else act(button, button.dataset.action, s);
    });
  }

  // Renaming, in the library for every session: the name becomes a text field. Enter or
  // leaving the field saves, Escape cancels, and an empty name brings back the file's name.
  // The item is not redrawn while it is being edited.
  function startRename(li, s) {
    const name = li.querySelector(".source-name");
    if (!name || li.dataset.editing) return;
    li.dataset.editing = "1";
    const input = document.createElement("input");
    input.className = "source-rename";
    input.value = s.title;
    input.placeholder = s.original_name;
    input.maxLength = 200;
    input.setAttribute("aria-label", `New name for ${s.title}; leave it empty for the file's own name`);
    name.replaceWith(input);
    input.focus();
    input.select();
    let finished = false;
    async function finish(save) {
      if (finished) return;
      finished = true;
      const title = input.value.trim();
      let renamed = null;
      if (save && title !== s.title) {
        try { renamed = await api.renameSource(s.id, title); } catch (err) { toast(err.message, true); }
      }
      delete li.dataset.editing;
      li.dataset.sig = ""; // draw the item again, with the new name or the old one
      if (renamed) store.apply("source.updated", renamed); // before the event, so no flash
      else redraw();
    }
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
  }
  els.sourceList.addEventListener("change", async (e) => {
    if (e.target.dataset.action !== "language") return;
    const s = source(e.target.closest("[data-id]").dataset.id);
    try { if (s) await api.setLanguage(s.id, e.target.value); } catch (err) { toast(err.message, true); }
  });
  els.libraryFilter.addEventListener("input", () => renderLibrary(store.state));

  // -- rendering ---------------------------------------------------------------------------

  // Keyed lists: an item is redrawn only when its source changed, and moved only when the
  // order did, so a button or menu under the pointer is not replaced while an answer streams.
  function sync(list, sources, draw) {
    const seen = new Set();
    for (const s of sources) {
      seen.add(s.id);
      let li = list.querySelector(`:scope > [data-id="${s.id}"]`);
      if (!li) { li = document.createElement("li"); li.dataset.id = s.id; list.appendChild(li); }
      const sig = JSON.stringify(s);
      if (li.dataset.sig !== sig && !li.dataset.editing) { draw(li, s); li.dataset.sig = sig; }
    }
    for (const li of [...list.children]) if (!seen.has(li.dataset.id)) li.remove();
    const order = sources.map((s) => s.id);
    if ([...list.children].some((li, i) => li.dataset.id !== order[i])) {
      for (const id of order) list.appendChild(list.querySelector(`:scope > [data-id="${id}"]`));
    }
  }

  function renderSession(state) {
    const sources = store.sessionSources();
    const ready = sources.filter((s) => s.status === "ready").length;
    const active = sources.filter((s) => ACTIVE.has(s.status)).length;
    const tokens = sources.reduce((n, s) => n + (s.token_estimate || 0), 0);
    els.summary.textContent = sources.length
      ? `${ready}/${sources.length} ready${active ? `, ${active} processing` : ""}${tokens ? `, ~${tokens.toLocaleString()} tokens` : ""}`
      : "";
    els.tabSourcesCount.textContent = sources.length ? String(sources.length) : "";
    els.sessionSourcesEmpty.hidden = sources.length > 0 || !state.session;
    sync(els.sourceList, sources, renderItem);
  }

  function renderLibrary(state) {
    const inSession = new Set(state.sessionSources);
    const others = [...state.library.values()].filter((s) => !inSession.has(s.id))
      .sort((a, b) => (b.last_used_at || b.created_at).localeCompare(a.last_used_at || a.created_at));
    const q = els.libraryFilter.value.trim().toLowerCase();
    const shown = q ? others.filter((s) => `${s.title}\n${s.original_name}`.toLowerCase().includes(q)) : others;
    els.libraryFilter.hidden = others.length <= FILTER_FROM && !q;
    els.librarySummary.textContent = others.length ? `${others.length} other file${others.length === 1 ? "" : "s"}` : "";
    const empty = !state.library.size ? "Nothing here yet. Files you add in any session appear here, ready for every other session."
      : !others.length ? "Every file in the library is in this session."
        : !shown.length ? `No file matches "${els.libraryFilter.value.trim()}".` : "";
    els.libraryEmpty.textContent = empty;
    els.libraryEmpty.hidden = !empty;
    sync(els.libraryList, shown, renderLibraryItem);
  }

  function describe(s) {
    const meta = [];
    if (s.meta?.duration_s) meta.push(formatDuration(s.meta.duration_s));
    if (s.meta?.pages) meta.push(`${s.meta.pages} pages`);
    meta.push(formatBytes(s.size_bytes));
    return meta;
  }

  // The stage, and on a session card the file's language beside it; a bar while processing.
  function statusLine(s, extra = "") {
    const pct = Math.round((s.progress || 0) * 100);
    const active = ACTIVE.has(s.status);
    return `<div class="source-state"><span class="source-status ${s.status}">${STAGE_LABELS[s.status] || s.status}${active && pct ? ` · ${pct}%` : ""}</span>${extra}</div>`
      + (active ? `<div class="progress ${pct ? "" : "indeterminate"}"><div style="width:${pct}%"></div></div>` : "");
  }

  // The name is a button that starts renaming; the tooltip has the full name and, once the
  // file is renamed, the file's own name.
  function head(s) {
    const file = s.title === s.original_name ? "" : `\nFile: ${s.original_name}`;
    return `<div class="source-head">
        <span class="source-kind">${s.kind}</span>
        <button class="source-name" type="button" data-action="rename" title="${escapeAttr(`${s.title}${file}\nClick to rename`)}">
          <span class="source-name-text">${escapeHtml(s.title)}</span>${PENCIL}
        </button>
      </div>`;
  }

  function renderItem(li, s) {
    li.className = "source-item";
    const active = ACTIVE.has(s.status);
    const meta = describe(s);
    if (s.meta?.detected_language) meta.push(`lang: ${s.meta.detected_language}`);
    if (s.meta?.transcriber) meta.push(s.meta.transcriber);
    if (s.meta?.ocr) meta.push("OCR");
    if (s.token_estimate) meta.push(`~${s.token_estimate.toLocaleString()} tok`);
    const notes = s.meta?.notes;
    if (notes?.status === "done") meta.push(`notes ~${(notes.tokens || 0).toLocaleString()} tok`);
    if (s.sessions > 1) meta.push(`in ${s.sessions} sessions`);
    const language = `<select data-action="language" aria-label="Language of ${escapeAttr(s.title)}" title="Language of the file. Changing it for a recording or a scanned PDF processes it again, for every session that uses it.">${LANGUAGES.map(([c, l]) => `<option value="${c}" ${c === s.language ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    li.innerHTML = `${head(s)}
      <div class="source-meta">${meta.map(escapeHtml).join(" · ")}</div>
      ${statusLine(s, language)}
      ${s.error ? `<div class="source-error">${escapeHtml(s.error)}</div>` : ""}
      ${notes?.status === "failed" && !active ? `<div class="source-warning">No notes (${escapeHtml(notes.error || "failed")}). Answers read this source in full, which is slower; Re-run to try again.</div>` : ""}
      <div class="source-actions">
        ${s.status === "ready" ? `<button class="btn btn-sm btn-quiet" type="button" data-action="view">View text</button>` : ""}
        ${s.status === "ready" && notes?.status === "done" ? `<button class="btn btn-sm btn-quiet" type="button" data-action="notes">View notes</button>` : ""}
        ${active ? `<button class="btn btn-sm btn-quiet" type="button" data-action="cancel">Cancel</button>` : `<button class="btn btn-sm btn-quiet" type="button" data-action="retry">Re-run</button>`}
        <button class="btn btn-sm btn-quiet push" type="button" data-action="remove" title="Take it out of this session. It stays in the library.">Remove</button>
      </div>`;
  }

  function renderLibraryItem(li, s) {
    li.className = "library-item";
    const meta = describe(s);
    if (s.language !== "auto") meta.push(LANGUAGES.find(([c]) => c === s.language)?.[1] || s.language);
    if (s.meta?.notes?.status === "done") meta.push("noted");
    meta.push(s.sessions ? `in ${s.sessions} session${s.sessions === 1 ? "" : "s"}` : "in no session");
    li.innerHTML = `${head(s)}
      <div class="source-meta">${meta.map(escapeHtml).join(" · ")}</div>
      ${s.status === "ready" ? "" : statusLine(s)}
      <div class="source-actions">
        <button class="btn btn-sm" type="button" data-action="add" title="Use this file in this session. It is not processed again.">Add to session</button>
        ${s.status === "ready" ? `<button class="btn btn-sm btn-quiet" type="button" data-action="view">View text</button>` : ""}
        <button class="btn btn-sm btn-quiet btn-danger push" type="button" data-action="delete" title="Delete from the library and from every session that uses it">Delete</button>
      </div>`;
  }

  let shownRev = -1;
  function render(state) {
    if (state.sourcesRev === shownRev) return;
    shownRev = state.sourcesRev;
    renderSession(state);
    renderLibrary(state);
  }
  function redraw() { shownRev = -1; render(store.state); }
  store.subscribe(render);
}

const SUPPORTED = new Set([".txt", ".md", ".markdown", ".text", ".pdf", ".docx", ".srt", ".vtt", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aiff", ".aif", ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg"]);
function isSupported(name) { const i = name.lastIndexOf("."); return i >= 0 && SUPPORTED.has(name.slice(i).toLowerCase()); }
function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escapeAttr(s) { return escapeHtml(s); }
