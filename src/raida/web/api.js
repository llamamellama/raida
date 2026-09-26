// Thin fetch wrapper for the raida HTTP API.

async function request(method, url, body, opts = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  if (opts.text) return res.text();
  return res.json();
}

export const api = {
  health: () => request("GET", "/api/health"),
  listSessions: () => request("GET", "/api/sessions"),
  // Without a title the server names the session and renames it after the first answer.
  createSession: (title) => request("POST", "/api/sessions", title ? { title } : {}),
  listLibrary: () => request("GET", "/api/library"),
  addFromLibrary: (sessionId, sha256s, language) =>
    request("POST", `/api/sessions/${sessionId}/sources/from-library`, { sha256s, language: language || null }),
  getSession: (id) => request("GET", `/api/sessions/${id}`),
  renameSession: (id, title) => request("PATCH", `/api/sessions/${id}`, { title }),
  deleteSession: (id) => request("DELETE", `/api/sessions/${id}`),
  addByPath: (sessionId, paths, language) =>
    request("POST", `/api/sessions/${sessionId}/sources/by-path`, { paths, language }),
  sourceText: (id) => request("GET", `/api/sources/${id}/text`, undefined, { text: true }),
  sourceNotes: (id) => request("GET", `/api/sources/${id}/notes`, undefined, { text: true }),
  setLanguage: (id, language) => request("PATCH", `/api/sources/${id}`, { language }),
  retrySource: (id) => request("POST", `/api/sources/${id}/retry`),
  cancelSource: (id) => request("POST", `/api/sources/${id}/cancel`),
  deleteSource: (id) => request("DELETE", `/api/sources/${id}`),
  sendMessage: (sessionId, content, runWithReadyOnly, fullText) =>
    request("POST", `/api/sessions/${sessionId}/messages`, { content, run_with_ready_only: runWithReadyOnly, full_text: fullText }),
  cancelMessage: (id) => request("POST", `/api/messages/${id}/cancel`),
  exportMessage: (id, format) => request("POST", `/api/messages/${id}/exports`, { format }),
};

// Uploads use XMLHttpRequest because fetch has no upload progress events.
export function uploadFiles(sessionId, files, language, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/sessions/${sessionId}/sources?language=${encodeURIComponent(language)}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        let detail = xhr.statusText;
        try { detail = JSON.parse(xhr.responseText).detail || detail; } catch (_) { /* ignore */ }
        reject(new Error(typeof detail === "string" ? detail : JSON.stringify(detail)));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed (network)"));
    xhr.send(form);
  });
}
