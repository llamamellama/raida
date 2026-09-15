"""In-process event bus feeding Server-Sent Events subscribers."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

QUEUE_LIMIT = 2000


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None

    def to_sse(self) -> dict[str, str]:
        return {"event": self.type, "data": json.dumps(self.data, ensure_ascii=False)}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str | None, set[asyncio.Queue[Event]]] = {}

    def subscribe(self, session_id: str | None) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str | None, queue: asyncio.Queue[Event]) -> None:
        subs = self._subscribers.get(session_id)
        if subs:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(session_id, None)

    def publish(self, event: Event) -> None:
        targets: set[asyncio.Queue[Event]] = set()
        if event.session_id is None:
            for subs in self._subscribers.values():
                targets |= subs
        else:
            targets |= self._subscribers.get(event.session_id, set())
            targets |= self._subscribers.get(None, set())
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled browser tab must not block the pipeline; drop the oldest event.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    log.warning("event_dropped", extra={"event_type": event.type})

    def subscriber_count(self) -> int:
        return sum(len(s) for s in self._subscribers.values())
