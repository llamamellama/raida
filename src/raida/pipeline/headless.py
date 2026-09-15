"""Headless flows behind the CLI: process files, ask, export. Useful for scripting and tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from raida.config import Config
from raida.db import Database
from raida.export.service import export_message as _export
from raida.models import TERMINAL_SOURCE_STATUSES
from raida.pipeline import ingest
from raida.pipeline.events import Event
from raida.pipeline.scheduler import Scheduler


async def _wait_sources(scheduler: Scheduler, session_id: str, poll: float = 0.2) -> None:
    while True:
        sources = await asyncio.to_thread(scheduler.db.list_sources, session_id)
        if all(s.status in TERMINAL_SOURCE_STATUSES for s in sources):
            return
        await asyncio.sleep(poll)


async def process_files(config: Config, paths: list[Path], language: str = "auto") -> int:
    config.ensure_dirs()
    db = Database(config.db_path)
    scheduler = Scheduler(config, db)
    await scheduler.start()
    try:
        session = await asyncio.to_thread(db.create_session, "cli")
        for path in paths:
            if not path.is_file():
                print(f"error: not a file: {path}", file=sys.stderr)
                return 2
            sha, size = await asyncio.to_thread(ingest.hash_file, path)
            source = ingest.build_source(
                session_id=session.id,
                original_name=path.name,
                stored_path=path.resolve(),
                sha256=sha,
                size_bytes=size,
                language=language,
                managed=False,
            )
            await asyncio.to_thread(db.create_source, source)
            scheduler.submit_source(source.id)
        await _wait_sources(scheduler, session.id)
        failed = 0
        for source in await asyncio.to_thread(db.list_sources, session.id):
            print(
                f"\n===== {source.original_name} [{source.status}] "
                f"tokens={source.token_estimate} meta={source.meta}\n"
            )
            if source.status == "ready" and source.processed_path:
                print(await asyncio.to_thread(Path(source.processed_path).read_text, "utf-8"))
            elif source.error:
                print(source.error, file=sys.stderr)
                failed += 1
        print(f"\nsession {session.id}", file=sys.stderr)
        return 1 if failed else 0
    finally:
        await scheduler.stop()
        db.close()


async def ask(config: Config, session_id: str, instruction: str) -> int:
    config.ensure_dirs()
    db = Database(config.db_path)
    scheduler = Scheduler(config, db)
    await scheduler.start()
    try:
        queue = scheduler.events.subscribe(session_id)
        message = await scheduler.submit_message(session_id, instruction, run_with_ready_only=False)
        while True:
            event: Event = await queue.get()
            if event.type == "message.delta" and event.data.get("message_id") == message.id:
                sys.stdout.write(event.data["text"])
                sys.stdout.flush()
            elif event.type == "message.progress" and event.data.get("message_id") == message.id:
                print(f"[{event.data['detail']}]", file=sys.stderr)
            elif event.type == "message.updated" and event.data.get("id") == message.id:
                status = event.data.get("status")
                if status in ("done", "failed", "cancelled"):
                    print(file=sys.stdout)
                    if status != "done":
                        print(f"error: {event.data.get('error')}", file=sys.stderr)
                        return 1
                    print(
                        f"message {message.id} strategy={event.data.get('strategy')}",
                        file=sys.stderr,
                    )
                    return 0
    finally:
        await scheduler.stop()
        db.close()


async def export_message(config: Config, message_id: str, fmt: str, output: str | None) -> int:
    config.ensure_dirs()
    db = Database(config.db_path)
    try:
        message = await asyncio.to_thread(db.get_message, message_id)
        artifact = await asyncio.to_thread(_export, config, db, message, fmt)  # type: ignore[arg-type]
        if output:
            data = await asyncio.to_thread(Path(artifact.path).read_bytes)
            await asyncio.to_thread(Path(output).write_bytes, data)
            print(output)
        else:
            print(artifact.path)
        return 0
    finally:
        db.close()
