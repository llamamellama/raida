"""How a skill run picks the answer's language and Chinese script."""

from __future__ import annotations

import pytest

from raida.llm.synthesis import main_source_language, skill_script
from raida.models import ProcessedSource, SkillUse

TRADITIONAL = "[00:00:00] 我們的力量來自於內在，真正的安全感也來自內在。" * 20
SIMPLIFIED = "[00:00:00] 我们的力量来自于内在，真正的安全感也来自内在。" * 20
ENGLISH = "[p. 1] The committee approved the budget for the new school building. " * 20


def _doc(text: str, tokens: int, **meta: object) -> ProcessedSource:
    return ProcessedSource(
        source_id=str(tokens),
        title="t",
        kind="text",
        meta=meta,
        text_markdown=text,
        token_estimate=tokens,
    )


def _use(language: str, body: str = "Summarize the sources.") -> SkillUse:
    return SkillUse(name="s", title="S", description="d", body=body, language=language)


def test_main_language_weighs_sources_by_length() -> None:
    zh = _doc(TRADITIONAL, 20_000)
    en = _doc(ENGLISH, 5_000)
    assert main_source_language([zh, en], ["zh-TW", "en"]) == "zh-TW"
    assert main_source_language([_doc(ENGLISH, 30_000), zh], ["en", "zh-TW"]) == "en"
    # Left on automatic: Chinese is recognized from the text, the language of speech from
    # detection, anything else is unknown, and unknown can win.
    assert main_source_language([_doc(TRADITIONAL, 100)], ["auto"]) == "zh-Hant"
    assert main_source_language([_doc(SIMPLIFIED, 100)], ["auto"]) == "zh"
    assert main_source_language([_doc(ENGLISH, 100, detected_language="en")], ["auto"]) == "en"
    assert main_source_language([_doc(ENGLISH, 30_000), zh], ["auto", "zh-TW"]) is None
    assert main_source_language([], []) is None


@pytest.mark.parametrize(
    ("language", "sources", "languages", "target", "directive"),
    [
        # "sources": the main language of the sources, firmly, in its own script.
        ("sources", [(TRADITIONAL, 9)], ["zh-TW"], "zh-TW", "請用繁體中文（臺灣用語）撰寫回答。"),
        ("sources", [(TRADITIONAL, 9)], ["auto"], "zh-Hant", "請用繁體中文撰寫回答。"),
        ("sources", [(SIMPLIFIED, 9)], ["zh"], "zh-CN", "请用简体中文撰写回答。"),
        # Whisper says only "zh"; the text shows the script.
        ("sources", [(TRADITIONAL, 9)], ["zh"], "zh-Hant", "請用繁體中文撰寫回答。"),
        (
            "sources",
            [(ENGLISH, 9), (TRADITIONAL, 1)],
            ["en", "zh-TW"],
            "zh-TW",  # English answer; Chinese quotes follow the Chinese source
            "Write the answer in English, the main language of the sources.",
        ),
        (
            "sources",
            [(ENGLISH, 9)],
            ["auto"],
            None,
            "Write the answer in the main language of the sources.",
        ),
        # A set language.
        ("fr", [(TRADITIONAL, 9)], ["zh-TW"], "zh-TW", "Write the answer in French."),
        ("zh-TW", [(ENGLISH, 9)], ["en"], "zh-TW", "請用繁體中文（臺灣用語）撰寫回答。"),
        ("zh", [(TRADITIONAL, 9)], ["zh-TW"], "zh-CN", "请用简体中文撰写回答。"),
        ("ja", [(TRADITIONAL, 9)], ["zh-TW"], "zh-TW", "Write the answer in Japanese."),
    ],
)
def test_skill_language_modes(
    language: str,
    sources: list[tuple[str, int]],
    languages: list[str],
    target: str | None,
    directive: str,
) -> None:
    docs = [_doc(text, tokens) for text, tokens in sources]
    sample = "\n".join(d.text_markdown[:1500] for d in docs)
    script = skill_script(_use(language), docs, languages, sample)
    assert (script.target, script.directive) == (target, directive)


def test_instructions_mode_behaves_like_a_normal_message() -> None:
    docs = [_doc(ENGLISH, 9)]
    sample = docs[0].text_markdown
    chinese = skill_script(_use("instructions", "請整理重點。"), docs, ["en"], sample)
    assert chinese.target == "zh-Hant" and chinese.directive == "（若以中文回答，請使用繁體字。）"
    english = skill_script(_use("instructions"), docs, ["en"], sample)
    assert english.target is None and english.directive == ""
