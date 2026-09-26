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
