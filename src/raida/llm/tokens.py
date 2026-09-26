"""Offline token estimation. Local servers report exact counts after the fact; planning uses
a character heuristic with a safety margin.

The heuristic is script-aware. `chars_per_token` (default 3.7) describes English and other
Latin-script text. Chinese, Japanese and Korean carry close to one token per character in the
tokenizers of current open models (measured on this project's Mac with Qwen3 on a Mandarin
transcript: 1.37 characters per token overall, 0.83 tokens per CJK character). Treating such
text at 3.7 characters per token under-counts it almost threefold, which let a prompt overflow
the context window and made the server silently drop the sources.
"""

from __future__ import annotations

import math
import unicodedata

SAFETY_MARGIN = 1.15

# Tokens per character for scripts that do not use spaces between words or pack much more
# meaning per character than Latin letters do.
CJK_TOKENS_PER_CHAR = 0.8  # Han ideographs, kana, fullwidth forms and CJK punctuation
SYLLABIC_TOKENS_PER_CHAR = 1.0  # Hangul, Thai, Lao, Khmer, Myanmar
# Non-Latin alphabetic scripts (Cyrillic, Greek, Arabic, Hebrew, Devanagari and the like) are
# tokenized less efficiently than English: roughly half the characters per token.
OTHER_SCRIPT_CPT_RATIO = 0.5


def _cjk(cp: int) -> bool:
    return (
        0x4E00 <= cp <= 0x9FFF  # CJK unified ideographs
        or 0x3400 <= cp <= 0x4DBF  # extension A
        or 0x20000 <= cp <= 0x2FFFF  # extensions B and beyond
        or 0x3040 <= cp <= 0x30FF  # hiragana, katakana
        or 0x31F0 <= cp <= 0x31FF  # katakana phonetic extensions
        or 0x3000 <= cp <= 0x303F  # CJK symbols and punctuation
        or 0xFF00 <= cp <= 0xFFEF  # halfwidth and fullwidth forms
        or 0xF900 <= cp <= 0xFAFF  # compatibility ideographs
    )


def _syllabic(cp: int) -> bool:
    return (
        0xAC00 <= cp <= 0xD7AF  # Hangul syllables
        or 0x1100 <= cp <= 0x11FF  # Hangul jamo
        or 0x0E00 <= cp <= 0x0E7F  # Thai
        or 0x0E80 <= cp <= 0x0EFF  # Lao
        or 0x1780 <= cp <= 0x17FF  # Khmer
        or 0x1000 <= cp <= 0x109F  # Myanmar
    )


def estimate_tokens(text: str, chars_per_token: float) -> int:
    if not text:
        return 0
    latin = 0
    other_alpha = 0
    tokens = 0.0
    for ch in text:
        cp = ord(ch)
        if cp < 0x0250:  # ASCII, Latin-1 and Latin Extended: the calibrated case
            latin += 1
        elif _cjk(cp):
            tokens += CJK_TOKENS_PER_CHAR
        elif _syllabic(cp):
            tokens += SYLLABIC_TOKENS_PER_CHAR
        elif unicodedata.category(ch).startswith("L"):
            other_alpha += 1
        else:
            latin += 1  # punctuation, symbols, emoji: treat like Latin text
    tokens += latin / chars_per_token
    tokens += other_alpha / (chars_per_token * OTHER_SCRIPT_CPT_RATIO)
    return math.ceil(tokens * SAFETY_MARGIN)
