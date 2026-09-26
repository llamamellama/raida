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
      await api.sendMessage(sessionId(), content, els.readyOnly.checked, els.fullText.checked);
      els.instruction.value = "";
      els.instruction.dispatchEvent(new Event("input")); // clears the skill line and menu
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
    el.className = `message ${m.role} ${m.status}${m.skill ? " has-skill" : ""}`;
    if (m.role === "user") {
      if (!m.skill) { el.textContent = m.content; return; }
      // A skill run: the command as typed, and the instruction it expanded to.
      el.innerHTML = `<div class="skill-line"><span class="pill skill-pill" title="${escapeAttr(m.skill.description)}">skill: ${escapeHtml(m.skill.title)}</span></div>`
        + `<div class="user-text">${escapeHtml(m.content)}</div>`
        + (m.prompt ? `<details class="skill-prompt"><summary>Instruction sent to the model</summary><pre>${escapeHtml(m.prompt)}</pre></details>` : "");
      return;
    }
    const head = [];
    if (m.status === "waiting_for_sources") head.push('<span class="pill">waiting for sources to finish</span>');
    if (m.status === "pending") head.push('<span class="pill">queued</span>');
    if (m.status === "streaming") head.push('<span class="pill">writing</span>');
    if (m.skill) head.push(`<span class="pill skill-pill" title="${escapeAttr(m.skill.description)}">skill: ${escapeHtml(m.skill.title)}</span>`);
    if (m.strategy) head.push(`<span class="pill" title="${escapeAttr(STRATEGY_HINTS[m.strategy] || "")}">${STRATEGY_LABELS[m.strategy] || m.strategy}</span>`);
    const u = m.token_usage || {};
    if (u.prompt_tokens) {
      const cached = u.cached_tokens ? ` (${u.cached_tokens.toLocaleString()} already read)` : "";
      head.push(`<span class="pill">${u.prompt_tokens.toLocaleString()} in${cached} / ${(u.completion_tokens || 0).toLocaleString()} out</span>`);
    }
    if (m.status === "cancelled") head.push('<span class="pill">cancelled</span>');
    const progress = state.progress.get(m.id);
    const artifacts = [...state.artifacts.values()].filter((a) => a.message_id === m.id);
    const body = m.content ? renderer.render(m.content) : "";
    el.innerHTML = `
      <div class="message-head"><span>Raida</span>${head.join("")}</div>
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

const STRATEGY_LABELS = { single_shot: "full text", notes: "from notes", map_reduce: "condensed then synthesized" };
const STRATEGY_HINTS = {
  single_shot: "Every source was read in full.",
  notes: "Long sources were read through the notes taken when they were added, plus passages found for this question.",
  map_reduce: "Sources were condensed for this instruction, then synthesized.",
};

function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escapeAttr(s) { return escapeHtml(s); }
