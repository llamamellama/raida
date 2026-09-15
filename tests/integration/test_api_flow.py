from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from raida.pipeline.stages import ocr
from tests.conftest import FIXTURES, wait_for

TERMINAL = {"ready", "failed", "cancelled"}


async def _upload(
    client: httpx.AsyncClient, session_id: str, *names: str, language: str = "auto"
) -> list[dict]:
    files = [("files", (n, (FIXTURES / n).read_bytes())) for n in names]
    r = await client.post(
        f"/api/sessions/{session_id}/sources", files=files, params={"language": language}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _session(client: httpx.AsyncClient) -> str:
    r = await client.post("/api/sessions", json={"title": "t"})
    assert r.status_code == 201
    return r.json()["id"]


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["llm"]["ok"] and body["ffmpeg"]["ok"] and body["storage"]["ok"]
    assert body["pdf_renderer"]["extra"]["renderer"] in ("weasyprint", "fpdf2")


async def test_index_served(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200 and "<title>raida</title>" in r.text
    assert (await client.get("/static/vendor/marked.umd.js")).status_code == 200


async def test_parallel_ingest_all_kinds(client: httpx.AsyncClient) -> None:
    sid = await _session(client)
    sources = await _upload(
        client, sid, "notes.md", "text.pdf", "captions.srt", "tone.wav", "clip.mp4"
    )
    assert len(sources) == 5
    results = await asyncio.gather(
        *(wait_for(client, f"/api/sources/{s['id']}", TERMINAL) for s in sources)
    )
    by_name = {s["original_name"]: s for s in results}
    assert by_name["notes.md"]["status"] == "ready"
    assert by_name["text.pdf"]["status"] == "ready" and by_name["text.pdf"]["meta"]["pages"] == 2
    assert by_name["captions.srt"]["status"] == "ready"
    assert by_name["tone.wav"]["status"] == "ready", by_name["tone.wav"]["error"]
    assert by_name["clip.mp4"]["status"] == "ready", by_name["clip.mp4"]["error"]
    assert 4.5 <= by_name["tone.wav"]["meta"]["duration_s"] <= 5.5
    assert by_name["clip.mp4"]["meta"]["transcriber"] == "fake"
    assert by_name["clip.mp4"]["meta"]["detected_language"] == "en"
    text = (await client.get(f"/api/sources/{by_name['clip.mp4']['id']}/text")).text
    assert text.startswith("[00:00:00] Fake transcript segment 1.")
    pdf_text = (await client.get(f"/api/sources/{by_name['text.pdf']['id']}/text")).text
    assert "[p. 1]" in pdf_text and "[p. 2]" in pdf_text


async def test_cache_hit_on_same_file(client: httpx.AsyncClient) -> None:
    sid = await _session(client)
    first = (await _upload(client, sid, "notes.md"))[0]
    await wait_for(client, f"/api/sources/{first['id']}", TERMINAL)
    second = (await _upload(client, sid, "notes.md"))[0]
    done = await wait_for(client, f"/api/sources/{second['id']}", TERMINAL)
    assert done["status"] == "ready" and done["meta"].get("cache_hit") is True
    assert done["sha256"] == first["sha256"]


async def test_scanned_pdf_without_ocr(client: httpx.AsyncClient) -> None:
    sid = await _session(client)
    src = (await _upload(client, sid, "scanned.pdf"))[0]
    done = await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)
    if ocr.ocr_available():
        pytest.skip("OCR available; this test covers the disabled path")
    assert done["status"] == "failed"
    assert "OCR" in done["error"]


async def test_unsupported_type_rejected(client: httpx.AsyncClient) -> None:
    sid = await _session(client)
    r = await client.post(f"/api/sessions/{sid}/sources", files=[("files", ("x.exe", b"MZ"))])
    assert r.status_code == 400 and "Unsupported" in r.json()["detail"]


async def test_add_by_path_and_remove(client: httpx.AsyncClient) -> None:
    sid = await _session(client)
    r = await client.post(
        f"/api/sessions/{sid}/sources/by-path", json={"paths": [str(FIXTURES / "notes.md")]}
    )
    assert r.status_code == 201, r.text
    src = r.json()[0]
    assert src["managed"] is False
    await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)
    assert (await client.delete(f"/api/sources/{src['id']}")).status_code == 204
    assert (FIXTURES / "notes.md").exists()  # never deletes files referenced in place
    bad = await client.post(f"/api/sessions/{sid}/sources/by-path", json={"paths": ["/etc/hosts"]})
    assert bad.status_code == 400


async def test_message_waits_for_sources_then_streams_and_exports(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    sid = await _session(client)
    await _upload(client, sid, "text.pdf", "notes.md")
    r = await client.post(
        f"/api/sessions/{sid}/messages", json={"content": "Summarize everything."}
    )
    assert r.status_code == 202
    message = r.json()
    assert message["status"] in ("pending", "waiting_for_sources", "streaming", "done")
    done = await wait_for(client, f"/api/messages/{message['id']}", {"done", "failed", "cancelled"})
    assert done["status"] == "done", done
    assert done["strategy"] == "single_shot"
    assert "Fake answer" in done["content"]
    assert "text.pdf" in done["content"] and "notes.md" in done["content"]
    for fmt, magic in (("txt", None), ("md", None), ("pdf", b"%PDF-"), ("docx", b"PK")):
        a = await client.post(f"/api/messages/{message['id']}/exports", json={"format": fmt})
        assert a.status_code == 201, a.text
        d = await client.get(f"/api/artifacts/{a.json()['id']}")
        assert d.status_code == 200
        assert a.json()["filename"] in d.headers["content-disposition"]
        if magic:
            assert d.content[: len(magic)] == magic
        else:
            assert "Fake answer" in d.text
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert len(detail["artifacts"]) == 4 and len(detail["messages"]) == 2


async def test_map_reduce_when_over_budget(tmp_path: Path) -> None:
    from raida.api.app import create_app
    from tests.conftest import make_config

    config = make_config(
        tmp_path,
        RAIDA_LLM__SYNTHESIS_BUDGET_TOKENS="4000",
        RAIDA_LLM__MAP_CHUNK_TOKENS="2000",
        RAIDA_LLM__CONDENSATION_TARGET_TOKENS="500",
    )
    big = tmp_path / "big.md"
    big.write_text(
        "\n\n".join(f"Paragraph {i}: " + "lorem ipsum dolor sit amet " * 30 for i in range(80))
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        sid = await _session(client)
        r = await client.post(
            f"/api/sessions/{sid}/sources", files=[("files", ("big.md", big.read_bytes()))]
        )
        src = r.json()[0]
        ready = await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)
        assert ready["token_estimate"] > 4000
        m = (
            await client.post(f"/api/sessions/{sid}/messages", json={"content": "Summarize."})
        ).json()
        done = await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
        assert done["status"] == "done", done
        assert done["strategy"] == "map_reduce"
        fake = app.state.raida.scheduler.llm
        assert len(fake.calls) > 2  # condensation calls plus the final synthesis


async def test_sse_snapshot_and_events(live_server: str) -> None:
    received: list[tuple[str, dict]] = []
    async with httpx.AsyncClient(base_url=live_server, timeout=30) as client:
        sid = await _session(client)

        async def consume() -> None:
            async with client.stream("GET", f"/api/sessions/{sid}/events") as resp:
                assert resp.status_code == 200
                event = None
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event:
                        received.append((event, json.loads(line.split(":", 1)[1])))
                        data = received[-1][1]
                        if (
                            event == "message.updated"
                            and data.get("role") == "assistant"
                            and data.get("status") == "done"
                        ):
                            return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        await _upload(client, sid, "notes.md")
        await client.post(f"/api/sessions/{sid}/messages", json={"content": "Go"})
        await asyncio.wait_for(task, timeout=30)
    types = [t for t, _ in received]
    assert types[0] == "snapshot"
    assert "source.updated" in types and "message.delta" in types
    assert any(t == "source.updated" and d["status"] == "ready" for t, d in received)


async def test_cancel_message_and_delete_session(client: httpx.AsyncClient) -> None:
    sid = await _session(client)
    m = (await client.post(f"/api/sessions/{sid}/messages", json={"content": "Hello"})).json()
    r = await client.post(f"/api/messages/{m['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] in ("cancelled", "done")
    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 204
    assert (await client.get(f"/api/sessions/{sid}")).status_code == 404
