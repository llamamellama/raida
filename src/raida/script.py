"""Chinese script handling (OpenCC): Traditional and Simplified Chinese share one language but
not one set of characters, and models mix them.

Answers follow the language of the instruction, whatever it is; the system prompt asks for that
and models follow it. Chinese is the exception measured on this project: Qwen3 answered a
Traditional Chinese instruction over Traditional Chinese sources in Simplified characters in
two runs out of four. So for Chinese the script is also enforced after the model:

- Machine transcripts for a regional language code are normalized when they are made. Apple's
  zh-TW recognizer writes what its zh-CN recognizer writes, converted character by character,
  which picks the wrong character wherever one Simplified character stands for several
  Traditional ones (外面 came out as 外麵, 秋天 as 鞦天). Going through Simplified first repairs
  that and leaves correctly written Traditional text as it was.
- Notes and answers are converted to a target script with phrase-aware OpenCC profiles. For
  an answer, the target is the script the instruction asks for or is written in; for an
  instruction in another language, the script of the sources, so quoted Chinese matches them.
  Text containing kana or hangul (Japanese, Korean) is never converted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

HanScript = Literal["hant", "hans"]

# Transcript normalization: language code (lowercase) -> OpenCC profiles applied in order.
PROFILES: dict[str, tuple[str, ...]] = {
    "zh-tw": ("t2s", "s2twp"),  # Traditional, Taiwan standard with Taiwan vocabulary
    "zh-hant": ("t2s", "s2tw"),
    "zh-hk": ("t2s", "s2hk"),  # Traditional, Hong Kong standard
    "zh-cn": ("t2s",),
    "zh-hans": ("t2s",),
    "zh-sg": ("t2s",),
}

# Conversion of model-written text into a target. Only Simplified characters change for a
# Traditional target (and the reverse), so text the model already wrote correctly stays.
TARGET_PROFILES: dict[str, str] = {
    "zh-TW": "s2twp",
    "zh-HK": "s2hk",
    "zh-Hant": "s2tw",  # Traditional of unknown region: Taiwan character forms, no vocabulary
    "zh-CN": "t2s",
}

# Added after an instruction written in Chinese; models follow a request in the instruction's
# own words more reliably than the rule in the system prompt. It constrains only the script,
# so an instruction that asks for an answer in English still gets one.
DIRECTIVES: dict[str, str] = {
    "zh-TW": "（若以中文回答，請使用繁體字與臺灣用語。）",
    "zh-HK": "（若以中文回答，請使用繁體字與香港用語。）",
    "zh-Hant": "（若以中文回答，請使用繁體字。）",
    "zh-CN": "（若以中文回答，请使用简体字。）",
}

# Said when the answer's language is set rather than taken from the instruction (a skill's
# language option): an unconditional request, in the language asked for.
FIRM_DIRECTIVES: dict[str, str] = {
    "zh-TW": "請用繁體中文（臺灣用語）撰寫回答。",
    "zh-HK": "請用繁體中文（香港用語）撰寫回答。",
    "zh-Hant": "請用繁體中文撰寫回答。",
    "zh-CN": "请用简体中文撰写回答。",
}

# Language codes whose written form is one Chinese script.
_CODE_TARGETS: dict[str, str] = {
    "zh-tw": "zh-TW",
    "zh-hant": "zh-Hant",
    "zh-hk": "zh-HK",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-sg": "zh-CN",
}

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002ffff]")
_KANA_HANGUL = re.compile(r"[぀-ヿㇰ-ㇿ가-힯ᄀ-ᇿ]")
_ASK_TRADITIONAL = re.compile(r"繁體|繁体|正體|traditional\s+chinese", re.IGNORECASE)
_ASK_SIMPLIFIED = re.compile(r"簡體|简体|simplified\s+chinese", re.IGNORECASE)
# Kanji and hanja in a Japanese or Korean answer must not be converted.
_ASK_JAPANESE_KOREAN = re.compile(
    r"日文|日語|日语|日本語|japanese|韓文|韩文|韓語|韩语|korean", re.IGNORECASE
)
SAMPLE_CHARS = 4000


def profiles_for(language: str | None) -> tuple[str, ...]:
    """OpenCC profiles for a language code; empty for bare "zh" and non-Chinese codes, where
    the script is whatever the source used."""
    if not language:
        return ()
    return PROFILES.get(language.replace("_", "-").lower(), ())


@lru_cache(maxsize=8)
def _converter(profile: str) -> Any:
    import opencc

    return opencc.OpenCC(profile)


def normalize_script(text: str, language: str | None) -> str:
    """Normalize a machine transcript (or notes on it) to the script of its language code."""
    for profile in profiles_for(language):
        text = _converter(profile).convert(text)
    return text


def han_script(text: str) -> HanScript | None:
    """The Chinese script a text is written in, judged by the characters that exist in only one
    of the two; None without Chinese, with kana or hangul, or when it cannot be told."""
    if _KANA_HANGUL.search(text):
        return None
    han = "".join(_HAN.findall(text))[:SAMPLE_CHARS]
    if not han:
        return None
    # A Traditional-only character changes when converted to Simplified, and the reverse.
    traditional = sum(a != b for a, b in zip(han, _converter("t2s").convert(han), strict=False))
    simplified = sum(a != b for a, b in zip(han, _converter("s2t").convert(han), strict=False))
    if traditional > simplified:
        return "hant"
    if simplified > traditional:
        return "hans"
    return None


def regional_target(script: HanScript, languages: list[str]) -> str:
    """Target code for a script, using the region of the sources' languages when one is set."""
    if script == "hans":
        return "zh-CN"
    codes = {code.replace("_", "-").lower() for code in languages}
    if "zh-hk" in codes and "zh-tw" not in codes:
        return "zh-HK"
    if "zh-tw" in codes:
        return "zh-TW"
    return "zh-Hant"


@dataclass(frozen=True)
class AnswerScript:
    target: str | None  # a key of TARGET_PROFILES, or None to leave the answer as written
    directive: str = ""  # appended to a Chinese instruction


def answer_script(instruction: str, languages: list[str], sources_sample: str) -> AnswerScript:
    """The Chinese script an answer must use: the one the instruction asks for, else the one it
    is written in, else, for an instruction in another language, the sources' script (which
    only affects Chinese quoted in the answer). Japanese and Korean instructions: none."""
    if _KANA_HANGUL.search(instruction) or _ASK_JAPANESE_KOREAN.search(instruction):
        return AnswerScript(None)
    asked = _asked_script(instruction)
    written = han_script(instruction)
    script = asked or written
    if script is not None:
        target = regional_target(script, languages)
        return AnswerScript(target, DIRECTIVES[target] if written is not None else "")
    if _HAN.search(instruction):
        return AnswerScript(None)  # Chinese of undecidable script: leave it to the model
    from_sources = han_script(sources_sample)
    if from_sources is None:
        return AnswerScript(None)
    return AnswerScript(regional_target(from_sources, languages))


def _asked_script(instruction: str) -> HanScript | None:
    """The script an instruction names; the later one when it names both ("translate this
    Simplified text into Traditional")."""
    last = {
        script: max((m.start() for m in pattern.finditer(instruction)), default=-1)
        for script, pattern in (("hans", _ASK_SIMPLIFIED), ("hant", _ASK_TRADITIONAL))
    }
    if max(last.values()) < 0:
        return None
    return "hans" if last["hans"] > last["hant"] else "hant"


def target_for_language(code: str | None) -> str | None:
    """Target script for a language code: "zh-TW" -> "zh-TW", "zh" -> "zh-CN"; None otherwise."""
    if not code:
        return None
    return _CODE_TARGETS.get(code.replace("_", "-").lower())


def fold_han(text: str) -> str:
    """Chinese folded to Simplified, for matching text across the two scripts."""
    if not _HAN.search(text) or _KANA_HANGUL.search(text):
        return text
    return _converter("t2s").convert(text)


def convert_to(text: str, target: str | None) -> str:
    """Convert model-written text to a target script, leaving Japanese and Korean alone."""
    if not target or not _HAN.search(text) or _KANA_HANGUL.search(text):
        return text
    return _converter(TARGET_PROFILES[target]).convert(text)


# Converting streamed text piece by piece could split a phrase ("頭" + "髮" read as "頭發"), so
# text is held back until a break where no phrase continues.
_BREAK = re.compile(r"[\s，。、；：？！「」『』（）《》〈〉【】…—,.;:?!()\[\]{}*#>|\"]")
MAX_PENDING = 48


class ScriptStream:
    """Converts a streamed answer to a target script, releasing text at phrase breaks."""

    def __init__(self, target: str | None) -> None:
        self.target = target
        self._pending = ""

    def feed(self, delta: str) -> str:
        if self.target is None:
            return delta
        self._pending += delta
        cut = max((m.end() for m in _BREAK.finditer(self._pending)), default=0)
        if cut == 0 and len(self._pending) > MAX_PENDING:
            cut = len(self._pending) - 4
        if cut == 0:
            return ""
        ready, self._pending = self._pending[:cut], self._pending[cut:]
        return convert_to(ready, self.target)

    def flush(self) -> str:
        ready, self._pending = self._pending, ""
        return convert_to(ready, self.target)
