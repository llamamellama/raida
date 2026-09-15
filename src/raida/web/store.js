// Minimal observable store: one session's state plus health, updated by SSE events.

export function createStore() {
  const listeners = new Set();
  const state = {
    sessions: [],
    sessionId: null,
    session: null,
    sources: new Map(),
    messages: new Map(),
    artifacts: new Map(),
    progress: new Map(),   // message_id -> detail text
    health: null,
  };
  const notify = () => listeners.forEach((fn) => fn(state));
  return {
    state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    notify,
    loadSnapshot(snapshot) {
      state.session = snapshot.session;
      state.sources = new Map(snapshot.sources.map((s) => [s.id, s]));
      state.messages = new Map(snapshot.messages.map((m) => [m.id, m]));
      state.artifacts = new Map(snapshot.artifacts.map((a) => [a.id, a]));
      state.progress = new Map();
      notify();
    },
    apply(type, data) {
      switch (type) {
        case "source.updated": state.sources.set(data.id, data); break;
        case "source.removed": state.sources.delete(data.source_id); break;
        case "job.progress": {
          const s = state.sources.get(data.source_id);
          if (s) state.sources.set(s.id, { ...s, progress: data.progress, status: data.stage });
          break;
        }
        case "message.updated":
          state.messages.set(data.id, { ...(state.messages.get(data.id) || {}), ...data });
          if (["done", "failed", "cancelled"].includes(data.status)) state.progress.delete(data.id);
          break;
        case "message.delta": {
          const m = state.messages.get(data.message_id);
          if (m) state.messages.set(m.id, { ...m, content: (m.content || "") + data.text });
          break;
        }
        case "message.progress": state.progress.set(data.message_id, data.detail); break;
        case "artifact.created": state.artifacts.set(data.id, data); break;
        case "session.updated": state.session = data; break;
        case "system.status": state.health = data; break;
        default: return;
      }
      notify();
    },
  };
}
