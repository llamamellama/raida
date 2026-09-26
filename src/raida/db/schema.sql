-- raida schema, version 2. Applied to fresh databases via PRAGMA user_version; existing
-- databases are upgraded step by step in Database.migrate (v2 added sessions.title_auto).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    title_auto  INTEGER NOT NULL DEFAULT 1,  -- 1 until the user renames the session
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    original_name   TEXT NOT NULL,
    stored_path     TEXT NOT NULL,
    managed         INTEGER NOT NULL DEFAULT 1,
    sha256          TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_sources_session ON sources(session_id);

CREATE TABLE IF NOT EXISTS messages (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role                 TEXT NOT NULL,
    content              TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL,
    strategy             TEXT,
    run_with_ready_only  INTEGER NOT NULL DEFAULT 0,
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
