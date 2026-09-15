"""Deterministic transcriber for tests and for running the app without MLX."""

from __future__ import annotations

from pathlib import Path

from raida.pipeline.stages.media import wav_duration_seconds
from raida.transcribe.base import ProgressCallback, Segment, Transcript


class FakeTranscriber:
    name = "fake"

    def __init__(self, segment_seconds: float = 5.0) -> None:
        self.segment_seconds = segment_seconds

    def supports_language(self, language: str | None) -> bool:
        return True

    def detect_language(self, wav_path: Path, seconds: int) -> str | None:
        return "en"

    def transcribe(
        self,
        wav_path: Path,
        *,
        language: str | None,
        progress: ProgressCallback | None = None,
    ) -> Transcript:
        duration = wav_duration_seconds(wav_path)
        segments: list[Segment] = []
        start = 0.0
        index = 1
        while start < duration:
            end = min(start + self.segment_seconds, duration)
            segments.append(Segment(start=start, end=end, text=f"Fake transcript segment {index}."))
            if progress:
                progress(end / duration if duration else 1.0)
            start = end
            index += 1
        return Transcript(
            language=language or "en", segments=segments, backend=self.name, model="fake"
        )
