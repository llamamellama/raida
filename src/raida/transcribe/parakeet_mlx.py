"""NVIDIA Parakeet TDT via parakeet-mlx (Apple Silicon, Metal)."""

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
from raida.transcribe.languages import PARAKEET_V3_LANGUAGES, base_language

log = logging.getLogger(__name__)


class ParakeetMlxTranscriber(Transcriber):
    name = "parakeet"

    def __init__(
        self, model_name: str, chunk_seconds: float = 120.0, overlap_seconds: float = 15.0
    ):
        self.model_name = model_name
        self.chunk_seconds = chunk_seconds
        self.overlap_seconds = overlap_seconds
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from parakeet_mlx import from_pretrained
            except ImportError as exc:
                raise TranscriberError(
                    "parakeet-mlx is not installed. Run `uv sync --extra mac` on an "
                    "Apple Silicon Mac."
                ) from exc
            log.info("loading_model", extra={"backend": self.name, "model": self.model_name})
            self._model = from_pretrained(self.model_name)
        return self._model

    def supports_language(self, language: str | None) -> bool:
        base = base_language(language)
        return base is None or base in PARAKEET_V3_LANGUAGES

    def detect_language(self, wav_path: Path, seconds: int) -> str | None:
        return None  # Parakeet identifies the language internally but does not report it.

    def transcribe(
        self,
        wav_path: Path,
        *,
        language: str | None,
        progress: ProgressCallback | None = None,
    ) -> Transcript:
        model = self._load()

        def _callback(*args: Any) -> None:
            if progress and len(args) >= 2 and args[1]:
                progress(min(1.0, float(args[0]) / float(args[1])))

        result = model.transcribe(
            str(wav_path),
            chunk_duration=self.chunk_seconds,
            overlap_duration=self.overlap_seconds,
            chunk_callback=_callback,
        )
        sentences = getattr(result, "sentences", None) or []
        segments = [
            Segment(start=float(s.start), end=float(s.end), text=str(s.text))
            for s in sentences
            if str(s.text).strip()
        ]
        if not segments and getattr(result, "text", ""):
            segments = [Segment(start=0.0, end=0.0, text=str(result.text))]
        return Transcript(
            language=base_language(language),
            segments=segments,
            backend=self.name,
            model=self.model_name,
        )
