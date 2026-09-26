"""Sources: one library of files shared by every session (ADR-0007). Uploading or adding a
file by path puts it in the library and in the session; any other session can then use it
without processing it again. Removing a file from a session keeps it in the library;
deleting it from the library removes it everywhere."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from raida.api.deps import State
from raida.models import Source
from raida.pipeline import ingest

router = APIRouter(prefix="/api", tags=["sources"])
CHUNK = 1024 * 1024


class ByPathBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)
    language: str = "auto"


class SourcePatch(BaseModel):
    # An empty title brings back the file's own name.
    title: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, min_length=1, max_length=16)


@router.get("/library", response_model=list[Source])
async def list_library(state: State) -> list[Source]:
    """Every file in the library, the most recently used first."""
    return await asyncio.to_thread(state.db.list_library)


async def _chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(CHUNK):
        yield chunk


@router.post("/sessions/{session_id}/sources", response_model=list[Source], status_code=201)
async def upload_sources(
    session_id: str,
    state: State,
    files: list[UploadFile],
    language: str = "auto",
) -> list[Source]:
    await asyncio.to_thread(state.db.get_session, session_id)
    added: list[Source] = []
    for upload in files:
        name = upload.filename or "upload"
        try:
            ingest.detect_kind(name)
            path, sha, size = await ingest.store_upload(state.config, name, _chunks(upload))
            candidate = ingest.build_source(
                original_name=ingest.safe_filename(name),
                stored_path=path,
                sha256=sha,
                size_bytes=size,
                language=language,
                managed=True,
            )
        except ingest.IngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await upload.close()
        added.append(await state.scheduler.add_source(session_id, candidate))
    return added


@router.post("/sessions/{session_id}/sources/by-path", response_model=list[Source], status_code=201)
async def add_by_path(session_id: str, body: ByPathBody, state: State) -> list[Source]:
    await asyncio.to_thread(state.db.get_session, session_id)
    added: list[Source] = []
    for raw in body.paths:
        try:
            path = ingest.check_allowed_path(state.config, Path(raw))
            ingest.detect_kind(path.name)
        except ingest.IngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sha, size = await asyncio.to_thread(ingest.hash_file, path)
        candidate = ingest.build_source(
            original_name=path.name,
            stored_path=path,
            sha256=sha,
            size_bytes=size,
            language=body.language,
            managed=False,
        )
        added.append(await state.scheduler.add_source(session_id, candidate))
    return added


@router.put("/sessions/{session_id}/sources/{source_id}", response_model=Source)
async def use_source(session_id: str, source_id: str, state: State) -> Source:
    """Use a library file in this session. Its processed text and notes are reused, so it is
    ready at once (or when the run in progress finishes)."""
    return await state.scheduler.attach_source(session_id, source_id)


@router.delete("/sessions/{session_id}/sources/{source_id}", status_code=204)
async def stop_using_source(session_id: str, source_id: str, state: State) -> None:
    """Remove a file from this session. It stays in the library for other sessions."""
    await state.scheduler.detach_source(session_id, source_id)


@router.get("/sources/{source_id}", response_model=Source)
async def get_source(source_id: str, state: State) -> Source:
    return await asyncio.to_thread(state.db.get_source, source_id)


@router.get("/sources/{source_id}/text", response_class=PlainTextResponse)
async def get_source_text(source_id: str, state: State) -> str:
    source = await asyncio.to_thread(state.db.get_source, source_id)
    if source.status != "ready" or not source.processed_path:
        raise HTTPException(status_code=409, detail=f"source is {source.status}, not ready")
    return await asyncio.to_thread(Path(source.processed_path).read_text, "utf-8")


@router.get("/sources/{source_id}/notes", response_class=PlainTextResponse)
async def get_source_notes(source_id: str, state: State) -> str:
    """The notes taken on the source when it was added, as Markdown."""
    source = await asyncio.to_thread(state.db.get_source, source_id)
    if not source.processed_path:
        raise HTTPException(status_code=409, detail=f"source is {source.status}, not ready")
    notes = await state.scheduler.notes.load(Path(source.processed_path).stem)
    if notes is None:
        raise HTTPException(status_code=404, detail="this source has no notes")
    return notes.markdown()


@router.patch("/sources/{source_id}", response_model=Source)
async def patch_source(source_id: str, body: SourcePatch, state: State) -> Source:
    """Rename the file, or set its language, in every session that uses it. A new name is
    only a name; a new language processes recordings and scanned PDFs again."""
    if body.title is None and body.language is None:
        raise HTTPException(status_code=422, detail="give a title, a language or both")
    source = await asyncio.to_thread(state.db.get_source, source_id)
    if body.title is not None:
        source = await state.scheduler.rename_source(source_id, body.title)
    if body.language is not None:
        source = await state.scheduler.set_language(source_id, body.language)
    return source


@router.post("/sources/{source_id}/retry", response_model=Source)
async def retry_source(source_id: str, state: State) -> Source:
    return await state.scheduler.retry_source(source_id)


@router.post("/sources/{source_id}/cancel", response_model=Source)
async def cancel_source(source_id: str, state: State) -> Source:
    return await state.scheduler.cancel_source(source_id)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: str, state: State) -> None:
    """Delete a file from the library and from every session that uses it, with the text,
    notes and uploaded copy raida kept. A file added by path is left where it is."""
    await state.scheduler.delete_source(source_id)
