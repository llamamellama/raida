"""Server-Sent Events: a snapshot of the session, then live events, with keep-alive pings."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from sse_starlette.sse import EventSourceResponse

from raida.api.deps import AppState
from raida.pipeline.events import Event

PING_SECONDS = 15


def session_event_stream(state: AppState, session_id: str) -> EventSourceResponse:
    bus = state.scheduler.events

    async def generator() -> AsyncIterator[dict[str, str]]:
        queue = bus.subscribe(session_id)
        try:
            snapshot = await asyncio.to_thread(state.scheduler.snapshot, session_id)
            yield {"event": "snapshot", "data": snapshot.model_dump_json()}
            if state.scheduler.last_health is not None:
                yield {
                    "event": "system.status",
                    "data": state.scheduler.last_health.model_dump_json(),
                }
            # The user opened this session: read its sources into the prompt cache now, so
            # the first question does not wait for them.
            state.scheduler.schedule_prepare(session_id)
            while True:
                event: Event = await queue.get()
                yield event.to_sse()
        finally:
            bus.unsubscribe(session_id, queue)

    return EventSourceResponse(
        generator(),
        ping=PING_SECONDS,
        ping_message_factory=lambda: {"comment": json.dumps({"ping": True})},
    )
