export function initStatus(store, el) {
  function chip(label, status) {
    const cls = status ? (status.ok ? "ok" : "fail") : "";
    const detail = status ? status.detail : "checking";
    return `<span class="status-chip ${cls}" title="${escapeAttr(detail)}">${label}: ${escapeHtml(shorten(detail))}</span>`;
  }
  store.subscribe((state) => {
    const h = state.health;
    if (!h) { el.innerHTML = '<span class="status-chip">checking environment</span>'; return; }
    el.innerHTML = [
      chip("model", h.llm),
      chip("speech", h.transcriber),
      chip("ocr", h.ocr),
      chip("pdf", h.pdf_renderer),
    ].join("");
  });
}
function shorten(s) { s = String(s || ""); return s.length > 48 ? s.slice(0, 46) + "…" : s; }
function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escapeAttr(s) { return escapeHtml(s); }
