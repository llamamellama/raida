import { api, uploadFiles } from "./api.js";
import { LANGUAGES, formatBytes, formatDuration, toast } from "./util.js";

const STAGE_LABELS = {
  queued: "Queued", extracting: "Extracting text", transcoding: "Decoding audio",
  detecting_language: "Detecting language", transcribing: "Transcribing", rendering: "Rendering pages",
  ocr: "Recognizing text (OCR)", ready: "Ready", failed: "Failed", cancelled: "Cancelled",
};
const ACTIVE = new Set(["queued", "extracting", "transcoding", "detecting_language", "transcribing", "rendering", "ocr"]);

export function initSources(store, els) {
  const langSelect = els.defaultLanguage;
  for (const [code, label] of LANGUAGES) {
    const opt = document.createElement("option");
    opt.value = code; opt.textContent = label; langSelect.appendChild(opt);
  }

  const sessionId = () => store.state.sessionId;

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
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); els.fileInput.click(); } });
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

  // List rendering
  function render(state) {
    const list = els.sourceList;
    const sources = [...state.sources.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
    const ready = sources.filter((s) => s.status === "ready").length;
    const active = sources.filter((s) => ACTIVE.has(s.status)).length;
    const tokens = sources.reduce((n, s) => n + (s.token_estimate || 0), 0);
    els.summary.textContent = sources.length
      ? `${ready}/${sources.length} ready${active ? `, ${active} processing` : ""}${tokens ? `, ~${tokens.toLocaleString()} tokens` : ""}`
      : "";
    const seen = new Set();
    for (const s of sources) {
      seen.add(s.id);
      let li = list.querySelector(`[data-id="${s.id}"]`);
      if (!li) { li = document.createElement("li"); li.className = "source-item"; li.dataset.id = s.id; list.appendChild(li); }
      renderItem(li, s);
    }
    for (const li of [...list.children]) if (!seen.has(li.dataset.id)) li.remove();
  }

  function renderItem(li, s) {
    const active = ACTIVE.has(s.status);
    const meta = [];
    meta.push(formatBytes(s.size_bytes));
    if (s.meta?.pages) meta.push(`${s.meta.pages} pages`);
    if (s.meta?.duration_s) meta.push(formatDuration(s.meta.duration_s));
    if (s.meta?.detected_language) meta.push(`lang: ${s.meta.detected_language}`);
    if (s.meta?.transcriber) meta.push(s.meta.transcriber);
    if (s.meta?.ocr) meta.push("OCR");
    if (s.meta?.cache_hit) meta.push("cached");
    if (s.token_estimate) meta.push(`~${s.token_estimate.toLocaleString()} tok`);
    const pct = Math.round((s.progress || 0) * 100);
    li.innerHTML = `
      <div class="source-head">
        <span class="source-kind">${s.kind}</span>
        <span class="source-name" title="${escapeAttr(s.original_name)}">${escapeHtml(s.original_name)}</span>
      </div>
      <div class="source-meta">${meta.map(escapeHtml).join(" · ")}</div>
      <div class="source-status ${s.status}">${STAGE_LABELS[s.status] || s.status}${active && pct ? ` · ${pct}%` : ""}</div>
      ${active ? `<div class="progress ${pct ? "" : "indeterminate"}"><div style="width:${pct}%"></div></div>` : ""}
      ${s.error ? `<div class="source-error">${escapeHtml(s.error)}</div>` : ""}
      <div class="source-actions">
        <select data-action="language" title="Language">${LANGUAGES.map(([c, l]) => `<option value="${c}" ${c === s.language ? "selected" : ""}>${l}</option>`).join("")}</select>
        ${s.status === "ready" ? `<button class="btn btn-sm btn-quiet" data-action="view">View text</button>` : ""}
        ${active ? `<button class="btn btn-sm btn-quiet" data-action="cancel">Cancel</button>` : `<button class="btn btn-sm btn-quiet" data-action="retry">Re-run</button>`}
        <button class="btn btn-sm btn-quiet btn-danger" data-action="remove">Remove</button>
      </div>`;
    li.querySelectorAll("[data-action]").forEach((el) => {
      const action = el.dataset.action;
      const handler = async () => {
        try {
          if (action === "view") {
            const text = await api.sourceText(s.id);
            els.textDialogTitle.textContent = s.original_name;
            els.textDialogBody.textContent = text;
            els.textDialog.showModal();
          } else if (action === "cancel") await api.cancelSource(s.id);
          else if (action === "retry") await api.retrySource(s.id);
          else if (action === "remove") await api.deleteSource(s.id);
          else if (action === "language") await api.setLanguage(s.id, el.value);
        } catch (err) { toast(err.message, true); }
      };
      el.addEventListener(action === "language" ? "change" : "click", handler);
    });
  }

  store.subscribe(render);
}

const SUPPORTED = new Set([".txt", ".md", ".markdown", ".text", ".pdf", ".docx", ".srt", ".vtt", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aiff", ".aif", ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg"]);
function isSupported(name) { const i = name.lastIndexOf("."); return i >= 0 && SUPPORTED.has(name.slice(i).toLowerCase()); }
function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escapeAttr(s) { return escapeHtml(s); }
