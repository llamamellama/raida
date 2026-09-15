from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from raida.api.deps import State
from raida.api.sse import session_event_stream
from raida.models import Message

router = APIRouter(prefix="/api", tags=["messages"])


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    run_with_ready_only: bool = False


@router.post("/sessions/{session_id}/messages", response_model=Message, status_code=202)
async def create_message(session_id: str, body: MessageCreate, state: State) -> Message:
    try:
        return await state.scheduler.submit_message(
            session_id, body.content, body.run_with_ready_only
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/messages/{message_id}", response_model=Message)
async def get_message(message_id: str, state: State) -> Message:
    return await asyncio.to_thread(state.db.get_message, message_id)


@router.post("/messages/{message_id}/cancel", response_model=Message)
async def cancel_message(message_id: str, state: State) -> Message:
    return await state.scheduler.cancel_message(message_id)


@router.get("/sessions/{session_id}/events")
async def events(session_id: str, state: State):
    await asyncio.to_thread(state.db.get_session, session_id)
    return session_event_stream(state, session_id)
