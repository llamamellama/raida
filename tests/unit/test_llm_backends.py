"""Request shaping of the LLM backends, without a server."""

from __future__ import annotations

import json

import httpx
import pytest

from raida.config import LlmConfig
from raida.llm.base import GenerationOptions, Usage
from raida.llm.llama_server import LlamaServerBackend
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


@pytest.mark.parametrize(
    ("configured", "requested", "sent"),
    [
        (None, False, None),  # thinking-only tags leak reasoning with think=false: never sent
        (True, False, False),
        (False, None, False),
        ("high", False, "low"),  # gpt-oss cannot turn reasoning off
        ("high", None, "high"),
    ],
)
async def test_ollama_think_hint(configured: object, requested: bool | None, sent: object) -> None:
    backend = OllamaBackend(LlmConfig(model="m", think=configured))  # type: ignore[arg-type]
    try:
        options = GenerationOptions(num_ctx=4096, max_tokens=16, think=requested)
        payload = backend._payload(MESSAGES, options, stream=True)
    finally:
        await backend.aclose()
    assert payload.get("think") == sent


# -- llama-server ------------------------------------------------------------------------------

THINKING_PROMPT = "<|im_start|>user\nhi<|im_end|>\n<|im_start|>assistant\n<think>\n"


def _sse(*chunks: dict) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


def _llama(handler, **config: object) -> LlamaServerBackend:
    backend = LlamaServerBackend(LlmConfig(model="m", backend="llama_server", **config))  # type: ignore[arg-type]
    backend._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://llama"
    )
    return backend


async def test_llama_server_closes_the_reasoning_block_when_asked_not_to_think() -> None:
    seen: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen[request.url.path] = body
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": THINKING_PROMPT})
        assert request.url.path == "/completion"
        return httpx.Response(
            200,
            content=b'data: {"content": "Notes", "stop": false}\n\n'
            b'data: {"content": ".", "stop": true, "tokens_evaluated": 12, '
            b'"tokens_predicted": 2, "timings": {"cache_n": 7}}\n\n',
        )

    backend = _llama(handler)
    usage = Usage()
    options = GenerationOptions(num_ctx=4096, max_tokens=100, think=False)
    text = "".join([d async for d in backend.stream_chat(MESSAGES, options, usage)])
    await backend.aclose()
    assert text == "Notes."
    assert seen["/completion"]["prompt"] == THINKING_PROMPT + "\n</think>\n\n"
    assert seen["/completion"]["n_predict"] == 100 and seen["/completion"]["cache_prompt"]
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cached_tokens) == (12, 2, 7)


async def test_llama_server_hybrid_template_uses_chat_with_thinking_disabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "<|im_start|>assistant\n"})
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "ok"}}]}))

    backend = _llama(handler)
    options = GenerationOptions(num_ctx=4096, max_tokens=10, think=False)
    assert await backend.complete(MESSAGES, options) == "ok"
    await backend.aclose()


async def test_llama_server_streams_reasoning_separately_and_reports_cache() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["cache_prompt"] is True and body["stream"] is True
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"reasoning_content": "Let me see."}}]},
                {"choices": [{"delta": {"reasoning_content": " Yes."}}]},
                {"choices": [{"delta": {"content": "Answer"}}]},
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 900,
                        "completion_tokens": 3,
                        "prompt_tokens_details": {"cached_tokens": 880},
                    },
                },
            ),
        )

    backend = _llama(handler)
    thoughts: list[str] = []
    usage = Usage()
    options = GenerationOptions(num_ctx=4096, max_tokens=10)
    parts = [d async for d in backend.stream_chat(MESSAGES, options, usage, thoughts.append)]
    await backend.aclose()
    assert parts == ["Answer"] and thoughts == ["Let me see.", " Yes."]
    assert usage.cached_tokens == 880 and usage.reasoning_tokens == 2


async def test_llama_server_prefill_reads_without_generating() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["max_tokens"] == 0 and body["stream"] is False
        return httpx.Response(
            200, json={"usage": {"prompt_tokens": 5000}, "timings": {"prompt_n": 5000}}
        )

    backend = _llama(handler)
    usage = await backend.prefill(MESSAGES, GenerationOptions(num_ctx=4096, max_tokens=1))
    await backend.aclose()
    assert usage.prompt_tokens == 5000


@pytest.mark.parametrize(
    ("alias", "n_ctx", "ok", "fragment"),
    [
        ("m", 200_000, True, "3 slots"),
        ("other", 200_000, False, "--alias m"),
        ("m", 8192, False, "larger -c"),
    ],
)
async def test_llama_server_health(alias: str, n_ctx: int, ok: bool, fragment: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(
            200,
            json={
                "model_alias": alias,
                "total_slots": 3,
                "default_generation_settings": {"n_ctx": n_ctx},
            },
        )

    backend = _llama(handler)
    status = await backend.health()
    await backend.aclose()
    assert status.ok is ok and fragment in status.detail
