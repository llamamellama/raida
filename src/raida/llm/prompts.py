"""Prompt templates for multi-source synthesis and map-reduce condensation."""

from __future__ import annotations

import html

from raida.models import ProcessedSource

SYSTEM_PROMPT = """You are raida, a writing assistant that works only from the sources the user \
provides. Rules:
- Base every statement on the sources. Do not invent facts, names, numbers or quotes. If the \
sources do not contain what the instruction asks for, say so plainly.
- When you draw on a specific passage, cite it with the anchor that appears in the source text, \
for example [p. 12] for a document page or [00:14:32] for a time code, together with the source \
title when more than one source is present.
- Write in the same language as the user's instruction unless told otherwise.
- Answer in well-structured Markdown: a title when appropriate, headings for long answers, \
lists only where they help. No preamble about being an AI.
"""

CONDENSE_SYSTEM = """You condense excerpts of source material for a later writing step. Keep \
every fact, figure, name, argument, quote and example that could matter for the instruction. \
Keep the anchors exactly as they appear ([p. N] or [hh:mm:ss]) next to the material they belong \
to. Do not add commentary, do not answer the instruction yourself, do not summarize beyond the \
requested length. Output plain Markdown."""

MERGE_SYSTEM = """You merge several condensed notes taken from the same source into one shorter \
set of notes. Remove duplication, keep every distinct fact and its anchor, keep chronological or \
document order. Do not answer the instruction; only merge. Output plain Markdown."""


def source_block(index: int, source: ProcessedSource, condensed: bool = False) -> str:
    meta = source.meta
    attrs = [
        f'id="{index}"',
        f'title="{html.escape(source.title, quote=True)}"',
        f'kind="{source.kind}"',
    ]
    if meta.get("pages"):
        attrs.append(f'pages="{meta["pages"]}"')
    if meta.get("duration_s"):
        attrs.append(f'duration="{_hms(float(meta["duration_s"]))}"')
    if meta.get("detected_language"):
        attrs.append(f'language="{meta["detected_language"]}"')
    if condensed:
        attrs.append('condensed="true"')
    return f"<source {' '.join(attrs)}>\n{source.text_markdown.strip()}\n</source>"


def sources_message(sources: list[ProcessedSource], condensed: bool = False) -> str:
    blocks = [source_block(i, s, condensed) for i, s in enumerate(sources, start=1)]
    intro = "Here are the sources. Each is wrapped in a <source> tag with its title and kind." + (
        " Long sources were condensed while preserving anchors." if condensed else ""
    )
    return intro + "\n\n" + "\n\n".join(blocks)


def condense_user_message(
    instruction: str, source: ProcessedSource, chunk: str, target_words: int, part: int, total: int
) -> str:
    return (
        f"The final instruction the notes must serve is:\n\n{instruction.strip()}\n\n"
        f"Source: {source.title} ({source.kind}), excerpt {part} of {total}.\n"
        f"Condense the excerpt below to at most about {target_words} words, preserving anchors.\n\n"
        f"<excerpt>\n{chunk.strip()}\n</excerpt>"
    )


def merge_user_message(
    instruction: str, source_title: str, notes: list[str], target_words: int
) -> str:
    joined = "\n\n---\n\n".join(n.strip() for n in notes)
    return (
        f"The final instruction the notes must serve is:\n\n{instruction.strip()}\n\n"
        f"Source: {source_title}. Merge the following {len(notes)} notes into at most about "
        f"{target_words} words, preserving anchors.\n\n{joined}"
    )


TITLE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string", "description": "Short title, at most 8 words"}},
    "required": ["title"],
}


def _hms(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
