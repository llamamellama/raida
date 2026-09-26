"""Request shaping of the LLM backends, without a server."""

from __future__ import annotations

import pytest

from raida.config import LlmConfig
from raida.llm.base import GenerationOptions
from raida.llm.ollama import OllamaBackend

MESSAGES = [{"role": "user", "content": "hi"}]
OPTIONS = GenerationOptions(num_ctx=4096, max_tokens=16, temperature=0.1)


async def _payload(**overrides: object) -> dict:
    backend = OllamaBackend(LlmConfig(model="m", **overrides))  # type: ignore[arg-type]
    try:
        return backend._payload(MESSAGES, OPTIONS, stream=True)
    finally:
        await backend.aclose()


async def test_ollama_payload_defaults() -> None:
    payload = await _payload()
    assert payload["model"] == "m"
    assert payload["stream"] is True
    assert payload["keep_alive"] == "1h"
    assert payload["options"] == {"num_ctx": 4096, "num_predict": 16, "temperature": 0.1}
    assert "think" not in payload


@pytest.mark.parametrize("value", [False, True, "low", "high"])
async def test_ollama_payload_passes_think_through(value: bool | str) -> None:
    payload = await _payload(think=value)
    assert payload["think"] == value
