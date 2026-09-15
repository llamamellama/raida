"""SRT and WebVTT subtitle files become timestamped transcripts."""

from __future__ import annotations

from pathlib import Path

from raida.transcribe.base import Segment


def _to_seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def read_subtitles(path: Path) -> list[Segment]:
    import webvtt

    if path.suffix.lower() == ".srt":
        captions = webvtt.from_srt(str(path))
    else:
        captions = webvtt.read(str(path))
    segments = []
    for cap in captions:
        text = " ".join(cap.text.split())
        if text:
            segments.append(
                Segment(start=_to_seconds(cap.start), end=_to_seconds(cap.end), text=text)
            )
    return segments
