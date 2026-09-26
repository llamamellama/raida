"""Answers pre-empt background model calls."""

from __future__ import annotations

import asyncio

import pytest

from raida.llm.gate import LlmGate


async def test_background_waits_while_an_answer_is_written() -> None:
    gate = LlmGate()
    order: list[str] = []

    async def job() -> str:
        order.append("background")
        return "done"

    async with gate.interactive():
        task = asyncio.create_task(gate.background(job))
        await asyncio.sleep(0.05)
        assert order == [] and gate.answering
        order.append("answer")
    assert await task == "done"
    assert order == ["answer", "background"]


async def test_answer_preempts_running_background_call_which_restarts() -> None:
    gate = LlmGate()
    starts = 0
    release = asyncio.Event()

    async def job() -> int:
        nonlocal starts
        starts += 1
        if starts == 1:
            await asyncio.sleep(10)  # interrupted by the answer below
        await release.wait()
        return starts

    task = asyncio.create_task(gate.background(job))
    await asyncio.sleep(0.05)
    async with gate.interactive():
        await asyncio.sleep(0.05)
        assert starts == 1
    release.set()
    assert await task == 2
    assert gate.preemptions == 1


async def test_cancelling_the_caller_is_not_mistaken_for_preemption() -> None:
    gate = LlmGate()

    async def job() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(gate.background(job))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.preemptions == 0
