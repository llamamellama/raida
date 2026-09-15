"""OpenAI Whisper via mlx-whisper (Apple Silicon, Metal). Fallback for the long tail of languages
and the language detector for routing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from raida.transcribe.base import (
    ProgressCallback,
    Segment,
    Transcriber,
    TranscriberError,
    Transcript,
)
from raida.transcribe.languages import base_language

log = logging.getLogger(__name__)

NO_SPEECH_THRESHOLD = 0.6


class MlxWhisperTranscriber(Transcriber):
    name = "whisper"

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def _module(self) -> Any:
        try:
            import mlx_whisper
        except ImportError as exc:
            raise TranscriberError(
                "mlx-whisper is not installed. Run `uv sync --extra mac` on an Apple Silicon Mac."
            ) from exc
        return mlx_whisper

    def supports_language(self, language: str | None) -> bool:
        return True

    def detect_language(self, wav_path: Path, seconds: int) -> str | None:
        result = self._module().transcribe(
            str(wav_path),
            path_or_hf_repo=self.model_name,
            clip_timestamps=[0.0, float(seconds)],
            condition_on_previous_text=False,
            verbose=None,
        )
        return result.get("language")

    def transcribe(
        self,
        wav_path: Path,
        *,
        language: str | None,
        progress: ProgressCallback | None = None,
    ) -> Transcript:
        result = self._module().transcribe(
            str(wav_path),
            path_or_hf_repo=self.model_name,
            language=base_language(language),
            condition_on_previous_text=False,
            word_timestamps=False,
            verbose=None,
        )
        segments: list[Segment] = []
        previous = ""
        for seg in result.get("segments", []):
            text = " ".join(str(seg.get("text", "")).split())
            if not text:
                continue
            if float(seg.get("no_speech_prob", 0.0)) > NO_SPEECH_THRESHOLD:
                continue
            if text == previous:  # repetition loop guard
                continue
            previous = text
            segments.append(Segment(start=float(seg["start"]), end=float(seg["end"]), text=text))
        if progress:
            progress(1.0)
        return Transcript(
            language=result.get("language") or base_language(language),
            segments=segments,
            backend=self.name,
            model=self.model_name,
        )
