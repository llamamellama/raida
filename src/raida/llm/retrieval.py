"""Lexical retrieval of verbatim passages for a question (BM25).

Answers are written from each long source's notes; the passages found here add the exact
wording for what the question names. Chinese, Japanese and Korean have no spaces between
words, so they are indexed as overlapping character pairs (bigrams), the standard baseline for
CJK retrieval; other scripts are indexed as words. Chinese is folded to Simplified first, so a
question in one script finds passages in the other. Nothing is downloaded or stored: a session
holds a few hundred paragraphs, scored in milliseconds.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from raida.llm.tokens import estimate_tokens
from raida.script import fold_han

_CJK_CLASS = "぀-ヿ㐀-䶿一-鿿가-힯豈-﫿"
_RUNS = re.compile(rf"[{_CJK_CLASS}]+|[^\W\d_{_CJK_CLASS}]+|\d+")
_ANCHOR = re.compile(r"\[(?:\d{2}:\d{2}:\d{2}|p\. \d+)\]")
_CJK_START = re.compile(rf"[{_CJK_CLASS}]")

K1 = 1.2
B = 0.75


@dataclass(frozen=True)
class Passage:
    source_index: int  # 1-based, the id of the <source> block the passage belongs to
    source_title: str
    position: int  # paragraph index within the source
    anchor: str  # the latest [hh:mm:ss] or [p. N] anchor at or before the paragraph
    text: str
    tokens: int


def terms(text: str) -> list[str]:
    out: list[str] = []
    for match in _RUNS.finditer(fold_han(_ANCHOR.sub(" ", text)).lower()):
        run = match.group()
        if _CJK_START.match(run):
            out.extend([run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)])
        elif len(run) > 1:
            out.append(run)
    return out


def split_passages(
    text: str, source_index: int, source_title: str, chars_per_token: float
) -> list[Passage]:
    passages: list[Passage] = []
    anchor = ""
    for position, paragraph in enumerate(p.strip() for p in re.split(r"\n\s*\n", text)):
        if not paragraph:
            continue
        found = _ANCHOR.findall(paragraph)
        first = _ANCHOR.match(paragraph)
        if first:
            anchor = first.group()
        passages.append(
            Passage(
                source_index=source_index,
                source_title=source_title,
                position=position,
                anchor=anchor,
                text=paragraph,
                tokens=estimate_tokens(paragraph, chars_per_token),
            )
        )
        if found:
            anchor = found[-1]
    return passages


def bm25(query: str, passages: list[Passage]) -> list[float]:
    docs = [terms(p.text) for p in passages]
    if not docs:
        return []
    count = len(docs)
    avg_len = sum(len(d) for d in docs) / count or 1.0
    df = Counter(t for d in docs for t in set(d))
    wanted = set(terms(query))
    scores: list[float] = []
    for doc in docs:
        tf = Counter(doc)
        norm = K1 * (1 - B + B * len(doc) / avg_len)
        score = 0.0
        for term in wanted:
            freq = tf.get(term)
            if freq:
                idf = math.log((count - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
                score += idf * freq * (K1 + 1) / (freq + norm)
        scores.append(score)
    return scores


def select_passages(
    query: str, passages: list[Passage], budget_tokens: int, min_ratio: float = 0.5
) -> list[Passage]:
    """Best-scoring passages that fit the budget, in source order. Passages scoring under
    ``min_ratio`` of the best are left out: they match only incidental words of the question."""
    if budget_tokens <= 0 or not passages:
        return []
    scores = bm25(query, passages)
    best = max(scores, default=0.0)
    if best <= 0:
        return []
    ranked = sorted(
        (i for i, s in enumerate(scores) if s >= best * min_ratio), key=lambda i: -scores[i]
    )
    chosen: list[Passage] = []
    used = 0
    for index in ranked:
        passage = passages[index]
        if used + passage.tokens > budget_tokens:
            continue
        chosen.append(passage)
        used += passage.tokens
    return sorted(chosen, key=lambda p: (p.source_index, p.position))


def excerpts_block(passages: list[Passage]) -> str:
    """Render passages as <excerpt> blocks, merging neighbours from the same source."""
    groups: list[list[Passage]] = []
    for passage in passages:
        last = groups[-1][-1] if groups else None
        if (
            last is not None
            and last.source_index == passage.source_index
            and passage.position == last.position + 1
        ):
            groups[-1].append(passage)
        else:
            groups.append([passage])
    blocks = []
    for group in groups:
        head = group[0]
        body = "\n\n".join(p.text for p in group)
        if head.anchor and not body.startswith(head.anchor):
            body = f"{head.anchor} {body}"
        blocks.append(f'<excerpt source="{head.source_index}">\n{body}\n</excerpt>')
    return "\n\n".join(blocks)
