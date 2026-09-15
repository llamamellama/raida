from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from raida.api.deps import State
from raida.export.service import MEDIA_TYPES, export_message
from raida.models import Artifact, ExportFormat
from raida.pipeline.events import Event

router = APIRouter(prefix="/api", tags=["exports"])


class ExportBody(BaseModel):
    format: ExportFormat


@router.post("/messages/{message_id}/exports", response_model=Artifact, status_code=201)
async def create_export(message_id: str, body: ExportBody, state: State) -> Artifact:
    message = await asyncio.to_thread(state.db.get_message, message_id)
    try:
        artifact = await asyncio.to_thread(
            export_message, state.config, state.db, message, body.format
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state.scheduler.events.publish(
        Event("artifact.created", artifact.model_dump(), session_id=message.session_id)
    )
    return artifact


@router.get("/artifacts/{artifact_id}")
async def download_artifact(artifact_id: str, state: State) -> FileResponse:
    artifact = await asyncio.to_thread(state.db.get_artifact, artifact_id)
    return FileResponse(
        artifact.path, media_type=MEDIA_TYPES[artifact.format], filename=artifact.filename
    )
