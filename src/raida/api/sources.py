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
from raida.pipeline.events import Event

router = APIRouter(prefix="/api", tags=["sources"])
CHUNK = 1024 * 1024


class ByPathBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)
    language: str = "auto"


class LanguagePatch(BaseModel):
    language: str = Field(min_length=1, max_length=16)


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
    created: list[Source] = []
    for upload in files:
        name = upload.filename or "upload"
        try:
            ingest.detect_kind(name)
            path, sha, size = await ingest.store_upload(state.config, name, _chunks(upload))
            source = ingest.build_source(
                session_id=session_id,
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
        await asyncio.to_thread(state.db.create_source, source)
        state.scheduler.events.publish(
            Event("source.updated", source.model_dump(), session_id=session_id)
        )
        state.scheduler.submit_source(source.id)
        created.append(source)
    return created


@router.post("/sessions/{session_id}/sources/by-path", response_model=list[Source], status_code=201)
async def add_by_path(session_id: str, body: ByPathBody, state: State) -> list[Source]:
    await asyncio.to_thread(state.db.get_session, session_id)
    created: list[Source] = []
    for raw in body.paths:
        try:
            path = ingest.check_allowed_path(state.config, Path(raw))
            ingest.detect_kind(path.name)
        except ingest.IngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sha, size = await asyncio.to_thread(ingest.hash_file, path)
        source = ingest.build_source(
            session_id=session_id,
            original_name=path.name,
            stored_path=path,
            sha256=sha,
            size_bytes=size,
            language=body.language,
            managed=False,
        )
        await asyncio.to_thread(state.db.create_source, source)
        from raida.pipeline.events import Event

        state.scheduler.events.publish(
            Event("source.updated", source.model_dump(), session_id=session_id)
        )
        state.scheduler.submit_source(source.id)
        created.append(source)
    return created


@router.get("/sources/{source_id}", response_model=Source)
async def get_source(source_id: str, state: State) -> Source:
    return await asyncio.to_thread(state.db.get_source, source_id)


@router.get("/sources/{source_id}/text", response_class=PlainTextResponse)
async def get_source_text(source_id: str, state: State) -> str:
    source = await asyncio.to_thread(state.db.get_source, source_id)
    if source.status != "ready" or not source.processed_path:
        raise HTTPException(status_code=409, detail=f"source is {source.status}, not ready")
    return await asyncio.to_thread(Path(source.processed_path).read_text, "utf-8")


@router.patch("/sources/{source_id}", response_model=Source)
async def patch_source(source_id: str, body: LanguagePatch, state: State) -> Source:
    return await state.scheduler.set_language(source_id, body.language)


@router.post("/sources/{source_id}/retry", response_model=Source)
async def retry_source(source_id: str, state: State) -> Source:
    return await state.scheduler.retry_source(source_id)


@router.post("/sources/{source_id}/cancel", response_model=Source)
async def cancel_source(source_id: str, state: State) -> Source:
    return await state.scheduler.cancel_source(source_id)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: str, state: State) -> None:
    await state.scheduler.remove_source(source_id)
