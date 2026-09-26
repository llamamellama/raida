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
    assert r.headers["cache-control"] == "no-cache"
    script = await client.get("/static/app.js")
    assert script.status_code == 200 and script.headers["cache-control"] == "no-cache"
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


async def test_regional_language_code_reaches_the_transcriber(client: httpx.AsyncClient) -> None:
    """'zh-TW' must not be reduced to 'zh' on the way to the backend: the Apple engine writes
    Traditional or Simplified characters depending on the exact locale."""
    sid = await _session(client)
    (src,) = await _upload(client, sid, "tone.wav", language="zh-TW")
    done = await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)
    assert done["status"] == "ready", done["error"]
    assert done["language"] == "zh-TW"
    assert done["meta"]["detected_language"] == "zh-TW"


async def test_library_shares_sources_across_sessions(client: httpx.AsyncClient) -> None:
    first = await _session(client)
    (src,) = await _upload(client, first, "notes.md", language="en")
    await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)

    library = (await client.get("/api/library")).json()
    entry = next(e for e in library if e["sha256"] == src["sha256"])
    assert entry["original_name"] == "notes.md"
    assert entry["session_ids"] == [first]

    second = await _session(client)
    r = await client.post(
        f"/api/sessions/{second}/sources/from-library", json={"sha256s": [src["sha256"]]}
    )
    assert r.status_code == 201, r.text
    (copy,) = r.json()
    assert copy["session_id"] == second and copy["language"] == "en"
    done = await wait_for(client, f"/api/sources/{copy['id']}", TERMINAL)
    assert done["status"] == "ready" and done["meta"].get("cache_hit") is True

    # Adding the same file again to the same session returns the existing source.
    again = await client.post(
        f"/api/sessions/{second}/sources/from-library", json={"sha256s": [src["sha256"]]}
    )
    assert again.status_code == 201 and again.json()[0]["id"] == copy["id"]
    library = (await client.get("/api/library")).json()
    entry = next(e for e in library if e["sha256"] == src["sha256"])
    assert sorted(entry["session_ids"]) == sorted([first, second])

    # Removing the copy leaves the original session's file in place.
    assert (await client.delete(f"/api/sources/{copy['id']}")).status_code == 204
    assert (await client.get(f"/api/sources/{src['id']}")).status_code == 200
    assert (await client.get(f"/api/sources/{src['id']}/text")).status_code == 200

    unknown = await client.post(
        f"/api/sessions/{second}/sources/from-library", json={"sha256s": ["0" * 64]}
    )
    assert unknown.status_code == 404


async def test_session_named_automatically_after_first_answer(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/sessions", json={})
    assert r.status_code == 201
    session = r.json()
    assert session["title"].startswith("Session ") and session["title_auto"] is True
    sid = session["id"]
    await _upload(client, sid, "notes.md")
    m = (await client.post(f"/api/sessions/{sid}/messages", json={"content": "Summarize."})).json()
    await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["session"]["title"] == "Fake title"  # what the fake backend returns

    # The user's own title wins, before or after the first answer.
    r = await client.patch(f"/api/sessions/{sid}", json={"title": "Mine"})
    assert r.json()["title"] == "Mine" and r.json()["title_auto"] is False
    m2 = (await client.post(f"/api/sessions/{sid}/messages", json={"content": "Again."})).json()
    await wait_for(client, f"/api/messages/{m2['id']}", {"done", "failed", "cancelled"})
    assert (await client.get(f"/api/sessions/{sid}")).json()["session"]["title"] == "Mine"

    named = (await client.post("/api/sessions", json={"title": "Chosen"})).json()
    assert named["title"] == "Chosen" and named["title_auto"] is False


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
            await client.post(
                f"/api/sessions/{sid}/messages",
                json={"content": "Summarize.", "full_text": True},
            )
        ).json()
        done = await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
        assert done["status"] == "done", done
        assert done["strategy"] == "map_reduce"
        assert done["full_text"] is True
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


def _long_transcript(paragraphs: int = 60) -> bytes:
    lines = [
        f"[00:{i // 60:02d}:{i % 60:02d}] Paragraph {i} is about topic {i % 5}. " + "words " * 60
        for i in range(paragraphs)
    ]
    return "\n\n".join(lines).encode()


def _notes_config(tmp_path: Path, **extra: str):
    from tests.conftest import make_config

    return make_config(
        tmp_path,
        RAIDA_NOTES__MIN_SOURCE_TOKENS="1000",
        RAIDA_NOTES__SECTION_TOKENS="1500",
        RAIDA_LLM__INTERACTIVE_BUDGET_TOKENS="4000",
        RAIDA_LLM__PASSAGE_BUDGET_TOKENS="500",
        **extra,
    )


async def test_long_source_is_noted_once_and_answered_from_notes(tmp_path: Path) -> None:
    from raida.api.app import create_app

    app = create_app(_notes_config(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        fake = app.state.raida.scheduler.llm
        sid = await _session(client)
        r = await client.post(
            f"/api/sessions/{sid}/sources", files=[("files", ("talk.md", _long_transcript()))]
        )
        src = await wait_for(client, f"/api/sources/{r.json()[0]['id']}", TERMINAL)
        assert src["status"] == "ready", src["error"]
        assert src["meta"]["notes"]["status"] == "done" and src["meta"]["notes"]["sections"] > 1
        notes = (await client.get(f"/api/sources/{src['id']}/notes")).text
        assert "Fake overview" in notes and "Fake note 1" in notes and "## [00:00:00]" in notes
        note_calls = [c for c in fake.calls if "<excerpt>" in c[-1]["content"]]
        assert note_calls and all(o.think is False for o in fake.options[: len(note_calls)])

        # A second session reuses the processed text and the notes: no new note calls.
        calls_before = len(fake.calls)
        sid2 = await _session(client)
        added = await client.post(
            f"/api/sessions/{sid2}/sources/from-library", json={"sha256s": [src["sha256"]]}
        )
        again = await wait_for(client, f"/api/sources/{added.json()[0]['id']}", TERMINAL)
        assert again["meta"]["notes"]["status"] == "done"
        assert len(fake.calls) == calls_before

        # The session was read into the prompt cache once its sources settled.
        for _ in range(100):
            if fake.prefills:
                break
            await asyncio.sleep(0.02)
        assert fake.prefills and 'form="notes"' in fake.prefills[-1][1]["content"]

        prepared = list(fake.prefills)
        m = (
            await client.post(
                f"/api/sessions/{sid2}/messages", json={"content": "What is said about topic 3?"}
            )
        ).json()
        done = await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
        assert done["status"] == "done", done
        assert done["strategy"] == "notes"
        answer_prompt = next(
            c for c in reversed(fake.calls) if c[0]["content"].startswith("You are raida")
        )
        assert 'form="notes"' in answer_prompt[1]["content"]
        assert '<excerpt source="1">' in answer_prompt[-1]["content"]
        assert "topic 3" in answer_prompt[-1]["content"]
        # The prefix read ahead is exactly the start of the answer's prompt.
        assert prepared[-1][:-1] == answer_prompt[:-1]
        # After the answer, the prefix with this turn in the history is read ahead too.
        for _ in range(100):
            if len(fake.prefills) > len(prepared):
                break
            await asyncio.sleep(0.02)
        assert fake.prefills[-1][-2]["role"] == "assistant"
        assert fake.prefills[-1][-2]["content"] == done["content"]


async def test_long_source_without_notes_is_read_in_full(tmp_path: Path) -> None:
    from raida.api.app import create_app

    app = create_app(_notes_config(tmp_path, RAIDA_NOTES__ENABLED="false"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        sid = await _session(client)
        r = await client.post(
            f"/api/sessions/{sid}/sources", files=[("files", ("talk.md", _long_transcript()))]
        )
        src = await wait_for(client, f"/api/sources/{r.json()[0]['id']}", TERMINAL)
        assert "notes" not in src["meta"]
        assert (await client.get(f"/api/sources/{src['id']}/notes")).status_code == 404
        m = (await client.post(f"/api/sessions/{sid}/messages", json={"content": "Go"})).json()
        done = await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
        assert done["status"] == "done", done
        assert done["strategy"] == "single_shot"


async def test_answers_and_titles_keep_the_instructions_chinese_script(
    client: httpx.AsyncClient,
) -> None:
    fake = client.app.state.raida.scheduler.llm  # type: ignore[attr-defined]
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await _upload(client, sid, "notes.md", language="zh-TW")
    m = (
        await client.post(f"/api/sessions/{sid}/messages", json={"content": "請整理這些內容的重點"})
    ).json()
    done = await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
    assert done["status"] == "done", done
    # The fake answers in Simplified; the stored answer is Traditional (Taiwan).
    assert "我們的內容，頭髮" in done["content"] and "我们" not in done["content"]
    answer_prompt = next(c for c in fake.calls if c[0]["content"].startswith("You are raida"))
    assert answer_prompt[-1]["content"].endswith("（若以中文回答，請使用繁體字與臺灣用語。）")
    title = ""
    for _ in range(200):
        title = (await client.get(f"/api/sessions/{sid}")).json()["session"]["title"]
        if title.startswith("Fake title"):
            break
        await asyncio.sleep(0.02)
    assert title == "Fake title 簡體標題"


async def test_english_instruction_over_english_sources_is_left_as_written(
    client: httpx.AsyncClient,
) -> None:
    sid = await _session(client)
    await _upload(client, sid, "notes.md")
    m = (await client.post(f"/api/sessions/{sid}/messages", json={"content": "Summarize"})).json()
    done = await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
    assert "我们的内容" in done["content"]  # no Chinese script to follow: unchanged
