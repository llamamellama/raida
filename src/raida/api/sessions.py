from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field

from raida.api.deps import State
from raida.models import Session, SessionDetail

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    # No title: the session gets a timestamp name and is renamed after its first answer.
    title: str | None = Field(default=None, max_length=200)


class SessionPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.post("", response_model=Session, status_code=201)
async def create_session(state: State, body: SessionCreate | None = None) -> Session:
    title = body.title if body is not None else None
    return await asyncio.to_thread(state.db.create_session, title)


@router.get("", response_model=list[Session])
async def list_sessions(state: State) -> list[Session]:
    return await asyncio.to_thread(state.db.list_sessions)


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, state: State) -> SessionDetail:
    return await asyncio.to_thread(state.scheduler.snapshot, session_id)


@router.patch("/{session_id}", response_model=Session)
async def patch_session(session_id: str, body: SessionPatch, state: State) -> Session:
    # A title chosen by the user is never overwritten by the automatic naming.
    return await asyncio.to_thread(
        state.db.update_session, session_id, title=body.title, title_auto=False
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, state: State) -> None:
    sources = await asyncio.to_thread(state.db.list_sources, session_id)
    for source in sources:
        await state.scheduler.remove_source(source.id)
    await asyncio.to_thread(state.db.delete_session, session_id)
