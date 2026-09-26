"""SQLite persistence. Synchronous by design; async callers use ``asyncio.to_thread``.

One connection guarded by a re-entrant lock is enough for a single-user local app and keeps
transactions simple. WAL mode lets the SSE reader and writers coexist without blocking.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from raida.models import (
    SOURCE_COMPUTED_FIELDS,
    Artifact,
    Job,
    Message,
    ProcessedCacheEntry,
    Session,
    Source,
    SourceNotes,
    new_id,
    utc_now,
)

SCHEMA_VERSION = 6
_JSON_FIELDS = {"meta", "token_usage"}
_NULLABLE_JSON_FIELDS = {"skill"}
_BOOL_FIELDS = {"managed", "run_with_ready_only", "title_auto", "full_text"}

# A library source with how it is used, for every query that returns sources.
_SOURCE_COLUMNS = (
    "s.*, "
    "(SELECT COUNT(*) FROM session_sources u WHERE u.source_id = s.id) AS sessions, "
    "(SELECT MAX(u.added_at) FROM session_sources u WHERE u.source_id = s.id) AS last_used_at"
)
_LIBRARY_COLUMNS = (
    "id, kind, original_name, stored_path, managed, sha256, size_bytes, language, status, "
    "progress, error, processed_path, token_estimate, meta, created_at, updated_at"
)


class NotFoundError(LookupError):
    pass


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in _JSON_FIELDS & data.keys():
        data[key] = json.loads(data[key]) if data[key] else {}
    for key in _NULLABLE_JSON_FIELDS & data.keys():
        data[key] = json.loads(data[key]) if data[key] else None
    for key in _BOOL_FIELDS & data.keys():
        data[key] = bool(data[key])
    return data


def _encode(value: Any) -> Any:
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Path):
        return str(value)
    return value


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None, timeout=30
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self.migrate()

    @classmethod
    def in_memory(cls) -> Database:
        return cls(":memory:")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- schema ----------------------------------------------------------------------------

    def migrate(self) -> None:
        with self._lock:
            current = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if current >= SCHEMA_VERSION:
                return
            schema = resources.files("raida.db").joinpath("schema.sql").read_text("utf-8")
            if current < 1:
                self._conn.executescript(schema)
            else:
                if current < 2:
                    columns = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)")}
                    if "title_auto" not in columns:
                        self._conn.execute(
                            "ALTER TABLE sessions ADD COLUMN title_auto INTEGER NOT NULL DEFAULT 1"
                        )
                if current < 3:
                    columns = {r[1] for r in self._conn.execute("PRAGMA table_info(messages)")}
                    if "full_text" not in columns:
                        self._conn.execute(
                            "ALTER TABLE messages ADD COLUMN full_text INTEGER NOT NULL DEFAULT 0"
                        )
                    # Every statement is CREATE ... IF NOT EXISTS, so this adds only new tables.
                    self._conn.executescript(schema)
                if current < 4:
                    columns = {r[1] for r in self._conn.execute("PRAGMA table_info(messages)")}
                    for column in ("skill", "prompt"):
                        if column not in columns:
                            self._conn.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
                if current < 5:
                    self._share_sources()
                if current < 6:
                    columns = {r[1] for r in self._conn.execute("PRAGMA table_info(sources)")}
                    if "title" not in columns:
                        self._conn.execute(
                            "ALTER TABLE sources ADD COLUMN title TEXT NOT NULL DEFAULT ''"
                        )
                    self._conn.execute("UPDATE sources SET title = original_name WHERE title = ''")
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _share_sources(self) -> None:
        """v5 (ADR-0007): per-session source rows become one library row per file, and
        session_sources records which sessions use it. Of several rows for one file the ready
        one updated last carries the processing state; jobs follow it. The table is rebuilt the
        way SQLite documents for changes ALTER TABLE cannot make: new table, copy, drop, rename,
        with foreign keys off so the drop does not cascade."""
        columns = {r[1] for r in self._conn.execute("PRAGMA table_info(sources)")}
        if "session_id" not in columns:
            return
        rows = [dict(r) for r in self._conn.execute("SELECT * FROM sources ORDER BY created_at")]
        keep: dict[str, dict[str, Any]] = {}
        first_added: dict[str, str] = {}
        for row in rows:
            sha = row["sha256"]
            first_added.setdefault(sha, row["created_at"])
            best = keep.get(sha)
            rank = (row["status"] == "ready", row["updated_at"])
            if best is None or rank > (best["status"] == "ready", best["updated_at"]):
                keep[sha] = row
        names = _LIBRARY_COLUMNS.split(", ")
        library = [
            [first_added[sha] if n == "created_at" else row[n] for n in names]
            for sha, row in keep.items()
        ]
        uses = [(r["session_id"], keep[r["sha256"]]["id"], r["created_at"]) for r in rows]
        moved = [(keep[r["sha256"]]["id"], r["id"]) for r in rows if keep[r["sha256"]] is not r]
        self._conn.execute("PRAGMA foreign_keys = OFF")  # a no-op inside a transaction
        try:
            self._conn.execute("BEGIN")
            self._conn.execute(
                "CREATE TABLE sources_v5 ("
                "id TEXT PRIMARY KEY, kind TEXT NOT NULL, original_name TEXT NOT NULL, "
                "stored_path TEXT NOT NULL, managed INTEGER NOT NULL DEFAULT 1, "
                "sha256 TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL, "
                "language TEXT NOT NULL DEFAULT 'auto', status TEXT NOT NULL, "
                "progress REAL NOT NULL DEFAULT 0, error TEXT, processed_path TEXT, "
                "token_estimate INTEGER, meta TEXT NOT NULL DEFAULT '{}', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS session_sources ("
                "session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, "
                "source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE, "
                "added_at TEXT NOT NULL, PRIMARY KEY (session_id, source_id))"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_sources_source "
                "ON session_sources(source_id)"
            )
            marks = ", ".join("?" for _ in names)
            self._conn.executemany(
                f"INSERT INTO sources_v5 ({_LIBRARY_COLUMNS}) VALUES ({marks})", library
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO session_sources (session_id, source_id, added_at) "
                "VALUES (?, ?, ?)",
                uses,
            )
            self._conn.executemany("UPDATE jobs SET source_id = ? WHERE source_id = ?", moved)
            self._conn.execute("DROP TABLE sources")
            self._conn.execute("ALTER TABLE sources_v5 RENAME TO sources")
            problems = self._conn.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"sources migration left broken references: {problems[:5]}")
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")

    # -- generic helpers -------------------------------------------------------------------

    def _insert(self, table: str, data: dict[str, Any]) -> None:
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                [_encode(v) for v in data.values()],
            )

    def _update(self, table: str, row_id: str, fields: dict[str, Any]) -> int:
        if not fields:
            return 0
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                [*(_encode(v) for v in fields.values()), row_id],
            )
            return cur.rowcount

    def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return _row_to_dict(row) if row else None

    def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    # -- sessions --------------------------------------------------------------------------

    def create_session(self, title: str | None = None) -> Session:
        """A session named by the caller keeps that name; one created without a title gets a
        timestamp name and is renamed automatically after its first answer."""
        now = utc_now()
        auto = title is None or not title.strip()
        if auto:
            title = f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        session = Session(id=new_id(), title=title, title_auto=auto, created_at=now, updated_at=now)
        self._insert("sessions", session.model_dump())
        return session

    def get_session(self, session_id: str) -> Session:
        row = self._fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if row is None:
            raise NotFoundError(f"session {session_id} not found")
        return Session.model_validate(row)

    def list_sessions(self) -> list[Session]:
        rows = self._fetchall("SELECT * FROM sessions ORDER BY updated_at DESC")
        return [Session.model_validate(r) for r in rows]

    def update_session(self, session_id: str, **fields: Any) -> Session:
        fields["updated_at"] = utc_now()
        if self._update("sessions", session_id, fields) == 0:
            raise NotFoundError(f"session {session_id} not found")
        return self.get_session(session_id)

    def touch_session(self, session_id: str) -> None:
        self._update("sessions", session_id, {"updated_at": utc_now()})

    def delete_session(self, session_id: str) -> None:
        if self._execute("DELETE FROM sessions WHERE id = ?", (session_id,)) == 0:
            raise NotFoundError(f"session {session_id} not found")

    # -- sources: the library, and the files each session uses -----------------------------

    def add_to_library(self, source: Source) -> tuple[Source, bool]:
        """Add a file unless the library already has its content; returns the library entry and
        whether it is new. One statement, so two uploads of one file cannot both insert."""
        data = source.model_dump(exclude=set(SOURCE_COMPUTED_FIELDS))
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO sources ({cols}) VALUES ({marks}) ON CONFLICT(sha256) DO NOTHING",
                [_encode(v) for v in data.values()],
            )
        entry = self.find_source(source.sha256)
        if entry is None:  # pragma: no cover - the row was just inserted or already there
            raise NotFoundError(f"source {source.sha256[:12]} not found")
        return entry, bool(cur.rowcount)

    def get_source(self, source_id: str) -> Source:
        sql = f"SELECT {_SOURCE_COLUMNS} FROM sources s WHERE s.id = ?"
        row = self._fetchone(sql, (source_id,))
        if row is None:
            raise NotFoundError(f"source {source_id} not found")
        return Source.model_validate(row)

    def find_source(self, sha256: str) -> Source | None:
        sql = f"SELECT {_SOURCE_COLUMNS} FROM sources s WHERE s.sha256 = ?"
        row = self._fetchone(sql, (sha256,))
        return Source.model_validate(row) if row else None

    def list_library(self) -> list[Source]:
        """Every file in the library, the most recently used first."""
        rows = self._fetchall(f"SELECT {_SOURCE_COLUMNS} FROM sources s")
        sources = [Source.model_validate(r) for r in rows]
        sources.sort(key=lambda s: s.last_used_at or s.created_at, reverse=True)
        return sources

    def list_sources(self, session_id: str) -> list[Source]:
        """The library files a session uses, in the order they were added to it."""
        rows = self._fetchall(
            f"SELECT {_SOURCE_COLUMNS} FROM session_sources ss JOIN sources s "
            "ON s.id = ss.source_id WHERE ss.session_id = ? ORDER BY ss.added_at, s.created_at",
            (session_id,),
        )
        return [Source.model_validate(r) for r in rows]

    def session_source_ids(self, session_id: str) -> list[str]:
        rows = self._fetchall(
            "SELECT source_id FROM session_sources WHERE session_id = ? ORDER BY added_at",
            (session_id,),
        )
        return [r["source_id"] for r in rows]

    def sessions_using(self, source_id: str) -> list[str]:
        rows = self._fetchall(
            "SELECT session_id FROM session_sources WHERE source_id = ? ORDER BY added_at",
            (source_id,),
        )
        return [r["session_id"] for r in rows]

    def attach_source(self, session_id: str, source_id: str) -> bool:
        """Use a library file in a session; False when the session already uses it."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO session_sources (session_id, source_id, added_at) "
                "VALUES (?, ?, ?)",
                (session_id, source_id, utc_now()),
            )
        if cur.rowcount:
            self.touch_session(session_id)
        return bool(cur.rowcount)

    def detach_source(self, session_id: str, source_id: str) -> bool:
        """Stop using a file in a session. The file stays in the library."""
        removed = self._execute(
            "DELETE FROM session_sources WHERE session_id = ? AND source_id = ?",
            (session_id, source_id),
        )
        if removed:
            self.touch_session(session_id)
        return bool(removed)

    def update_source(self, source_id: str, **fields: Any) -> Source:
        fields["updated_at"] = utc_now()
        if self._update("sources", source_id, fields) == 0:
            raise NotFoundError(f"source {source_id} not found")
        return self.get_source(source_id)

    def delete_source(self, source_id: str) -> None:
        """Remove a file from the library and from every session that uses it."""
        if self._execute("DELETE FROM sources WHERE id = ?", (source_id,)) == 0:
            raise NotFoundError(f"source {source_id} not found")

    def forget_processed(self, sha256: str) -> list[str]:
        """Drop the processed text, notes and condensations of a file; returns the processed
        paths so the caller can delete the files."""
        with self._lock:
            paths = [
                r[0]
                for r in self._conn.execute(
                    "SELECT processed_path FROM processed_cache WHERE sha256 = ?", (sha256,)
                )
            ]
            for table in ("processed_cache", "notes", "condensations"):
                self._conn.execute(f"DELETE FROM {table} WHERE sha256 = ?", (sha256,))
        return paths

    def unfinished_sources(self) -> list[Source]:
        rows = self._fetchall(
            f"SELECT {_SOURCE_COLUMNS} FROM sources s "
            "WHERE s.status NOT IN ('ready', 'failed', 'cancelled') ORDER BY s.created_at"
        )
        return [Source.model_validate(r) for r in rows]

    # -- jobs ------------------------------------------------------------------------------

    def create_job(
        self,
        *,
        stage: str,
        resource_class: str,
        source_id: str | None = None,
        message_id: str | None = None,
    ) -> Job:
        job = Job(
            id=new_id(),
            source_id=source_id,
            message_id=message_id,
            stage=stage,
            resource_class=resource_class,  # type: ignore[arg-type]
            state="queued",
            created_at=utc_now(),
        )
        self._insert("jobs", job.model_dump())
        return job

    def update_job(self, job_id: str, **fields: Any) -> None:
        self._update("jobs", job_id, fields)

    def list_jobs(self, source_id: str) -> list[Job]:
        rows = self._fetchall(
            "SELECT * FROM jobs WHERE source_id = ? ORDER BY created_at", (source_id,)
        )
        return [Job.model_validate(r) for r in rows]

    # -- messages --------------------------------------------------------------------------

    def create_message(self, message: Message) -> Message:
        self._insert("messages", message.model_dump())
        self.touch_session(message.session_id)
        return message

    def get_message(self, message_id: str) -> Message:
        row = self._fetchone("SELECT * FROM messages WHERE id = ?", (message_id,))
        if row is None:
            raise NotFoundError(f"message {message_id} not found")
        return Message.model_validate(row)

    def list_messages(self, session_id: str) -> list[Message]:
        rows = self._fetchall(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at", (session_id,)
        )
        return [Message.model_validate(r) for r in rows]

    def update_message(self, message_id: str, **fields: Any) -> Message:
        fields["updated_at"] = utc_now()
        if self._update("messages", message_id, fields) == 0:
            raise NotFoundError(f"message {message_id} not found")
        return self.get_message(message_id)

    def append_message_content(self, message_id: str, delta: str) -> None:
        self._execute(
            "UPDATE messages SET content = content || ?, updated_at = ? WHERE id = ?",
            (delta, utc_now(), message_id),
        )

    def waiting_messages(self, session_id: str) -> list[Message]:
        rows = self._fetchall(
            "SELECT * FROM messages WHERE session_id = ? AND status = 'waiting_for_sources' "
            "ORDER BY created_at",
            (session_id,),
        )
        return [Message.model_validate(r) for r in rows]

    # -- artifacts -------------------------------------------------------------------------

    def create_artifact(self, artifact: Artifact) -> Artifact:
        self._insert("artifacts", artifact.model_dump())
        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact:
        row = self._fetchone("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
        if row is None:
            raise NotFoundError(f"artifact {artifact_id} not found")
        return Artifact.model_validate(row)

    def list_artifacts(self, session_id: str) -> list[Artifact]:
        rows = self._fetchall(
            "SELECT a.* FROM artifacts a JOIN messages m ON m.id = a.message_id "
            "WHERE m.session_id = ? ORDER BY a.created_at",
            (session_id,),
        )
        return [Artifact.model_validate(r) for r in rows]

    # -- caches ----------------------------------------------------------------------------

    def get_processed_cache(self, cache_key: str) -> ProcessedCacheEntry | None:
        row = self._fetchone("SELECT * FROM processed_cache WHERE cache_key = ?", (cache_key,))
        return ProcessedCacheEntry.model_validate(row) if row else None

    def put_processed_cache(self, entry: ProcessedCacheEntry) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO processed_cache "
                "(cache_key, sha256, processed_path, token_estimate, meta, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.cache_key,
                    entry.sha256,
                    entry.processed_path,
                    entry.token_estimate,
                    json.dumps(entry.meta, ensure_ascii=False),
                    entry.created_at,
                ),
            )

    def get_condensation(self, cache_key: str) -> str | None:
        row = self._fetchone("SELECT text FROM condensations WHERE cache_key = ?", (cache_key,))
        return row["text"] if row else None

    def put_condensation(
        self, cache_key: str, *, sha256: str, instruction_hash: str, model: str, text: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO condensations "
                "(cache_key, sha256, instruction_hash, model, text, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cache_key, sha256, instruction_hash, model, text, utc_now()),
            )

    def get_notes(self, cache_key: str) -> SourceNotes | None:
        row = self._fetchone("SELECT * FROM notes WHERE cache_key = ?", (cache_key,))
        if row is None:
            return None
        row["key"] = row.pop("cache_key")
        row["sections"] = json.loads(row["sections"] or "[]")
        return SourceNotes.model_validate(row)

    def put_notes(self, notes: SourceNotes) -> None:
        sections = [s.model_dump() for s in notes.sections]
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO notes "
                "(cache_key, sha256, model, overview, sections, token_estimate, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    notes.key,
                    notes.sha256,
                    notes.model,
                    notes.overview,
                    json.dumps(sections, ensure_ascii=False),
                    notes.token_estimate,
                    notes.created_at,
                ),
            )

    def ready_sources(self) -> list[Source]:
        rows = self._fetchall(
            f"SELECT {_SOURCE_COLUMNS} FROM sources s "
            "WHERE s.status = 'ready' AND s.processed_path IS NOT NULL ORDER BY s.created_at"
        )
        return [Source.model_validate(r) for r in rows]

    # -- bulk ------------------------------------------------------------------------------

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
