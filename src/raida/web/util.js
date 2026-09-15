export const LANGUAGES = [
  ["auto", "Auto-detect"], ["en", "English"], ["de", "German"], ["fr", "French"], ["es", "Spanish"],
  ["it", "Italian"], ["pt", "Portuguese"], ["nl", "Dutch"], ["sv", "Swedish"], ["da", "Danish"],
  ["fi", "Finnish"], ["nb", "Norwegian"], ["pl", "Polish"], ["cs", "Czech"], ["ru", "Russian"],
  ["uk", "Ukrainian"], ["el", "Greek"], ["hu", "Hungarian"], ["ro", "Romanian"], ["tr", "Turkish"],
  ["ja", "Japanese"], ["ko", "Korean"], ["zh", "Chinese"], ["ar", "Arabic"], ["hi", "Hindi"],
  ["he", "Hebrew"], ["id", "Indonesian"], ["vi", "Vietnamese"], ["th", "Thai"],
];

export function formatBytes(n) {
  if (!n && n !== 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v < 10 && i ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

export function formatDuration(s) {
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
}

let toastTimer = null;
export function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = `toast ${isError ? "error" : ""}`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6000 : 2500);
}
