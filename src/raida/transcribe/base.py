"""Transcriber protocol and transcript data types."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

ProgressCallback = Callable[[float], None]


class Segment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    language: str | None = None
    segments: list[Segment] = Field(default_factory=list)
    backend: str = ""
    model: str = ""

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def duration(self) -> float:
        return max((s.end for s in self.segments), default=0.0)


class Transcriber(Protocol):
    name: str

    def supports_language(self, language: str | None) -> bool: ...

    def transcribe(
        self,
        wav_path: Path,
        *,
        language: str | None,
        progress: ProgressCallback | None = None,
    ) -> Transcript: ...

    def detect_language(self, wav_path: Path, seconds: int) -> str | None: ...


class TranscriberError(RuntimeError):
    pass
