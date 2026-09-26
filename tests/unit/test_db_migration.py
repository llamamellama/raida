"""Schema upgrades of an existing database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from raida.db import Database
from raida.db.repo import SCHEMA_VERSION


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_fresh_database_is_at_current_version(tmp_path: Path) -> None:
    db = Database(tmp_path / "fresh.sqlite3")
    try:
        assert "title_auto" in _columns(tmp_path / "fresh.sqlite3", "sessions")
        session = db.create_session()
        assert session.title_auto is True
        assert session.title.startswith("Session ")
        named = db.create_session("My notes")
        assert named.title_auto is False
    finally:
        db.close()


def test_version_1_database_gains_title_auto(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    db = Database(path)
    old = db.create_session("Old session")
    db.close()
    # Rewind to the version 1 shape: no title_auto column, user_version 1.
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE sessions DROP COLUMN title_auto")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    assert "title_auto" not in _columns(path, "sessions")

    upgraded = Database(path)
    try:
        assert "title_auto" in _columns(path, "sessions")
        assert upgraded._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        # Existing sessions keep their title and are treated as automatically named.
        session = upgraded.get_session(old.id)
        assert session.title == "Old session"
        assert session.title_auto is True
    finally:
        upgraded.close()


_V4 = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL,
    title_auto INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE sources (id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, original_name TEXT NOT NULL, stored_path TEXT NOT NULL,
    managed INTEGER NOT NULL DEFAULT 1, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    language TEXT NOT NULL DEFAULT 'auto', status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
    error TEXT, processed_path TEXT, token_estimate INTEGER, meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX idx_sources_session ON sources(session_id);
CREATE TABLE jobs (id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES sources(id) ON DELETE CASCADE, message_id TEXT,
    stage TEXT NOT NULL, resource_class TEXT NOT NULL, state TEXT NOT NULL, started_at TEXT,
    finished_at TEXT, error TEXT, created_at TEXT NOT NULL);
PRAGMA user_version = 4;
"""


def test_version_4_sources_become_one_library_shared_by_sessions(tmp_path: Path) -> None:
    """Before v5 every session had its own row per file. The rows of one file collapse into a
    library entry carrying the state of its ready row updated last; each session keeps using
    what it had, and jobs follow the entry."""
    path = tmp_path / "v4.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(_V4)
    for sid in ("s1", "s2", "s3"):
        conn.execute("INSERT INTO sessions VALUES (?, ?, 1, 't0', 't0')", (sid, sid.upper()))
    rows = [  # id, session, sha, status, processed path, created, updated
        ("r0", "s1", "a", "ready", "/p/a0.md", "t1", "t1"),  # the same file twice in s1
        ("r1", "s1", "a", "ready", "/p/a1.md", "t2", "t3"),
        ("r2", "s2", "a", "failed", None, "t4", "t9"),  # updated last, but not ready
        ("r3", "s2", "a", "ready", "/p/a3.md", "t5", "t6"),
        ("r4", "s1", "b", "cancelled", None, "t2", "t7"),
        ("r5", "s3", "b", "queued", None, "t3", "t8"),
    ]
    for rid, sid, sha, status, processed, created, updated in rows:
        conn.execute(
            "INSERT INTO sources (id, session_id, kind, original_name, stored_path, sha256, "
            "size_bytes, status, processed_path, created_at, updated_at) "
            "VALUES (?, ?, 'text', ?, '/u/x', ?, 1, ?, ?, ?, ?)",
            (rid, sid, f"{sha}.md", sha, status, processed, created, updated),
        )
    conn.executemany(
        "INSERT INTO jobs (id, source_id, stage, resource_class, state, created_at) "
        "VALUES (?, ?, 'extracting', 'cpu', 'done', ?)",
        [("j1", "r2", "t4"), ("j2", "r3", "t5")],
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        assert db._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "session_id" not in _columns(path, "sources")
        library = {s.sha256: s for s in db.list_library()}
        assert set(library) == {"a", "b"}
        a, b = library["a"], library["b"]
        assert (a.id, a.status, a.processed_path) == ("r3", "ready", "/p/a3.md")
        assert a.created_at == "t1"  # when the file first came in
        assert (a.title, b.title) == ("a.md", "b.md")  # v6: named after the file until renamed
        assert (b.id, b.status) == ("r5", "queued")
        assert [s.id for s in db.list_sources("s1")] == ["r3", "r5"]
        assert [s.id for s in db.list_sources("s2")] == ["r3"]
        assert [s.id for s in db.list_sources("s3")] == ["r5"]
        assert (a.sessions, b.sessions) == (2, 2)
        assert {j.id for j in db.list_jobs("r3")} == {"j1", "j2"}
        assert db._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # A deleted session's files stay in the library.
        db.delete_session("s1")
        assert db.get_source("r3").sessions == 1 and db.get_source("r5").sessions == 1
    finally:
        db.close()
    Database(path).close()  # a second open finds nothing left to migrate


def test_version_5_sources_gain_a_title(tmp_path: Path) -> None:
    path = tmp_path / "v5.sqlite3"
    db = Database(path)
    session = db.create_session()
    db.close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE sources DROP COLUMN title")
    conn.execute(
        "INSERT INTO sources (id, kind, original_name, stored_path, sha256, size_bytes, status, "
        "created_at, updated_at) VALUES ('s', 'text', 'talk.md', '/u/talk.md', 'h', 1, 'ready', "
        "'t0', 't0')"
    )
    conn.execute(
        "INSERT INTO session_sources (session_id, source_id, added_at) VALUES (?, 's', 't0')",
        (session.id,),
    )
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()
    db = Database(path)
    try:
        assert db.get_source("s").title == "talk.md"
        assert db.list_sources(session.id)[0].title == "talk.md"
        renamed = db.update_source("s", title="Talk")
        assert renamed.title == "Talk" and renamed.original_name == "talk.md"
    finally:
        db.close()


def test_version_2_database_gains_full_text_and_notes(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite3"
    db = Database(path)
    db.close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE messages DROP COLUMN full_text")
    conn.execute("DROP TABLE notes")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    db = Database(path)
    try:
        assert "full_text" in _columns(path, "messages")
        assert {"cache_key", "overview", "sections"} <= _columns(path, "notes")
        assert db.get_notes("missing") is None
    finally:
        db.close()
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()
