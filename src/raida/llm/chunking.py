"""Structure-aware splitting of markdown into token-bounded chunks."""

from __future__ import annotations

import re

from raida.llm.tokens import estimate_tokens

_HEADING = re.compile(r"^#{1,6}\s")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts


def _split_long_paragraph(paragraph: str, max_tokens: int, cpt: float) -> list[str]:
    if estimate_tokens(paragraph, cpt) <= max_tokens:
        return [paragraph]
    sentences = _SENTENCE_END.split(paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and estimate_tokens(candidate, cpt) > max_tokens:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    # A single sentence longer than the budget is cut on character boundaries.
    out: list[str] = []
    max_chars = int(max_tokens * cpt / 1.15)
    for piece in pieces:
        while len(piece) > max_chars:
            out.append(piece[:max_chars])
            piece = piece[max_chars:]
        out.append(piece)
    return out


def split_markdown(
    text: str, max_tokens: int, chars_per_token: float, overlap_ratio: float = 0.1
) -> list[str]:
    """Greedy packing of paragraphs into chunks; headings start a new chunk when the current one
    is more than half full; a short trailing paragraph is repeated at the start of the next chunk
    for continuity."""
    units: list[str] = []
    for paragraph in _paragraphs(text):
        units.extend(_split_long_paragraph(paragraph, max_tokens, chars_per_token))
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for unit in units:
        unit_tokens = estimate_tokens(unit, chars_per_token)
        heading_break = _HEADING.match(unit) and current_tokens > max_tokens / 2
        if current and (current_tokens + unit_tokens > max_tokens or heading_break):
            chunks.append("\n\n".join(current))
            tail = current[-1]
            if estimate_tokens(tail, chars_per_token) <= max_tokens * overlap_ratio:
                current = [tail]
                current_tokens = estimate_tokens(tail, chars_per_token)
            else:
                current, current_tokens = [], 0
        current.append(unit)
        current_tokens += unit_tokens
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text]
