"""Turn timestamped segments into compact markdown with citeable time anchors."""

from __future__ import annotations

from raida.transcribe.base import Segment


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def coalesce_segments(segments: list[Segment], paragraph_seconds: int) -> list[Segment]:
    """Merge consecutive segments into paragraphs of roughly ``paragraph_seconds``."""
    paragraphs: list[Segment] = []
    current: Segment | None = None
    for seg in segments:
        text = " ".join(seg.text.split())
        if not text:
            continue
        if current is None:
            current = Segment(start=seg.start, end=seg.end, text=text)
            continue
        if seg.end - current.start <= paragraph_seconds:
            current = Segment(start=current.start, end=seg.end, text=f"{current.text} {text}")
        else:
            paragraphs.append(current)
            current = Segment(start=seg.start, end=seg.end, text=text)
    if current is not None:
        paragraphs.append(current)
    return paragraphs


def transcript_markdown(segments: list[Segment], paragraph_seconds: int) -> str:
    lines = [
        f"[{format_timestamp(p.start)}] {p.text}"
        for p in coalesce_segments(segments, paragraph_seconds)
    ]
    return "\n\n".join(lines) + ("\n" if lines else "")
