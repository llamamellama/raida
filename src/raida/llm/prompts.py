"""Prompt templates for multi-source synthesis and map-reduce condensation."""

from __future__ import annotations

import html
import re
from typing import Literal

from raida.models import ProcessedSource

SourceForm = Literal["full", "notes", "overview"]

SYSTEM_PROMPT = """You are raida, a writing assistant that works only from the sources the user \
provides. Rules:
- Base every statement on the sources. Do not invent facts, names, numbers or quotes. If the \
sources do not contain what the instruction asks for, say so plainly.
- Long sources are given as detailed notes taken from their full text (form="notes"), or as a \
short overview (form="overview"); verbatim excerpts found for the instruction may follow it. \
Treat notes and excerpts as the source's content.
- When you draw on a specific passage, cite it with the anchor that appears in the source text, \
notes or excerpt, for example [p. 12] for a document page or [00:14:32] for a time code, \
together with the source title when more than one source is present.
- Write in the same language as the user's instruction unless told otherwise, even when the \
sources are in another language. For Chinese, match the instruction's script (Traditional or \
Simplified).
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


def source_block(
    index: int,
    source: ProcessedSource,
    condensed: bool = False,
    form: SourceForm = "full",
    body: str | None = None,
) -> str:
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
    if form != "full":
        attrs.append(f'form="{form}"')
    text = source.text_markdown if body is None else body
    return f"<source {' '.join(attrs)}>\n{text.strip()}\n</source>"


def sources_message(sources: list[ProcessedSource], condensed: bool = False) -> str:
    blocks = [source_block(i, s, condensed) for i, s in enumerate(sources, start=1)]
    intro = "Here are the sources. Each is wrapped in a <source> tag with its title and kind." + (
        " Long sources were condensed while preserving anchors." if condensed else ""
    )
    return intro + "\n\n" + "\n\n".join(blocks)


def blocks_message(blocks: list[str]) -> str:
    return (
        "Here are the sources. Each is wrapped in a <source> tag with its id, title and kind."
        "\n\n" + "\n\n".join(blocks)
    )


def instruction_message(instruction: str, excerpts: str) -> str:
    """The final user turn: verbatim excerpts found for this instruction, then the instruction.
    Excerpts sit here rather than in the sources so the sources stay a stable, cached prefix."""
    if not excerpts:
        return instruction.strip()
    return (
        "Verbatim excerpts from the full text of the sources, found for this instruction "
        "(source ids as above):\n\n"
        f"{excerpts}\n\nInstruction:\n{instruction.strip()}"
    )


NOTES_SYSTEM = """You take study notes on one excerpt of a longer source, a recording \
transcript or a document, so that a later step can answer any question about the source from \
your notes alone. Rules:
- Write in the same language and script as the excerpt.
- Keep every distinct point, teaching, argument, example, story, instruction, practice or \
exercise, question and answer, name, number and date, in the order they occur.
- Start each note with the anchor ([hh:mm:ss] or [p. N]) of the passage it comes from.
- Quote short, memorable sentences verbatim in quotation marks.
- Group the notes under short headings (Markdown ### headings) with bullet points below them.
- A transcript comes from speech recognition: it has little punctuation and some misheard \
words. Write what the speaker evidently meant; keep a name as heard when unsure.
- No introduction, no commentary and no conclusions of your own."""

OVERVIEW_SYSTEM = """You write the overview that opens a set of study notes on one source. \
Say what the source is (for example a talk, a class, a meeting or a report; name the speakers \
or authors if the notes do), then its main themes in order and its key takeaways. Write in the \
same language and script as the notes. Plain prose, no headings, no anchors."""


def length_hint(text: str, ratio: float) -> str:
    """ "about N characters" for Chinese, Japanese and Korean, "about N words" otherwise."""
    cjk = sum(1 for ch in text if "\u3040" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af")
    if cjk > len(text) * 0.3:
        return f"about {max(80, int(cjk * ratio))} characters"
    return f"about {max(60, int(len(text.split()) * ratio))} words"


def notes_user_message(
    source: ProcessedSource, excerpt: str, part: int, total: int, length: str
) -> str:
    return (
        f"Source: {source.title} ({source.kind}), excerpt {part} of {total}. "
        f"Write the notes in {length}.\n\n<excerpt>\n{excerpt.strip()}\n</excerpt>"
    )


def overview_user_message(source: ProcessedSource, notes: str, length: str) -> str:
    return (
        f"Source: {source.title} ({source.kind}). Write the overview in {length}.\n\n"
        f"<notes>\n{notes.strip()}\n</notes>"
    )


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


TITLE_SYSTEM = """Name the session below. Return a title of at most 8 words (at most 12 \
characters for Chinese, Japanese or Korean), in the same language and script as the \
instruction. Name the subject matter, not the task: "Book club 248 and 249 notes", not \
"Summary request". Reply with the title only: no quotes, no label, no trailing punctuation."""

_TITLE_LABEL = re.compile(r"^(?:title|標題|标题)\s*[:：]\s*", re.IGNORECASE)


def clean_title(text: str) -> str | None:
    """First non-empty line of a model reply, without a label, quotes or Markdown marks."""
    line = next((ln.strip() for ln in text.strip().splitlines() if ln.strip()), "")
    line = _TITLE_LABEL.sub("", line.strip("#*` ").strip())
    line = line.strip("\"'`“”「」『』 ").rstrip("。.!！")
    return line[:120] or None


def _hms(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
