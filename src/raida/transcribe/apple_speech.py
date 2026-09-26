"""Apple's on-device SpeechAnalyzer (macOS 26+) through the `yap` CLI. Optional backend.

Needs an explicit locale: language detection must run first (or the user sets the language).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from raida.transcribe.base import (
    ProgressCallback,
    Segment,
    Transcriber,
    TranscriberError,
    Transcript,
)
from raida.transcribe.languages import apple_locale, base_language

# yap occasionally exits with "Downloading required assets... CancellationError()" for a locale
# whose assets are installed, typically while another yap process is running. A retry succeeds.
YAP_ATTEMPTS = 3
YAP_RETRY_DELAY_S = 2.0


def apple_speech_available() -> bool:
    if platform.system() != "Darwin" or shutil.which("yap") is None:
        return False
    try:
        major = int(platform.mac_ver()[0].split(".")[0] or 0)
    except ValueError:
        return False
    return major >= 26


def run_yap(cmd: list[str], attempts: int = YAP_ATTEMPTS) -> subprocess.CompletedProcess[str]:
    """Run yap, retrying the transient asset-download cancellation."""
    proc: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=False)
        transient = proc.returncode != 0 and "CancellationError" in proc.stderr
        if not transient or attempt == attempts:
            return proc
        time.sleep(YAP_RETRY_DELAY_S * attempt)
    assert proc is not None
    return proc


class AppleSpeechTranscriber(Transcriber):
    name = "apple"

    def supports_language(self, language: str | None) -> bool:
        return apple_locale(language) is not None

    def detect_language(self, wav_path: Path, seconds: int) -> str | None:
        return None

    def transcribe(
        self,
        wav_path: Path,
        *,
        language: str | None,
        progress: ProgressCallback | None = None,
    ) -> Transcript:
        locale = apple_locale(language)
        if locale is None:
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
                locale,
                "--srt",
                "--output-file",
                str(out),
                str(wav_path),
            ]
            proc = run_yap(cmd)
            if proc.returncode != 0 or not out.exists():
                detail = proc.stderr.strip() or str(proc.returncode)
                if "CancellationError" in detail:
                    detail += (
                        f". The Apple engine has no recognition assets for {locale} on this Mac "
                        "and could not download them; add the language under System Settings > "
                        "Keyboard > Dictation, or pick an installed language."
                    )
                raise TranscriberError(f"yap failed: {detail}")
            segments: list[Segment] = read_subtitles(out)
        if progress:
            progress(1.0)
        return Transcript(
            language=base_language(language), segments=segments, backend=self.name, model=locale
        )
