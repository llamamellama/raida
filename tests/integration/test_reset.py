"""Nuke: the factory reset deletes everything the user made and keeps what raida needs."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx

from tests.conftest import FIXTURES, wait_for

DONE = {"done", "failed", "cancelled"}
BUILTINS = {"article", "key-takeaways", "meeting-minutes", "questions-answers", "summary"}
NUKE = {"confirm": "NUKE"}


async def _session_with(client: httpx.AsyncClient, *names: str) -> str:
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    files = [("files", (n, (FIXTURES / n).read_bytes())) for n in names]
    for source in (await client.post(f"/api/sessions/{sid}/sources", files=files)).json():
        await wait_for(client, f"/api/sources/{source['id']}", {"ready", "failed"})
    return sid


def _state(client: httpx.AsyncClient):
    return client.app.state.raida  # type: ignore[attr-defined]


def _row_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


async def test_nuke_needs_the_word(client: httpx.AsyncClient) -> None:
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    assert (await client.post("/api/reset", json={})).status_code == 422
    wrong = await client.post("/api/reset", json={"confirm": "nuke"})
    assert wrong.status_code == 400 and "NUKE" in wrong.json()["detail"]
    assert (await client.get(f"/api/sessions/{sid}")).status_code == 200


async def test_nuke_deletes_what_the_user_made_and_keeps_the_rest(
    client: httpx.AsyncClient,
) -> None:
    config = _state(client).config
    sid = await _session_with(client, "notes.md", "text.pdf", "tone.wav")
    by_path = await client.post(
        f"/api/sessions/{sid}/sources/by-path", json={"paths": [str(FIXTURES / "captions.srt")]}
    )
    await wait_for(client, f"/api/sources/{by_path.json()[0]['id']}", {"ready"})
    r = await client.post(f"/api/sessions/{sid}/messages", json={"content": "Summarize."})
    answer = await wait_for(client, f"/api/messages/{r.json()['id']}", DONE)
    export = await client.post(f"/api/messages/{answer['id']}/exports", json={"format": "md"})
    assert export.status_code == 201
    skill = {"name": "mine", "description": "Mine.", "instructions": "Do it."}
    assert (await client.post("/api/skills", json=skill)).status_code == 201
    assert (await client.delete("/api/skills/article")).status_code == 200
    await client.post("/api/sessions", json={})  # a second, empty session
    backups = config.paths.data_dir / "backups"
    backups.mkdir()
    (backups / "raida-before-upgrade.sqlite3").write_bytes(b"old")
    weights = config.models_dir / "hf" / "weights.bin"
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"model")

    r = await client.post("/api/reset", json=NUKE)
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["sessions"] == 2 and summary["sources"] == 4 and summary["skills"] == 1
    assert summary["bytes_freed"] > 0

    assert (await client.get("/api/sessions")).json() == []
    assert (await client.get("/api/library")).json() == []
    listing = (await client.get("/api/skills")).json()
    assert {s["name"] for s in listing["skills"]} == BUILTINS
    assert all(s["origin"] == "builtin" for s in listing["skills"])
    assert listing["deleted_builtins"] == []
    assert set(_row_counts(config.db_path).values()) == {0}
    for folder in ("uploads", "media", "processed", "artifacts", "skills", "backups"):
        path = config.paths.data_dir / folder
        if folder == "backups":
            assert not path.exists()
        else:
            assert path.is_dir() and list(path.iterdir()) == [], folder
    assert weights.read_bytes() == b"model"  # downloaded models stay
    assert (FIXTURES / "captions.srt").is_file()  # a file added by path is the user's

    # Factory settings, still working: a new session processes and answers as before.
    sid = await _session_with(client, "notes.md")
    r = await client.post(f"/api/sessions/{sid}/messages", json={"content": "Again."})
    assert (await wait_for(client, f"/api/messages/{r.json()['id']}", DONE))["status"] == "done"


async def test_nuke_stops_work_in_progress(client: httpx.AsyncClient) -> None:
    scheduler = _state(client).scheduler
    scheduler.llm.delay_s = 0.2  # a slow answer, still streaming when the reset comes
    sid = await _session_with(client, "notes.md")
    r = await client.post(f"/api/sessions/{sid}/messages", json={"content": "Summarize."})
    await wait_for(client, f"/api/messages/{r.json()['id']}", {"streaming"})
    assert (await client.post("/api/reset", json=NUKE)).status_code == 200
    assert not scheduler._message_tasks and not scheduler._source_tasks
    assert not scheduler._prepare_tasks
    assert (await client.get(f"/api/messages/{r.json()['id']}")).status_code == 404
    assert (await client.get("/api/sessions")).json() == []


async def test_every_open_tab_hears_the_reset(live_server: str) -> None:
    async with httpx.AsyncClient(base_url=live_server, timeout=30) as client:
        sid = (await client.post("/api/sessions", json={})).json()["id"]
        received: list[tuple[str, dict]] = []

        async def consume() -> None:
            async with client.stream("GET", f"/api/sessions/{sid}/events") as resp:
                event = None
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event:
                        received.append((event, json.loads(line.split(":", 1)[1])))
                        if event == "app.reset":
                            return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        assert (await client.post("/api/reset", json=NUKE)).status_code == 200
        await asyncio.wait_for(task, timeout=10)
    reset = next(data for event, data in received if event == "app.reset")
    assert reset["sessions"] == 1
