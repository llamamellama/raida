-- raida schema, version 6. Applied to fresh databases via PRAGMA user_version; existing
-- databases are upgraded step by step in Database.migrate (v2 added sessions.title_auto,
-- v3 added messages.full_text and the notes table, v4 messages.skill and messages.prompt,
-- v5 turned per-session source rows into one library with session_sources, ADR-0007,
-- v6 added sources.title, the name the user can give a file).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    title_auto  INTEGER NOT NULL DEFAULT 1,  -- 1 until the user renames the session
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- The library: one row per file (content hash), with its processing state. Sessions use files
-- through session_sources; deleting a session leaves its files here for the others.
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    original_name   TEXT NOT NULL,             -- the file's own name
    title           TEXT NOT NULL DEFAULT '',  -- the name shown and given to the model
    stored_path     TEXT NOT NULL,
    managed         INTEGER NOT NULL DEFAULT 1,
    sha256          TEXT NOT NULL UNIQUE,
    size_bytes      INTEGER NOT NULL,
    language        TEXT NOT NULL DEFAULT 'auto',
    status          TEXT NOT NULL,
    progress        REAL NOT NULL DEFAULT 0,
    error           TEXT,
    processed_path  TEXT,
    token_estimate  INTEGER,
    meta            TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_sources (
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, source_id)
);
CREATE INDEX IF NOT EXISTS idx_session_sources_source ON session_sources(source_id);

CREATE TABLE IF NOT EXISTS messages (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role                 TEXT NOT NULL,
    content              TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL,
    strategy             TEXT,
    run_with_ready_only  INTEGER NOT NULL DEFAULT 0,
    full_text            INTEGER NOT NULL DEFAULT 0,
    skill                TEXT,               -- JSON SkillUse when the message invoked a skill
    prompt               TEXT,               -- the rendered skill instruction (user message)
    token_usage          TEXT NOT NULL DEFAULT '{}',
    error                TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    source_id       TEXT REFERENCES sources(id) ON DELETE CASCADE,
    message_id      TEXT REFERENCES messages(id) ON DELETE CASCADE,
    stage           TEXT NOT NULL,
    resource_class  TEXT NOT NULL,
    state           TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    format      TEXT NOT NULL,
    path        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_cache (
    cache_key       TEXT PRIMARY KEY,
    sha256          TEXT NOT NULL,
    processed_path  TEXT NOT NULL,
    token_estimate  INTEGER NOT NULL,
    meta            TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS condensations (
    cache_key         TEXT PRIMARY KEY,
    sha256            TEXT NOT NULL,
    instruction_hash  TEXT NOT NULL,
    model             TEXT NOT NULL,
    text              TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

-- Notes taken on a processed document when it became ready; keyed by processed-document key,
-- model and notes version, so every session using the file shares them.
CREATE TABLE IF NOT EXISTS notes (
    cache_key       TEXT PRIMARY KEY,
    sha256          TEXT NOT NULL,
    model           TEXT NOT NULL,
    overview        TEXT NOT NULL,
    sections        TEXT NOT NULL DEFAULT '[]',
    token_estimate  INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);
