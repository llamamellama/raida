// Minimal observable store: one session's state, the shared library and health, updated by
// SSE events.

export function createStore() {
  const listeners = new Set();
  const state = {
    sessions: [],
    sessionId: null,
    session: null,
    library: new Map(),    // source id -> source: every file, whichever session added it
    sessionSources: [],    // ids of the library files this session uses, in the order added
    sourcesRev: 0,         // bumped when either changes, so views redraw only then
    messages: new Map(),
    artifacts: new Map(),
    progress: new Map(),   // message_id -> detail text
    health: null,
    skills: [],            // SkillInfo list, replaced as a whole on every change
    skillProblems: [],
  };
  const notify = () => listeners.forEach((fn) => fn(state));
  return {
    state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    notify,
    // The session's sources, as library entries.
    sessionSources() { return state.sessionSources.map((id) => state.library.get(id)).filter(Boolean); },
    loadSnapshot(snapshot) {
      state.session = snapshot.session;
      state.library = new Map((snapshot.library || []).map((s) => [s.id, s]));
      for (const s of snapshot.sources) state.library.set(s.id, s);
      state.sessionSources = snapshot.sources.map((s) => s.id);
      state.sourcesRev++;
      state.messages = new Map(snapshot.messages.map((m) => [m.id, m]));
      state.artifacts = new Map(snapshot.artifacts.map((a) => [a.id, a]));
      state.progress = new Map();
      notify();
    },
    apply(type, data) {
      switch (type) {
        case "source.updated": state.library.set(data.id, data); state.sourcesRev++; break;
        case "source.removed":
          state.library.delete(data.source_id);
          state.sessionSources = state.sessionSources.filter((id) => id !== data.source_id);
          state.sourcesRev++;
          break;
        case "session.sources":
          if (data.session_id !== state.session?.id) return;
          state.sessionSources = data.source_ids;
          state.sourcesRev++;
          break;
        case "job.progress": {
          const s = state.library.get(data.source_id);
          if (!s) return;
          state.library.set(s.id, { ...s, progress: data.progress, status: data.stage });
          state.sourcesRev++;
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
        case "skills.updated": state.skills = data.skills; state.skillProblems = data.problems || []; break;
        default: return;
      }
      notify();
    },
  };
}
