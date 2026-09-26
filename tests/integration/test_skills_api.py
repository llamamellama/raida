"""Skills through the HTTP API: managing them, importing and exporting, running them."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

from tests.conftest import FIXTURES, wait_for

DONE = {"done", "failed", "cancelled"}
SKILL = {
    "name": "study-notes",
    "title": "重點整理",
    "description": "整理重點並給出練習步驟",
    "instructions": "請整理來源的重點，特別是：$ARGUMENTS",
    "example": "# 標題\n\n## 重點\n- 重點一 [00:00:00]",
    "reference": "正確名稱：賽斯心法（不是 賽事心法）",
    "argument_hint": "要著重的主題",
}


async def _session(client: httpx.AsyncClient, source: str = "notes.md", **params: str) -> str:
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    files = [("files", (source, (FIXTURES / source).read_bytes()))]
    r = await client.post(f"/api/sessions/{sid}/sources", files=files, params=params)
    await wait_for(client, f"/api/sources/{r.json()[0]['id']}", {"ready", "failed"})
    return sid


async def _ask(client: httpx.AsyncClient, sid: str, content: str, **extra: object) -> dict:
    r = await client.post(f"/api/sessions/{sid}/messages", json={"content": content, **extra})
    assert r.status_code == 202, r.text
    return await wait_for(client, f"/api/messages/{r.json()['id']}", DONE)


def _answer_prompt(client: httpx.AsyncClient) -> list[dict[str, str]]:
    fake = client.app.state.raida.scheduler.llm  # type: ignore[attr-defined]
    return next(c for c in reversed(fake.calls) if c[0]["content"].startswith("You are raida"))


async def test_manage_skills(client: httpx.AsyncClient) -> None:
    listing = (await client.get("/api/skills")).json()
    builtin = {s["name"]: s for s in listing["skills"]}
    assert builtin["summary"]["origin"] == "builtin" and listing["problems"] == []

    r = await client.post("/api/skills", json=SKILL)
    assert r.status_code == 201, r.text
    assert r.json()["origin"] == "user" and r.json()["language"] == "instructions"
    assert (await client.post("/api/skills", json=SKILL)).status_code == 409
    assert (await client.get("/api/skills/study-notes")).json()["reference"] == SKILL["reference"]

    bad = await client.post("/api/skills", json={**SKILL, "name": "Study Notes"})
    assert bad.status_code == 400 and "lowercase" in bad.json()["detail"]
    empty = await client.post("/api/skills", json={**SKILL, "name": "x", "instructions": " "})
    assert empty.status_code == 400 and "instructions" in empty.json()["detail"]
    unknown_field = await client.post("/api/skills", json={**SKILL, "name": "y", "color": "red"})
    assert unknown_field.status_code == 422

    renamed = await client.put("/api/skills/study-notes", json={**SKILL, "name": "notes"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "notes"
    assert (await client.get("/api/skills/study-notes")).status_code == 404

    override = await client.put(
        "/api/skills/summary", json={**SKILL, "name": "summary", "language": "sources"}
    )
    assert override.json()["origin"] == "override"
    keep_name = await client.put("/api/skills/summary", json={**SKILL, "name": "renamed"})
    assert keep_name.status_code == 400 and "keeps its name" in keep_name.json()["detail"]
    reset = (await client.delete("/api/skills/summary")).json()
    assert reset["restored"]["origin"] == "builtin"
    assert (await client.delete("/api/skills/summary")).status_code == 400
    assert (await client.delete("/api/skills/notes")).json() == {
        "deleted": "notes",
        "restored": None,
    }
    assert (await client.delete("/api/skills/notes")).status_code == 404


async def test_import_and_export(client: httpx.AsyncClient) -> None:
    claude = "---\nname: brief\ndescription: A one-paragraph brief.\nlicense: MIT\n---\nBe brief."
    r = await client.post(
        "/api/skills/import", files={"file": ("SKILL.md", claude.encode(), "text/markdown")}
    )
    assert r.status_code == 201, r.text
    assert r.json()["skill"]["name"] == "brief" and r.json()["ignored"] == []
    again = await client.post("/api/skills/import", files={"file": ("SKILL.md", claude.encode())})
    assert again.status_code == 409
    replaced = await client.post(
        "/api/skills/import",
        params={"replace": "true"},
        files={"file": ("SKILL.md", claude.replace("Be brief.", "Be very brief.").encode())},
    )
    assert replaced.json()["skill"]["instructions"] == "Be very brief."

    exported = await client.get("/api/skills/brief/export")
    assert exported.headers["content-type"] == "application/zip"
    assert 'filename="brief.zip"' in exported.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        text = archive.read("brief/SKILL.md").decode()
    assert "license: MIT" in text and "Be very brief." in text
    await client.delete("/api/skills/brief")
    round_trip = await client.post(
        "/api/skills/import", files={"file": ("brief.zip", exported.content, "application/zip")}
    )
    assert round_trip.json()["skill"]["instructions"] == "Be very brief."
    wrong = await client.post("/api/skills/import", files={"file": ("notes.txt", b"hello")})
    assert wrong.status_code == 400
    huge = await client.post(
        "/api/skills/import", files={"file": ("big.zip", b"x" * (3 * 1024 * 1024))}
    )
    assert huge.status_code == 400 and "larger than 2 MB" in huge.json()["detail"]


async def test_a_skill_runs_in_any_session(client: httpx.AsyncClient) -> None:
    await client.post("/api/skills", json=SKILL)
    for _ in range(2):
        sid = await _session(client, language="zh-TW")
        done = await _ask(client, sid, "@study-notes 日常練習")
        assert done["status"] == "done", done
        assert done["skill"]["name"] == "study-notes"
        messages = (await client.get(f"/api/sessions/{sid}")).json()["messages"]
        user = next(m for m in messages if m["role"] == "user")
        assert user["content"] == "@study-notes 日常練習"
        assert user["skill"]["arguments"] == "日常練習"
        assert user["prompt"].startswith("請整理來源的重點，特別是：日常練習")
        final = _answer_prompt(client)[-1]["content"]
        assert "請整理來源的重點，特別是：日常練習" in final
        assert "<example>\n# 標題" in final and "<reference>\n正確名稱" in final
        assert final.endswith("（若以中文回答，請使用繁體字與臺灣用語。）")
    # Editing the skill later does not change what an earlier run recorded.
    await client.put("/api/skills/study-notes", json={**SKILL, "instructions": "Changed."})
    user = next(
        m
        for m in (await client.get(f"/api/sessions/{sid}")).json()["messages"]
        if m["role"] == "user"
    )
    assert user["prompt"].startswith("請整理來源的重點")


async def test_skill_options_and_follow_up(client: httpx.AsyncClient) -> None:
    fake = client.app.state.raida.scheduler.llm  # type: ignore[attr-defined]
    await client.post(
        "/api/skills",
        json={**SKILL, "name": "quick", "think": False, "example": "x " * 400},
    )
    await client.post("/api/skills", json={**SKILL, "name": "thorough", "full_text": True})
    sid = await _session(client)
    done = await _ask(client, sid, "@quick")
    assert done["full_text"] is False and fake.options[-1].think is False
    done = await _ask(client, sid, "@thorough")
    assert done["full_text"] is True and done["strategy"] == "single_shot"
    assert fake.options[-1].think is None
    # A follow-up sees the earlier run as its command, not its long prompt.
    await _ask(client, sid, "Make it shorter")
    turns = [m["content"] for m in _answer_prompt(client) if m["role"] == "user"]
    assert '@quick\n(Ran the skill "重點整理": 整理重點並給出練習步驟)' in turns
    assert not any("x x x x" in t for t in turns[:-1] if t.startswith("@"))


async def test_unknown_skill_and_escaped_at_sign(client: httpx.AsyncClient) -> None:
    sid = await _session(client)
    r = await client.post(f"/api/sessions/{sid}/messages", json={"content": "@nope"})
    assert r.status_code == 400
    assert "no skill named @nope" in r.json()["detail"] and "@summary" in r.json()["detail"]
    done = await _ask(client, sid, "@@nope is not a skill")
    user = next(
        m
        for m in (await client.get(f"/api/sessions/{sid}")).json()["messages"]
        if m["role"] == "user"
    )
    assert user["content"] == "@nope is not a skill" and user["skill"] is None
    assert done["skill"] is None and done["status"] == "done"
    # Not a skill at all: a capitalized name, or a path now that the slash runs nothing.
    for content in ("@Anna asked about the budget", "/summary of /Users/me/notes.md"):
        done = await _ask(client, sid, content)
        assert done["skill"] is None and done["status"] == "done"


async def test_builtin_skill_answers_in_the_sources_language(client: httpx.AsyncClient) -> None:
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    text = "[00:00:00] 我們的力量來自於內在，真正的安全感也來自內在。\n\n" * 20
    r = await client.post(
        f"/api/sessions/{sid}/sources",
        files=[("files", ("talk.md", text.encode()))],
        params={"language": "zh-TW"},
    )
    await wait_for(client, f"/api/sources/{r.json()[0]['id']}", {"ready"})
    done = await _ask(client, sid, "@summary")
    final = _answer_prompt(client)[-1]["content"]
    assert final.startswith("Summarize the sources in this session.")
    assert final.endswith("請用繁體中文（臺灣用語）撰寫回答。")
    # The fake answers with a Simplified line; the answer is held to Traditional.
    assert "我們的內容" in done["content"] and "我们" not in done["content"]


async def test_skill_changes_reach_open_tabs(live_server: str) -> None:
    received: list[dict] = []
    async with httpx.AsyncClient(base_url=live_server, timeout=30) as client:
        sid = (await client.post("/api/sessions", json={})).json()["id"]

        async def consume() -> None:
            async with client.stream("GET", f"/api/sessions/{sid}/events") as resp:
                event = None
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event == "skills.updated":
                        received.append(json.loads(line.split(":", 1)[1]))
                        return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        await client.post("/api/skills", json=SKILL)
        await asyncio.wait_for(task, timeout=10)
    assert "study-notes" in {s["name"] for s in received[0]["skills"]}


def test_cli_lists_skills_and_asks_with_one(tmp_path: Path) -> None:
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("RAIDA_")},
        "RAIDA_LLM__MODEL": "fake-model",
        "RAIDA_LLM__BACKEND": "fake",
        "RAIDA_TRANSCRIBE__BACKEND": "fake",
        "RAIDA_OCR__ENABLED": "false",
        "RAIDA_PATHS__DATA_DIR": str(tmp_path / "data"),
    }

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "raida", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    listed = run("skills")
    assert listed.returncode == 0, listed.stderr
    assert "@summary [builtin]  Summary:" in listed.stdout
    shown = run("skills", "show", "meeting-minutes")
    assert shown.stdout.startswith("---\nname: meeting-minutes\n")
    assert "----- assets/example.md -----" in shown.stdout
    assert run("skills", "show", "nope").returncode == 1
    assert run("skills", "show").returncode == 2
    processed = run("process", str(FIXTURES / "notes.md"))
    session_id = processed.stderr.strip().splitlines()[-1].split()[-1]
    asked = run("ask", session_id, "@summary for the team")
    assert asked.returncode == 0, asked.stderr
    assert "Write the answer in the main language of the sources." in asked.stdout
    from raida.db import Database

    db = Database(tmp_path / "data" / "raida.sqlite3")
    try:
        user = next(m for m in db.list_messages(session_id) if m.role == "user")
    finally:
        db.close()
    assert user.skill is not None and user.skill.name == "summary"
    assert user.prompt is not None
    assert "Summarize the sources in this session." in user.prompt
    assert "Additional instructions: for the team" in user.prompt
