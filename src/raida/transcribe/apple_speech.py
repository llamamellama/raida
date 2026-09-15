"""Apple's on-device SpeechAnalyzer (macOS 26+) through the `yap` CLI. Optional backend.

Needs an explicit locale: language detection must run first (or the user sets the language).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from raida.transcribe.base import (
    ProgressCallback,
    Segment,
    Transcriber,
    TranscriberError,
    Transcript,
)
from raida.transcribe.languages import APPLE_LOCALES, base_language


def apple_speech_available() -> bool:
    if platform.system() != "Darwin" or shutil.which("yap") is None:
        return False
    try:
        major = int(platform.mac_ver()[0].split(".")[0] or 0)
    except ValueError:
        return False
    return major >= 26


class AppleSpeechTranscriber(Transcriber):
    name = "apple"

    def supports_language(self, language: str | None) -> bool:
        base = base_language(language)
        return base is not None and base in APPLE_LOCALES

    def detect_language(self, wav_path: Path, seconds: int) -> str | None:
        return None

    def transcribe(
        self,
        wav_path: Path,
        *,
        language: str | None,
        progress: ProgressCallback | None = None,
    ) -> Transcript:
        base = base_language(language)
        if base is None or base not in APPLE_LOCALES:
            raise TranscriberError("Apple speech backend needs an explicit, supported language.")
        if not apple_speech_available():
            raise TranscriberError("Apple speech backend needs macOS 26+ and `brew install yap`.")
        from raida.pipeline.stages.subtitles import read_subtitles

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.srt"
            cmd = [
                "yap",
                "transcribe",
                "--locale",
                APPLE_LOCALES[base],
                "--srt",
                "--output-file",
                str(out),
                str(wav_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=False)
            if proc.returncode != 0 or not out.exists():
                raise TranscriberError(f"yap failed: {proc.stderr.strip() or proc.returncode}")
            segments: list[Segment] = read_subtitles(out)
        if progress:
            progress(1.0)
        return Transcript(language=base, segments=segments, backend=self.name, model="apple-speech")
