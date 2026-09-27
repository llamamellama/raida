from __future__ import annotations

import asyncio
import json
import re
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
    assert r.status_code == 200 and "<title>Raida</title>" in r.text
    assert r.headers["cache-control"] == "no-cache"
    # Assets load from a path that changes with their content, so an update is never mixed
    # with modules a browser cached from an older version.
    versioned = re.search(r'src="/static/([0-9a-f]{12})/app\.js"', r.text)
    assert versioned is not None
    # The page carries the same version, which it compares with the server's on reconnect.
    assert f'<meta name="raida-ui-version" content="{versioned.group(1)}">' in r.text
    script = await client.get(f"/static/{versioned.group(1)}/app.js")
    assert script.status_code == 200 and script.headers["cache-control"] == "no-cache"
    assert (await client.get("/static/app.js")).status_code == 200
    assert (await client.get("/static/vendor/marked.umd.js")).status_code == 200


def _copy_ui(target: Path) -> Path:
    import shutil

    from raida.api.app import web_root

    shutil.copytree(web_root(), target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def _edit_ui(web: Path) -> None:
    (web / "app.js").write_text("// edited while the server runs\n")
    (web / "index.html").write_text("<p>edited</p>")


async def test_ui_is_served_as_it_was_when_the_server_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running server once served scripts edited on disk under its older page; they looked
    for elements the page did not have and stopped before listing any session. Edited files
    are served after a restart, with the server code that matches them."""
    from raida.api import app as app_module
    from tests.conftest import make_config

    web = _copy_ui(tmp_path / "web")
    monkeypatch.setattr(app_module, "web_root", lambda: web)
    app = app_module.create_app(make_config(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        page = (await client.get("/")).text
        found = re.search(r'src="/static/([0-9a-f]{12})/app\.js"', page)
        assert found is not None
        version = found.group(1)
        script = await client.get(f"/static/{version}/app.js")
        assert script.headers["content-type"].startswith("text/javascript")
        _edit_ui(web)
        assert (await client.get(f"/static/{version}/app.js")).text == script.text
        assert (await client.get("/static/app.js")).text == script.text
        assert (await client.get("/")).text == page
        again = await client.get(
            f"/static/{version}/app.js", headers={"If-None-Match": f'"{version}"'}
        )
        assert again.status_code == 304
        assert (await client.get("/static/missing.js")).status_code == 404


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


def _jobs(client: httpx.AsyncClient, source_id: str) -> list[str]:
    return [j.stage for j in client.app.state.raida.db.list_jobs(source_id)]  # type: ignore[attr-defined]


def _exists(path: str) -> bool:
    return Path(path).exists()


def _files_beside(path: str) -> list[str]:
    return sorted(p.name for p in Path(path).parent.iterdir())


async def test_library_shares_sources_across_sessions(client: httpx.AsyncClient) -> None:
    first = await _session(client)
    (src,) = await _upload(client, first, "notes.md", language="en")
    await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)

    library = (await client.get("/api/library")).json()
    entry = next(e for e in library if e["id"] == src["id"])
    assert entry["original_name"] == "notes.md" and entry["sessions"] == 1

    # A new session starts with no sources; it can use any file in the library, which is
    # ready at once because nothing is processed again.
    second = await _session(client)
    assert (await client.get(f"/api/sessions/{second}")).json()["sources"] == []
    r = await client.put(f"/api/sessions/{second}/sources/{src['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == src["id"] and r.json()["status"] == "ready"
    assert r.json()["sessions"] == 2 and r.json()["language"] == "en"
    detail = (await client.get(f"/api/sessions/{second}")).json()
    assert [s["id"] for s in detail["sources"]] == [src["id"]]
    again = await client.put(f"/api/sessions/{second}/sources/{src['id']}")
    assert again.status_code == 200 and again.json()["sessions"] == 2

    # The same content uploaded in a third session, under another name, is the same file.
    third = await _session(client)
    files = [("files", ("copy.md", (FIXTURES / "notes.md").read_bytes()))]
    (dup,) = (await client.post(f"/api/sessions/{third}/sources", files=files)).json()
    assert dup["id"] == src["id"] and dup["original_name"] == "notes.md"
    assert dup["status"] == "ready" and dup["sessions"] == 3
    assert _jobs(client, src["id"]).count("extracting") == 1
    assert _files_beside(src["stored_path"]) == ["notes.md"]

    # Removing it from one session leaves it in the library and in the others.
    assert (await client.delete(f"/api/sessions/{second}/sources/{src['id']}")).status_code == 204
    assert (await client.get(f"/api/sessions/{second}")).json()["sources"] == []
    assert (await client.get(f"/api/sources/{src['id']}")).json()["sessions"] == 2
    assert (await client.get(f"/api/sources/{src['id']}/text")).status_code == 200
    gone = await client.delete(f"/api/sessions/{second}/sources/{src['id']}")
    assert gone.status_code == 404
    unknown = await client.put(f"/api/sessions/{second}/sources/{'0' * 32}")
    assert unknown.status_code == 404


async def test_renaming_a_source(client: httpx.AsyncClient) -> None:
    """A file can be given another name, in every session. The model is given the new name,
    so answers cite it; an empty name brings the file's own name back."""
    first, second = await _session(client), await _session(client)
    (src,) = await _upload(client, first, "notes.md")
    await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)
    await client.put(f"/api/sessions/{second}/sources/{src['id']}")
    assert src["title"] == "notes.md" == src["original_name"]
    jobs_before = _jobs(client, src["id"])

    r = await client.patch(f"/api/sources/{src['id']}", json={"title": "  Team\nnotes  2026 "})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Team notes 2026" and r.json()["original_name"] == "notes.md"
    for sid in (first, second):
        (listed,) = (await client.get(f"/api/sessions/{sid}")).json()["sources"]
        assert listed["title"] == "Team notes 2026"
    assert (await client.get("/api/library")).json()[0]["title"] == "Team notes 2026"
    assert _jobs(client, src["id"]) == jobs_before  # a name only: nothing processed again

    m = (await client.post(f"/api/sessions/{second}/messages", json={"content": "Go"})).json()
    done = await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
    assert "- Team notes 2026" in done["content"] and "notes.md" not in done["content"]

    reset = await client.patch(f"/api/sources/{src['id']}", json={"title": ""})
    assert reset.json()["title"] == "notes.md"
    too_long = await client.patch(f"/api/sources/{src['id']}", json={"title": "x" * 201})
    assert too_long.status_code == 422
    assert (await client.patch(f"/api/sources/{src['id']}", json={})).status_code == 422
    missing = await client.patch(f"/api/sources/{'0' * 32}", json={"title": "x"})
    assert missing.status_code == 404


async def test_a_file_in_two_sessions_is_processed_once(client: httpx.AsyncClient) -> None:
    """A recording dropped into a second session while it is still being transcribed shares
    that run, and a question there waits for it."""
    scheduler = client.app.state.raida.scheduler  # type: ignore[attr-defined]
    first, second = await _session(client), await _session(client)
    await scheduler.resources.gpu.acquire()  # holds the transcription back
    try:
        (src,) = await _upload(client, first, "tone.wav", language="en")
        (same,) = await _upload(client, second, "tone.wav", language="en")
        assert same["id"] == src["id"] and same["status"] not in TERMINAL
        m = (await client.post(f"/api/sessions/{second}/messages", json={"content": "Go"})).json()
        await wait_for(client, f"/api/messages/{m['id']}", {"waiting_for_sources"})
    finally:
        scheduler.resources.gpu.release()
    done = await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
    assert done["status"] == "done", done
    assert "tone.wav" in done["content"]
    assert _jobs(client, src["id"]).count("transcribing") == 1


async def test_deleting_a_session_keeps_its_files(client: httpx.AsyncClient) -> None:
    sid = await _session(client)
    (src,) = await _upload(client, sid, "notes.md")
    await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)
    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 204
    kept = (await client.get(f"/api/sources/{src['id']}")).json()
    assert kept["status"] == "ready" and kept["sessions"] == 0
    assert _exists(src["stored_path"])
    assert src["id"] in {s["id"] for s in (await client.get("/api/library")).json()}
    other = await _session(client)
    used = await client.put(f"/api/sessions/{other}/sources/{src['id']}")
    assert used.json()["status"] == "ready"


async def test_deleting_from_the_library_removes_the_file_everywhere(
    client: httpx.AsyncClient,
) -> None:
    a, b = await _session(client), await _session(client)
    (src,) = await _upload(client, a, "notes.md")
    ready = await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)
    await client.put(f"/api/sessions/{b}/sources/{src['id']}")
    assert _exists(ready["processed_path"])

    assert (await client.delete(f"/api/sources/{src['id']}")).status_code == 204
    for sid in (a, b):
        assert (await client.get(f"/api/sessions/{sid}")).json()["sources"] == []
    assert (await client.get(f"/api/sources/{src['id']}")).status_code == 404
    assert not _exists(src["stored_path"]) and not _exists(ready["processed_path"])
    assert (await client.get("/api/library")).json() == []

    # Added again, it is processed from the start: nothing of it was kept.
    (again,) = await _upload(client, a, "notes.md")
    done = await wait_for(client, f"/api/sources/{again['id']}", TERMINAL)
    assert done["status"] == "ready" and not done["meta"].get("cache_hit")


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


async def test_same_file_twice_in_a_session_is_one_source(client: httpx.AsyncClient) -> None:
    sid = await _session(client)
    first = (await _upload(client, sid, "notes.md"))[0]
    await wait_for(client, f"/api/sources/{first['id']}", TERMINAL)
    second = (await _upload(client, sid, "notes.md"))[0]
    assert second["id"] == first["id"] and second["status"] == "ready"
    assert len((await client.get(f"/api/sessions/{sid}")).json()["sources"]) == 1


async def test_cache_hit_when_switching_back_to_a_language(client: httpx.AsyncClient) -> None:
    """Processed text is cached per content and language: going back to a language used
    before is not transcribed again."""
    sid = await _session(client)
    (src,) = await _upload(client, sid, "tone.wav", language="en")
    await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)
    for language in ("fr", "en"):
        await client.patch(f"/api/sources/{src['id']}", json={"language": language})
        done = await wait_for(client, f"/api/sources/{src['id']}", TERMINAL)
        assert done["status"] == "ready" and done["language"] == language
    assert done["meta"].get("cache_hit") is True
    assert _jobs(client, src["id"]).count("transcribing") == 2


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
    # Deleted from the library: raida's text goes, the user's own file stays where it is.
    assert (await client.delete(f"/api/sources/{src['id']}")).status_code == 204
    assert (FIXTURES / "notes.md").exists()
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
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
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
    assert received[0][1]["library"] == [] and received[0][1]["sources"] == []
    assert types[1] == "app.version" and re.fullmatch(r"[0-9a-f]{12}", received[1][1]["ui"])
    assert "source.updated" in types and "message.delta" in types
    assert any(t == "source.updated" and d["status"] == "ready" for t, d in received)
    listed = next(d for t, d in received if t == "session.sources")
    assert listed["session_id"] == sid and len(listed["source_ids"]) == 1


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
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
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
        added = (await client.put(f"/api/sessions/{sid2}/sources/{src['id']}")).json()
        assert added["status"] == "ready" and added["meta"]["notes"]["status"] == "done"
        assert len(fake.calls) == calls_before

        # The session was read into the prompt cache as soon as it had the file.
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
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
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


async def test_a_question_waits_for_its_sessions_read_ahead(tmp_path: Path) -> None:
    """Cancelling the read-ahead of the very prefix a question needs made the question start
    over on another server slot; the question lets it finish instead."""
    from raida.api.app import create_app

    app = create_app(_notes_config(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        scheduler = app.state.raida.scheduler
        fake = scheduler.llm
        sid = await _session(client)
        r = await client.post(
            f"/api/sessions/{sid}/sources", files=[("files", ("talk.md", _long_transcript()))]
        )
        await wait_for(client, f"/api/sources/{r.json()[0]['id']}", TERMINAL)
        # No tab has the session open, so the finished file did not start a read-ahead.
        await asyncio.sleep(0.1)
        assert fake.prefills_done == 0
        done_before = fake.prefills_done
        fake.prefill_delay_s = 0.5
        scheduler.schedule_prepare(sid)  # as when the session is opened
        await asyncio.sleep(0.05)
        m = (await client.post(f"/api/sessions/{sid}/messages", json={"content": "Go"})).json()
        done = await wait_for(client, f"/api/messages/{m['id']}", {"done", "failed", "cancelled"})
        assert done["status"] == "done", done
        assert fake.prefills_done == done_before + 1  # finished, not cancelled
        assert scheduler.resources.llm.preemptions == 0
