"""Resource classes with independent concurrency limits.

cpu: a process pool for parsing/OCR (real parallelism, GIL-free).
gpu: transcription; one job at a time by default because Metal kernels serialize anyway.
llm: calls to the model server; answers first, background work (notes, prefill) when idle.
subprocess: ffmpeg and friends.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import multiprocessing
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from typing import Any, TypeVar

from raida.config import Config
from raida.doctor import performance_core_count
from raida.llm.gate import LlmGate

log = logging.getLogger(__name__)
T = TypeVar("T")


class ResourcePool:
    def __init__(self, config: Config) -> None:
        cpu = config.workers.cpu
        self.cpu_workers = min(6, performance_core_count()) if cpu == "auto" else int(cpu)
        self.gpu = asyncio.Semaphore(config.workers.gpu)
        self.llm = LlmGate(config.workers.llm, config.workers.llm_background)
        self.subprocess = asyncio.Semaphore(config.workers.subprocess)
        self._pool: ProcessPoolExecutor | None = None

    def start(self) -> None:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                max_workers=self.cpu_workers, mp_context=multiprocessing.get_context("spawn")
            )
            log.info("resource_pool_started", extra={"cpu_workers": self.cpu_workers})

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    async def run_cpu(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if self._pool is None:
            raise RuntimeError("ResourcePool not started")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, functools.partial(fn, *args, **kwargs))

    async def run_blocking(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run a blocking call in a thread (for libraries that must stay in-process, e.g. MLX)."""
        return await asyncio.to_thread(functools.partial(fn, *args, **kwargs))
