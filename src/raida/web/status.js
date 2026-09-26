// The status strip: one chip per component. A working component shows a short name (the full
// detail is in the tooltip) so the strip fits beside the session controls; a failing one shows
// what is wrong.
const SHORT = {
  llm: (s) => s.extra?.model || s.detail,
  transcriber: (s) => s.detail.split(" via ")[0],
  ocr: (s) => s.detail.split(" via ")[0],
  pdf_renderer: (s) => s.extra?.renderer || s.detail,
};

export function initStatus(store, el) {
  function chip(label, status, key) {
    const cls = status ? (status.ok ? "ok" : "fail") : "";
    const detail = status ? status.detail : "checking";
    const text = status?.ok ? SHORT[key](status) : detail;
    return `<span class="status-chip ${cls}" title="${escapeAttr(`${label}: ${detail}`)}"><span class="chip-label">${label}</span><span class="chip-detail">${escapeHtml(text)}</span></span>`;
  }
  let shown; // the store notifies on every streamed word; redraw only when health changes
  store.subscribe((state) => {
    const h = state.health;
    if (h === shown) return;
    shown = h;
    if (!h) { el.innerHTML = '<span class="status-chip">checking environment</span>'; return; }
    el.innerHTML = [
      chip("model", h.llm, "llm"),
      chip("speech", h.transcriber, "transcriber"),
      chip("OCR", h.ocr, "ocr"),
      chip("PDF", h.pdf_renderer, "pdf_renderer"),
    ].join("");
  });
}
function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escapeAttr(s) { return escapeHtml(s); }
