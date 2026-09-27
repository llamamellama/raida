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
  // A library file joins or leaves a session; it stays in the library either way.
  useSource: (sessionId, sourceId) => request("PUT", `/api/sessions/${sessionId}/sources/${sourceId}`),
  stopUsingSource: (sessionId, sourceId) => request("DELETE", `/api/sessions/${sessionId}/sources/${sourceId}`),
  getSession: (id) => request("GET", `/api/sessions/${id}`),
  renameSession: (id, title) => request("PATCH", `/api/sessions/${id}`, { title }),
  deleteSession: (id) => request("DELETE", `/api/sessions/${id}`),
  addByPath: (sessionId, paths, language) =>
    request("POST", `/api/sessions/${sessionId}/sources/by-path`, { paths, language }),
  sourceText: (id) => request("GET", `/api/sources/${id}/text`, undefined, { text: true }),
  sourceNotes: (id) => request("GET", `/api/sources/${id}/notes`, undefined, { text: true }),
  setLanguage: (id, language) => request("PATCH", `/api/sources/${id}`, { language }),
  // The name shown everywhere and given to the model; "" brings back the file's own name.
  renameSource: (id, title) => request("PATCH", `/api/sources/${id}`, { title }),
  retrySource: (id) => request("POST", `/api/sources/${id}/retry`),
  cancelSource: (id) => request("POST", `/api/sources/${id}/cancel`),
  // Deletes the file from the library and from every session that uses it.
  deleteSource: (id) => request("DELETE", `/api/sources/${id}`),
  sendMessage: (sessionId, content, runWithReadyOnly, fullText) =>
    request("POST", `/api/sessions/${sessionId}/messages`, { content, run_with_ready_only: runWithReadyOnly, full_text: fullText }),
  cancelMessage: (id) => request("POST", `/api/messages/${id}/cancel`),
  exportMessage: (id, format) => request("POST", `/api/messages/${id}/exports`, { format }),
  listSkills: () => request("GET", "/api/skills"),
  getSkill: (name) => request("GET", `/api/skills/${encodeURIComponent(name)}`),
  createSkill: (skill) => request("POST", "/api/skills", skill),
  updateSkill: (name, skill) => request("PUT", `/api/skills/${encodeURIComponent(name)}`, skill),
  // A built-in skill is deleted with any changes to it; restoreBuiltins brings them all back.
  deleteSkill: (name) => request("DELETE", `/api/skills/${encodeURIComponent(name)}`),
  resetSkill: (name) => request("POST", `/api/skills/${encodeURIComponent(name)}/reset`),
  restoreBuiltins: () => request("POST", "/api/skills/restore-builtins"),
  // Fetched, not navigated to, so an error is a message instead of a page replacing the app.
  exportSkill: async (name) => {
    const res = await fetch(`/api/skills/${encodeURIComponent(name)}/export`);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* not json */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    const url = URL.createObjectURL(await res.blob());
    const link = Object.assign(document.createElement("a"), { href: url, download: `${name}.zip` });
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  },
  // Nuke: deletes everything the user made. The server refuses it without the typed word.
  factoryReset: (confirm) => request("POST", "/api/reset", { confirm }),
  importSkill: async (file, replace) => {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch(`/api/skills/import?replace=${replace ? "true" : "false"}`, { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : res.statusText);
    return data;
  },
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
