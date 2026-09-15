from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from raida.api.app import create_app
from raida.config import Config, load_config

FIXTURES = Path(__file__).parent / "fixtures"


def make_config(tmp_path: Path, **overrides: str) -> Config:
    env = {
        "RAIDA_LLM__MODEL": "fake-model",
        "RAIDA_LLM__BACKEND": "fake",
        "RAIDA_TRANSCRIBE__BACKEND": "fake",
        "RAIDA_PATHS__DATA_DIR": str(tmp_path / "data"),
        "RAIDA_PATHS__ALLOWED_ROOTS": f'["{FIXTURES}"]',
        "RAIDA_OCR__ENABLED": "false",
        "RAIDA_WORKERS__CPU": "2",
        "RAIDA_LOG_LEVEL": "WARNING",
        **overrides,
    }
    clean = {k: v for k, v in os.environ.items() if not k.startswith("RAIDA_")}
    return load_config(None, {**clean, **env})


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


@pytest.fixture
async def client(config: Config) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


async def wait_for(
    client: httpx.AsyncClient,
    url: str,
    terminal: set[str],
    key: str = "status",
    max_wait: float = 30.0,
) -> dict:
    import asyncio
    import time

    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        data = (await client.get(url)).json()
        if data[key] in terminal:
            return data
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {url} to reach {terminal}: {data}")


@pytest.fixture
async def live_server(config: Config) -> AsyncIterator[str]:
    """Real uvicorn server in a thread: needed for streaming responses, which the in-process
    ASGI transport buffers."""
    import asyncio
    import socket
    import threading

    import uvicorn

    app = create_app(config)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient() as probe:
        for _ in range(200):
            try:
                if (await probe.get(f"{base}/api/health")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("live server did not start")
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=10)
