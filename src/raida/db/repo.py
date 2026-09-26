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
    Artifact,
    Job,
    LibraryEntry,
    Message,
    ProcessedCacheEntry,
    Session,
    Source,
    new_id,
    utc_now,
)

SCHEMA_VERSION = 2
_JSON_FIELDS = {"meta", "token_usage"}
_BOOL_FIELDS = {"managed", "run_with_ready_only", "title_auto"}


class NotFoundError(LookupError):
    pass


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in _JSON_FIELDS & data.keys():
        data[key] = json.loads(data[key]) if data[key] else {}
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
            if current < 1:
                schema = resources.files("raida.db").joinpath("schema.sql").read_text("utf-8")
                self._conn.executescript(schema)
            elif current < 2:
                columns = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)")}
                if "title_auto" not in columns:
                    self._conn.execute(
                        "ALTER TABLE sessions ADD COLUMN title_auto INTEGER NOT NULL DEFAULT 1"
                    )
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

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

    # -- sources ---------------------------------------------------------------------------

    def create_source(self, source: Source) -> Source:
        self._insert("sources", source.model_dump())
        self.touch_session(source.session_id)
        return source

    def get_source(self, source_id: str) -> Source:
        row = self._fetchone("SELECT * FROM sources WHERE id = ?", (source_id,))
        if row is None:
            raise NotFoundError(f"source {source_id} not found")
        return Source.model_validate(row)

    def list_sources(self, session_id: str) -> list[Source]:
        rows = self._fetchall(
            "SELECT * FROM sources WHERE session_id = ? ORDER BY created_at", (session_id,)
        )
        return [Source.model_validate(r) for r in rows]

    def update_source(self, source_id: str, **fields: Any) -> Source:
        fields["updated_at"] = utc_now()
        if self._update("sources", source_id, fields) == 0:
            raise NotFoundError(f"source {source_id} not found")
        return self.get_source(source_id)

    def delete_source(self, source_id: str) -> None:
        if self._execute("DELETE FROM sources WHERE id = ?", (source_id,)) == 0:
            raise NotFoundError(f"source {source_id} not found")

    def list_library(self) -> list[LibraryEntry]:
        """Every distinct file known to any session, newest use first: the shared library."""
        rows = self._fetchall("SELECT * FROM sources ORDER BY created_at DESC")
        by_sha: dict[str, LibraryEntry] = {}
        for row in rows:
            source = Source.model_validate(row)
            entry = by_sha.get(source.sha256)
            if entry is None:
                by_sha[source.sha256] = LibraryEntry(
                    sha256=source.sha256,
                    source_id=source.id,
                    original_name=source.original_name,
                    kind=source.kind,
                    size_bytes=source.size_bytes,
                    language=source.language,
                    managed=source.managed,
                    stored_path=source.stored_path,
                    status=source.status,
                    token_estimate=source.token_estimate,
                    session_ids=[source.session_id],
                    last_used_at=source.updated_at,
                )
            elif source.session_id not in entry.session_ids:
                entry.session_ids.append(source.session_id)
        return list(by_sha.values())

    def find_source_by_sha(self, session_id: str, sha256: str) -> Source | None:
        row = self._fetchone(
            "SELECT * FROM sources WHERE session_id = ? AND sha256 = ? ORDER BY created_at LIMIT 1",
            (session_id, sha256),
        )
        return Source.model_validate(row) if row else None

    def sources_referencing(self, sha256: str) -> int:
        row = self._fetchone("SELECT COUNT(*) AS n FROM sources WHERE sha256 = ?", (sha256,))
        return int(row["n"]) if row else 0

    def unfinished_sources(self) -> list[Source]:
        rows = self._fetchall(
            "SELECT * FROM sources WHERE status NOT IN ('ready', 'failed', 'cancelled') "
            "ORDER BY created_at"
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

    # -- bulk ------------------------------------------------------------------------------

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
