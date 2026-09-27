"""Skills: the SKILL.md format, the store, archive import, commands and rendering."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from raida.skills import (
    SkillConflictError,
    SkillError,
    SkillNotFoundError,
    SkillStore,
    build_skill,
    parse_command,
    parse_skill_md,
    render,
    render_skill_md,
    unescape,
)
from raida.skills.render import ENGLISH_ADDITIONS, EXAMPLE_INTRO, REFERENCE_INTRO, history_text
from raida.skills.store import builtin_dir, parse_upload
from raida.transcribe.languages import WRITTEN_LANGUAGE_NAMES

ROOT = Path(__file__).resolve().parents[2]

CLAUDE_SKILL = """---
name: pdf-summary
description: Summarize a document. Use when the user asks for a summary.
license: Apache-2.0
argument-hint: "[focus]"
metadata:
  author: someone
  version: "1.2"
---

# PDF summary

Summarize the document in three parts.
"""


def _skill(**overrides: object):
    fields = {
        "name": "study-notes",
        "title": "重點整理",
        "description": "整理重點與練習",
        "instructions": "請整理重點。",
    }
    fields.update(overrides)
    return build_skill(**fields)


# -- the format -------------------------------------------------------------------------------


def test_round_trip_keeps_every_field_and_unicode() -> None:
    skill = _skill(
        example="# 標題\n\n- 重點",
        reference="賽斯心法: not 賽事心法",
        argument_hint="要著重的主題",
        language="zh-tw",
        full_text=True,
        think=False,
    )
    assert skill.language == "zh-TW"  # canonical code
    text = render_skill_md(skill)
    assert text.startswith("---\nname: study-notes\ndescription: 整理重點與練習\n")
    assert "raida-title: 重點整理" in text and "raida-full-text: 'true'" in text
    again = parse_skill_md(text, example=skill.example, reference=skill.reference)
    assert again.model_dump() == skill.model_dump()


def test_claude_skill_imports_and_keeps_its_own_frontmatter() -> None:
    skill = parse_skill_md(CLAUDE_SKILL)
    assert skill.name == "pdf-summary"
    assert skill.title == "Pdf summary"  # derived from the name
    assert skill.argument_hint == "[focus]"  # Claude Code's top-level field
    assert skill.instructions.startswith("# PDF summary")
    assert skill.language == "instructions" and skill.think and not skill.full_text
    assert skill.extra == {
        "license": "Apache-2.0",
        "metadata": {"author": "someone", "version": "1.2"},
    }
    exported = render_skill_md(skill)
    assert "license: Apache-2.0" in exported and "author: someone" in exported
    assert parse_skill_md(exported).extra == skill.extra


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no frontmatter here", "must start with YAML frontmatter"),
        ("---\nname: [unclosed\n---\nbody", "not valid YAML"),
        ("---\n- a list\n---\nbody", "key: value"),
        ("---\nname: ok\ndescription: d\n---\n   \n", "instructions: must not be empty"),
        ("---\nname: Bad_Name\ndescription: d\n---\nbody", "lowercase letters"),
        ("---\nname: ok\n---\nbody", "description"),
        (
            "---\nname: ok\ndescription: d\nmetadata:\n  raida-think: maybe\n---\nbody",
            "true or false",
        ),
        ("---\nname: ok\ndescription: d\nmetadata:\n  raida-language: klingon\n---\nb", "language"),
    ],
)
def test_invalid_skill_files_are_rejected_with_a_reason(text: str, message: str) -> None:
    with pytest.raises(SkillError, match=re.escape(message)):
        parse_skill_md(text)


def test_limits() -> None:
    with pytest.raises(SkillError, match="description: at most 1024"):
        _skill(description="x" * 1025)
    with pytest.raises(SkillError, match="title: at most 80"):
        _skill(title="x" * 81)
    with pytest.raises(SkillError, match="keep them under"):
        _skill(example="重" * 9000)
    with pytest.raises(SkillError, match="lowercase"):
        _skill(name="-leading")
    with pytest.raises(SkillError, match="lowercase"):
        _skill(name="double--hyphen")
    with pytest.raises(SkillError, match="lowercase"):
        _skill(name="x" * 65)
    assert _skill(name="a").name == "a" and _skill(title="").title == "Study notes"


def test_ui_languages_match_the_server_list() -> None:
    js = (ROOT / "src/raida/web/util.js").read_text("utf-8")
    codes = re.findall(r'\["([a-zA-Z-]+)", "', js.split("export function")[0])
    assert [c for c in codes if c != "auto"] == list(WRITTEN_LANGUAGE_NAMES)


def test_builtin_skills_are_valid() -> None:
    store = SkillStore(Path("/nonexistent"), builtin_dir())
    skills, problems = store.list()
    assert problems == []
    assert {s.name for s in skills} >= {"summary", "key-takeaways", "meeting-minutes"}
    for info in skills:
        skill = store.load(info.name)
        assert skill.language == "sources" and info.origin == "builtin"
        # Folders must be named after the skill, as the Agent Skills format requires.
        assert (builtin_dir() / skill.name / "SKILL.md").is_file()


# -- the store --------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SkillStore:
    return SkillStore(tmp_path / "skills")


def test_create_get_update_rename_delete(store: SkillStore) -> None:
    created = store.create(_skill(example="# Example"))
    assert created.origin == "user" and created.example == "# Example"
    folder = store.user_dir / "study-notes"
    assert (folder / "assets" / "example.md").read_text("utf-8") == "# Example\n"
    assert "[assets/example.md](assets/example.md)" in (folder / "SKILL.md").read_text("utf-8")
    with pytest.raises(SkillConflictError):
        store.create(_skill())
    updated = store.update("study-notes", _skill(instructions="新的指示", example=""))
    assert updated.instructions == "新的指示"
    assert not (store.user_dir / "study-notes" / "assets").exists()
    assert "raida: files" not in (store.user_dir / "study-notes" / "SKILL.md").read_text("utf-8")
    renamed = store.update("study-notes", _skill(name="notes"))
    assert renamed.name == "notes" and not (store.user_dir / "study-notes").exists()
    with pytest.raises(SkillNotFoundError):
        store.get("study-notes")
    assert store.delete("notes") is None
    with pytest.raises(SkillNotFoundError):
        store.get("notes")
    assert not [p for p in store.user_dir.iterdir() if p.name.startswith(".")]  # no leftovers


def test_builtin_override_and_reset(store: SkillStore) -> None:
    original = store.get("summary")
    with pytest.raises(SkillConflictError):
        store.create(_skill(name="summary"))
    with pytest.raises(SkillError, match="no changes to discard"):
        store.reset("summary")
    changed = store.update("summary", _skill(name="summary", instructions="Mine."))
    assert changed.origin == "override" and changed.instructions == "Mine."
    with pytest.raises(SkillError, match="keeps its name"):
        store.update("summary", _skill(name="my-summary"))
    restored = store.reset("summary")
    assert restored.origin == "builtin" and restored.instructions == original.instructions
    assert not (store.user_dir / "summary").exists()


def test_builtin_skills_can_be_deleted_and_restored(store: SkillStore) -> None:
    store.delete("summary")
    assert "summary" not in store.names() and store.deleted_builtins() == ["summary"]
    with pytest.raises(SkillNotFoundError):
        store.get("summary")
    with pytest.raises(SkillNotFoundError):
        store.load("summary")  # so @summary no longer runs
    with pytest.raises(SkillNotFoundError):
        store.delete("summary")

    # A changed built-in goes with its changes.
    store.update("article", _skill(name="article", instructions="Mine."))
    store.delete("article")
    assert not (store.user_dir / "article").exists()
    assert store.deleted_builtins() == ["article", "summary"]

    # The name is free for a skill of the user's own, which the built-in does not hide.
    mine = store.create(_skill(name="summary", instructions="My own summary."))
    assert mine.origin == "user" and store.load("summary").instructions == "My own summary."

    assert store.restore_builtins() == ["article", "summary"]
    assert store.deleted_builtins() == [] and not (store.user_dir / ".deleted-builtins").exists()
    assert store.get("article").origin == "builtin"
    assert store.get("summary").origin == "override"  # the user's skill now changes the built-in
    assert store.restore_builtins() == []


def test_a_broken_change_to_a_builtin_can_be_repaired(store: SkillStore) -> None:
    folder = store.user_dir / "summary"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: summary\ndescription: [unclosed\n---\nx", "utf-8")
    skills, problems = store.list()
    assert next(s for s in skills if s.name == "summary").origin == "override"
    assert any("summary" in p.path for p in problems)
    with pytest.raises(SkillError):
        store.load("summary")  # running it says what is wrong instead of using the original
    saved = store.update("summary", _skill(name="summary", instructions="Fixed."))
    assert saved.origin == "override" and store.load("summary").instructions == "Fixed."
    (folder / "SKILL.md").write_text("no frontmatter", "utf-8")
    assert store.reset("summary").origin == "builtin"
    (folder).mkdir()
    (folder / "SKILL.md").write_text("no frontmatter", "utf-8")
    store.delete("summary")
    assert not folder.exists() and "summary" in store.deleted_builtins()


def test_an_unreadable_user_skill_folder_can_be_deleted(store: SkillStore) -> None:
    folder = store.user_dir / "broken"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("no frontmatter", "utf-8")
    store.delete("broken")
    assert not folder.exists() and store.deleted_builtins() == []
    with pytest.raises(SkillNotFoundError):
        store.delete("broken")


def test_hand_edited_and_broken_folders(store: SkillStore) -> None:
    store.user_dir.mkdir(parents=True)
    good = store.user_dir / "handmade"
    good.mkdir()
    (good / "SKILL.md").write_text(
        "---\nname: other-name\ndescription: Made in an editor\n---\nDo it.", "utf-8"
    )
    broken = store.user_dir / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no frontmatter", "utf-8")
    (store.user_dir / "empty").mkdir()
    skills, problems = store.list()
    names = {s.name for s in skills}
    assert "handmade" in names  # the folder name wins over a mismatched frontmatter name
    assert {
        Path(p.path).parent.name if p.path.endswith("SKILL.md") else Path(p.path).name
        for p in problems
    } == {"broken", "empty"}
    assert "frontmatter" in next(p.error for p in problems if "broken" in p.path)


def test_update_keeps_imported_frontmatter(store: SkillStore) -> None:
    store.import_upload("SKILL.md", CLAUDE_SKILL.encode())
    fields = store.get("pdf-summary").model_dump(exclude={"origin", "updated_at"})
    store.update("pdf-summary", build_skill(**{**fields, "instructions": "Edited."}))
    exported = render_skill_md(store.load("pdf-summary"))
    assert "license: Apache-2.0" in exported and "Edited." in exported


# -- import and export ------------------------------------------------------------------------


def _zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_archive_import_reads_markdown_and_lists_the_rest() -> None:
    data = _zip(
        {
            "pdf-summary/SKILL.md": CLAUDE_SKILL,
            "pdf-summary/assets/template.md": "# Template",
            "pdf-summary/references/terms.md": "Term list",
            "pdf-summary/references/style.md": "Style guide",
            "pdf-summary/scripts/run.py": "print('never run')",
            "__MACOSX/pdf-summary/._SKILL.md": b"\x00",
        }
    )
    skill, ignored = parse_upload("pdf-summary.skill", data)
    assert skill.example == "# Template"
    assert skill.reference == "## style.md\n\nStyle guide\n\n## terms.md\n\nTerm list"
    assert ignored == ["pdf-summary/scripts/run.py"]


def test_archive_problems() -> None:
    with pytest.raises(SkillError, match=r"no SKILL\.md"):
        parse_upload("x.zip", _zip({"readme.txt": "hi"}))
    with pytest.raises(SkillError, match="several skills"):
        parse_upload("x.zip", _zip({"a/SKILL.md": CLAUDE_SKILL, "b/SKILL.md": CLAUDE_SKILL}))
    with pytest.raises(SkillError, match=r"not a valid \.zip"):
        parse_upload("x.zip", b"not a zip")
    with pytest.raises(SkillError, match="unpacks to more than"):
        parse_upload("x.zip", _zip({"a/SKILL.md": CLAUDE_SKILL, "a/big.md": "x" * 5_000_000}))
    with pytest.raises(SkillError, match=r"import a SKILL\.md"):
        parse_upload("skill.txt", b"")
    with pytest.raises(SkillError, match="not UTF-8"):
        parse_upload("SKILL.md", "---\nname: a\n".encode("utf-16"))
    # Paths that climb out of the archive are only ever read in memory, never extracted.
    skill, ignored = parse_upload(
        "x.zip", _zip({"ok/SKILL.md": CLAUDE_SKILL, "../../evil.md": "x"})
    )
    assert skill.name == "pdf-summary" and ignored == ["../../evil.md"]


def test_markdown_import_names_the_skill_after_the_file_when_needed() -> None:
    body = "---\ndescription: No name given\n---\nDo it."
    assert parse_upload("weekly-report.md", body.encode())[0].name == "weekly-report"
    with pytest.raises(SkillError, match="lowercase"):
        parse_upload("SKILL.md", body.encode())


def test_import_conflict_and_export_round_trip(store: SkillStore) -> None:
    store.import_upload("SKILL.md", CLAUDE_SKILL.encode())
    with pytest.raises(SkillConflictError):
        store.import_upload("SKILL.md", CLAUDE_SKILL.encode())
    replaced = store.import_upload("SKILL.md", CLAUDE_SKILL.replace("three", "two").encode(), True)
    assert "two parts" in replaced.skill.instructions
    store.create(_skill(example="# 範例", reference="名詞表"))
    data = store.export_zip("study-notes")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert sorted(archive.namelist()) == [
            "study-notes/SKILL.md",
            "study-notes/assets/example.md",
            "study-notes/references/reference.md",
        ]
        skill_md = archive.read("study-notes/SKILL.md").decode()
    # Other tools load the files the SKILL.md body points to.
    assert skill_md.rstrip().endswith("[references/reference.md](references/reference.md).")
    skill, ignored = parse_upload("study-notes.zip", data)
    assert ignored == [] and skill.model_dump() == store.load("study-notes").model_dump()


# -- commands and rendering -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("@summary", ("summary", "")),
        ("  @summary focus on the budget  ", ("summary", "focus on the budget")),
        ("@key-takeaways 著重在練習\n第二行", ("key-takeaways", "著重在練習\n第二行")),
        ("@summary\nnext line", ("summary", "next line")),
        ("@@summary", None),
        ("@Anna asked about the budget", None),
        ("@ summary", None),
        ("@summary/other", None),
        ("@example.com is our domain", None),
        ("summary @summary", None),
        ("/summary", None),  # the slash is not a skill trigger (paths such as /Users/me)
    ],
)
def test_parse_command(content: str, expected: tuple[str, str] | None) -> None:
    command = parse_command(content)
    assert (None if command is None else (command.name, command.arguments)) == expected


def test_unescape() -> None:
    assert unescape("@@team: what was decided?") == "@team: what was decided?"
    assert unescape("plain") == "plain"


def test_render_fills_arguments_and_marks_example_and_reference() -> None:
    skill = _skill(instructions="Summarize. Focus: $ARGUMENTS.", example="# E", reference="R")
    use, prompt = render(skill, "the budget")
    assert use.body == "Summarize. Focus: the budget."
    assert prompt.index(REFERENCE_INTRO) < prompt.index(EXAMPLE_INTRO)
    assert "<reference>\nR\n</reference>" in prompt and "<example>\n# E\n</example>" in prompt
    assert use.arguments == "the budget" and use.title == "重點整理"
    assert len(use.revision) == 12 and use.revision != render(_skill(), "x")[0].revision
    assert use.revision == render(skill, "other arguments")[0].revision
    # Without the placeholder, the text is added with a label in the instructions' script.
    assert render(_skill(), "著重練習")[0].body == "請整理重點。\n\n補充說明：著重練習"
    english = _skill(instructions="Summarize the sources.")
    assert render(english, "focus")[0].body == f"Summarize the sources.\n\n{ENGLISH_ADDITIONS}focus"
    assert render(english, "")[0].body == "Summarize the sources."
    assert render(english, "")[1] == "Summarize the sources."


def test_history_shows_the_command_not_the_prompt() -> None:
    use, _ = render(_skill(example="# long example " * 50), "focus")
    text = history_text("/study-notes focus", use)
    assert text == '/study-notes focus\n(Ran the skill "重點整理": 整理重點與練習)'


def test_folders_in_the_first_layout_are_still_read(store: SkillStore) -> None:
    folder = store.user_dir / "old-layout"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: old-layout\ndescription: d\n---\nDo it.", "utf-8")
    (folder / "example.md").write_text("# Old example", "utf-8")
    (folder / "reference.md").write_text("Old terms", "utf-8")
    skill = store.load("old-layout")
    assert (skill.example, skill.reference) == ("# Old example", "Old terms")
    store.update("old-layout", skill)  # saving moves the files into the format's directories
    assert (folder / "assets" / "example.md").is_file() and not (folder / "example.md").exists()


def test_pointer_block_is_not_part_of_the_instructions() -> None:
    skill = _skill(example="# E", reference="R")
    text = render_skill_md(skill)
    assert "<!-- raida: files -->" in text
    assert parse_skill_md(text, example="# E", reference="R").instructions == "請整理重點。"


def test_a_broken_folder_is_replaced_by_saving_or_importing(store: SkillStore) -> None:
    broken = store.user_dir / "study-notes"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("broken", "utf-8")
    assert store.list()[1]  # reported as a problem
    assert store.create(_skill()).name == "study-notes"
    assert store.list()[1] == []
    (broken / "SKILL.md").write_text("broken again", "utf-8")
    store.import_upload("SKILL.md", render_skill_md(_skill()).encode())
    assert store.get("study-notes").instructions == "請整理重點。"
