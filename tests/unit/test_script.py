"""Chinese script handling: transcript normalization, answer targets, streamed conversion."""

from __future__ import annotations

import pytest

from raida.script import (
    ScriptStream,
    answer_script,
    convert_to,
    han_script,
    normalize_script,
    profiles_for,
)

TRADITIONAL_QUESTION = "幫我整理重要內容。如何將這些內容應用在每天生活的靈性學習成長的過程中？"


def test_naive_traditional_is_repaired_for_taiwan() -> None:
    # What Apple's zh-TW recognizer wrote for 外面, 秋天 and 只有 on a real recording.
    assert normalize_script("出去外麵走一走，鞦天鞦高氣爽，我隻有一個問題", "zh-TW") == (
        "出去外面走一走，秋天秋高氣爽，我只有一個問題"
    )


def test_simplified_becomes_traditional_and_correct_text_is_kept() -> None:
    assert normalize_script("我们的力量来自于内在", "zh-TW") == "我們的力量來自於內在"
    assert normalize_script("一隻貓在麵包店外面", "zh-TW") == "一隻貓在麵包店外面"
    assert normalize_script("頭髮", "zh-HK") == "頭髮"
    assert normalize_script("我們的力量", "zh-CN") == "我们的力量"


def test_other_languages_and_bare_zh_are_untouched() -> None:
    assert profiles_for("zh") == () and profiles_for(None) == () and profiles_for("en-US") == ()
    assert normalize_script("外麵", "zh") == "外麵"
    assert normalize_script("Hello", "en") == "Hello"
    assert profiles_for("zh_tw") == ("t2s", "s2twp")


def test_han_script_tells_the_scripts_apart() -> None:
    assert han_script(TRADITIONAL_QUESTION) == "hant"
    assert han_script("帮我整理重要内容，如何应用在每天的生活中") == "hans"
    assert han_script("Summarize the talks") is None
    assert han_script("これは学校です") is None  # Japanese: kana present
    assert han_script("人口") is None  # shared characters only


@pytest.mark.parametrize(
    ("instruction", "languages", "sample", "target", "directive"),
    [
        (TRADITIONAL_QUESTION, ["zh-TW"], "", "zh-TW", "臺灣用語"),
        (TRADITIONAL_QUESTION, ["auto"], "", "zh-Hant", "繁體字"),
        (TRADITIONAL_QUESTION, ["zh-HK"], "", "zh-HK", "香港用語"),
        ("帮我整理重要内容", ["zh-TW"], "", "zh-CN", "简体字"),
        # Another language: Chinese in the answer (quotes) follows the sources, no directive.
        ("Summarize these talks", ["zh-TW"], "外面的世界很大，我們都來學習", "zh-TW", ""),
        ("Summarize these talks", ["en"], "The world is big", None, ""),
        ("Resume ces conferences", ["fr"], "Le monde est grand", None, ""),
        ("Translate the quotes into Simplified Chinese", ["zh-TW"], "我們", "zh-CN", ""),
        ("把这段简体翻成繁體", [], "", "zh-Hant", "繁體字"),
        ("請把這段翻譯成日文", ["zh-TW"], "", None, ""),
        ("これを要約してください", [], "我們的內容", None, ""),
        ("요약해 주세요", [], "我們的內容", None, ""),
    ],
)
def test_answer_script(
    instruction: str, languages: list[str], sample: str, target: str | None, directive: str
) -> None:
    script = answer_script(instruction, languages, sample)
    assert script.target == target
    assert (directive in script.directive) if directive else script.directive == ""


def test_directive_constrains_the_script_not_the_language() -> None:
    # A Chinese instruction may ask for an English answer; the directive must allow it.
    assert answer_script("請用英文寫一段摘要", [], "").directive.startswith("（若以中文回答")


def test_stream_converts_at_phrase_breaks() -> None:
    stream = ScriptStream("zh-TW")
    deltas = ["# 灵性成长", "实践指南\n\n", "我们的头", "发很长，", "软件与网络。", "Hello world"]
    out = "".join(stream.feed(d) for d in deltas) + stream.flush()
    assert out == "# 靈性成長實踐指南\n\n我們的頭髮很長，軟體與網路。Hello world"


def test_stream_releases_long_text_without_breaks_and_passes_through_without_target() -> None:
    stream = ScriptStream("zh-Hant")
    released = stream.feed("我们" * 40)
    assert released and "们" not in released
    assert ScriptStream(None).feed("我们") == "我们"


def test_convert_leaves_correct_text_and_other_languages_alone() -> None:
    assert convert_to("一隻貓在外面，頭髮很長。", "zh-TW") == "一隻貓在外面，頭髮很長。"
    assert convert_to("学校に行きます", "zh-TW") == "学校に行きます"
    assert convert_to("The model said 我们.", "zh-Hant") == "The model said 我們."
    assert convert_to("我們的內容", "zh-CN") == "我们的内容"
