"""Priority for model calls: an answer never waits behind background work.

Background work (notes taken when a source is added, reading a session's sources into the
prompt cache ahead of a question, session titles) runs only while no answer is being written.
When a question arrives, background calls in flight are cancelled and retried after the answer.
llama-server keeps the part of a prompt it had already read, so a retry loses little.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")


class LlmGate:
    def __init__(self, interactive: int = 1, background: int = 1) -> None:
        self._interactive = asyncio.Semaphore(interactive)
        self._background = asyncio.Semaphore(background)
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._running: set[asyncio.Future[object]] = set()
        self.preemptions = 0

    @property
    def answering(self) -> bool:
        return self._active > 0

    @asynccontextmanager
    async def interactive(self) -> AsyncIterator[None]:
        """Hold for the whole of an answer: background calls stop and wait until it ends."""
        self._active += 1
        self._idle.clear()
        for future in list(self._running):
            future.cancel()
        try:
            async with self._interactive:
                yield
        finally:
            self._active -= 1
            if self._active == 0:
                self._idle.set()

    async def background(self, call: Callable[[], Awaitable[T]]) -> T:
        """Run ``call`` when no answer is being written; start it again if one interrupts it."""
        while True:
            await self._idle.wait()
            async with self._background:
                if not self._idle.is_set():
                    continue
                future = asyncio.ensure_future(call())
                self._running.add(future)  # type: ignore[arg-type]
                try:
                    return await future
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        raise  # the caller itself was cancelled
                    self.preemptions += 1
                    log.info("llm_background_preempted")
                finally:
                    self._running.discard(future)  # type: ignore[arg-type]
