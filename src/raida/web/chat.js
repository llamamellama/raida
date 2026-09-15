import { api } from "./api.js";
import { toast } from "./util.js";

const renderer = { render(md) { return DOMPurify.sanitize(marked.parse(md || "", { gfm: true, breaks: false })); } };

export function initChat(store, els) {
  const sessionId = () => store.state.sessionId;
  let renderTimer = null;

  els.composer.addEventListener("submit", async (e) => {
    e.preventDefault();
    const content = els.instruction.value.trim();
    if (!content || !sessionId()) return;
    els.send.disabled = true;
    try {
      await api.sendMessage(sessionId(), content, els.readyOnly.checked);
      els.instruction.value = "";
    } catch (err) { toast(err.message, true); } finally { els.send.disabled = false; }
  });
  els.instruction.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); els.composer.requestSubmit(); }
  });
  els.cancelRun.addEventListener("click", async () => {
    const running = [...store.state.messages.values()].find((m) => m.role === "assistant" && ["pending", "waiting_for_sources", "streaming"].includes(m.status));
    if (running) { try { await api.cancelMessage(running.id); } catch (err) { toast(err.message, true); } }
  });

  function scheduleRender(state) {
    if (renderTimer) return;
    renderTimer = setTimeout(() => { renderTimer = null; render(state); }, 60);
  }

  function render(state) {
    const list = els.messageList;
    const messages = [...state.messages.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
    els.chatEmpty.hidden = messages.length > 0;
    const running = messages.some((m) => m.role === "assistant" && ["pending", "waiting_for_sources", "streaming"].includes(m.status));
    els.cancelRun.hidden = !running;
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    const seen = new Set();
    for (const m of messages) {
      seen.add(m.id);
      let el = list.querySelector(`[data-id="${m.id}"]`);
      if (!el) { el = document.createElement("article"); el.dataset.id = m.id; list.appendChild(el); }
      renderMessage(el, m, state);
    }
    for (const el of [...list.querySelectorAll("article[data-id]")]) if (!seen.has(el.dataset.id)) el.remove();
    if (atBottom) list.scrollTop = list.scrollHeight;
  }

  function renderMessage(el, m, state) {
    el.className = `message ${m.role} ${m.status}`;
    if (m.role === "user") { el.textContent = m.content; return; }
    const head = [];
    if (m.status === "waiting_for_sources") head.push('<span class="pill">waiting for sources to finish</span>');
    if (m.status === "pending") head.push('<span class="pill">queued</span>');
    if (m.status === "streaming") head.push('<span class="pill">writing</span>');
    if (m.strategy) head.push(`<span class="pill">${m.strategy === "map_reduce" ? "condensed then synthesized" : "single pass"}</span>`);
    if (m.token_usage?.prompt_tokens) head.push(`<span class="pill">${m.token_usage.prompt_tokens.toLocaleString()} in / ${(m.token_usage.completion_tokens || 0).toLocaleString()} out</span>`);
    if (m.status === "cancelled") head.push('<span class="pill">cancelled</span>');
    const progress = state.progress.get(m.id);
    const artifacts = [...state.artifacts.values()].filter((a) => a.message_id === m.id);
    const body = m.content ? renderer.render(m.content) : "";
    el.innerHTML = `
      <div class="message-head"><span>raida</span>${head.join("")}</div>
      <div class="message-body">${body}</div>
      ${progress && m.status !== "done" ? `<div class="message-progress">${escapeHtml(progress)}</div>` : ""}
      ${m.error ? `<div class="message-error">${escapeHtml(m.error)}</div>` : ""}
      ${m.status === "done" ? `
        <div class="export-bar">
          <span class="label">Export</span>
          ${["txt", "md", "pdf", "docx"].map((f) => `<button class="btn btn-sm" data-export="${f}">.${f}</button>`).join("")}
          <button class="btn btn-sm btn-quiet" data-copy="1">Copy</button>
        </div>
        <div class="artifact-links">${artifacts.map((a) => `<a href="/api/artifacts/${a.id}" download="${escapeAttr(a.filename)}">${escapeHtml(a.filename)}</a>`).join("")}</div>` : ""}`;
    el.querySelectorAll("[data-export]").forEach((btn) => btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const artifact = await api.exportMessage(m.id, btn.dataset.export);
        window.location.assign(`/api/artifacts/${artifact.id}`);
      } catch (err) { toast(err.message, true); } finally { btn.disabled = false; }
    }));
    const copy = el.querySelector("[data-copy]");
    if (copy) copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(m.content); toast("Copied as Markdown"); } catch (_) { toast("Copy failed", true); }
    });
  }

  store.subscribe(scheduleRender);
}

function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escapeAttr(s) { return escapeHtml(s); }
