"""Lazy construction, caching and routing of transcriber backends."""

from __future__ import annotations

import logging
import platform

from raida.config import Config
from raida.models import ComponentStatus
from raida.transcribe.base import Transcriber, TranscriberError
from raida.transcribe.languages import base_language

log = logging.getLogger(__name__)


class TranscriberRegistry:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._instances: dict[str, Transcriber] = {}

    def get(self, name: str) -> Transcriber:
        if name in self._instances:
            return self._instances[name]
        tc = self.config.transcribe
        instance: Transcriber
        if name == "parakeet":
            from raida.transcribe.parakeet_mlx import ParakeetMlxTranscriber

            instance = ParakeetMlxTranscriber(tc.parakeet_model)
        elif name == "whisper":
            from raida.transcribe.mlx_whisper import MlxWhisperTranscriber

            instance = MlxWhisperTranscriber(tc.whisper_model)
        elif name == "apple":
            from raida.transcribe.apple_speech import AppleSpeechTranscriber

            instance = AppleSpeechTranscriber()
        elif name == "fake":
            from raida.transcribe.fake import FakeTranscriber

            instance = FakeTranscriber()
        else:
            raise TranscriberError(f"Unknown transcriber backend: {name}")
        self._instances[name] = instance
        return instance

    def primary(self) -> Transcriber:
        return self.get(self.config.transcribe.backend)

    def fallback(self) -> Transcriber:
        return self.get("fake" if self.config.transcribe.backend == "fake" else "whisper")

    def detector(self) -> Transcriber | None:
        """Backend able to identify the spoken language from a short clip."""
        if not self.config.transcribe.language_detection:
            return None
        primary = self.primary()
        if primary.name in {"whisper", "fake"}:
            return primary
        return self.fallback()

    def route(self, language: str | None) -> Transcriber:
        primary = self.primary()
        if primary.supports_language(language):
            return primary
        fallback = self.fallback()
        log.info(
            "transcriber_fallback",
            extra={"language": base_language(language), "from": primary.name, "to": fallback.name},
        )
        return fallback


def describe_backend(config: Config) -> ComponentStatus:
    name = config.transcribe.backend
    if name == "fake":
        return ComponentStatus(ok=True, detail="fake transcriber (tests only)")
    if platform.system() != "Darwin":
        return ComponentStatus(
            ok=False,
            detail=f"{name} needs Apple Silicon (MLX). On this platform only the fake "
            f"backend works.",
        )
    module = {"parakeet": "parakeet_mlx", "whisper": "mlx_whisper", "apple": None}.get(name)
    if module is not None:
        try:
            __import__(module)
        except ImportError:
            return ComponentStatus(
                ok=False, detail=f"{module} not installed; run `uv sync --extra mac`."
            )
    if name == "apple":
        from raida.transcribe.apple_speech import apple_speech_available

        if not apple_speech_available():
            return ComponentStatus(ok=False, detail="needs macOS 26+ and `brew install yap`.")
    model = (
        config.transcribe.parakeet_model if name == "parakeet" else config.transcribe.whisper_model
    )
    detail = f"{name} ({model})" if name != "apple" else "Apple SpeechAnalyzer via yap"
    if name in {"parakeet", "whisper"} and not _weights_cached(model, config):
        if config.transcribe.allow_model_download:
            detail += "; weights will download on first use"
        else:
            return ComponentStatus(
                ok=False,
                detail=f"{detail}: weights not in cache and allow_model_download is false. "
                f"Run `scripts/pull-models.sh` once while online.",
            )
    return ComponentStatus(ok=True, detail=detail, extra={"backend": name, "model": model})


def _weights_cached(repo_id: str, config: Config) -> bool:
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        return False
    try:
        info = scan_cache_dir(str(config.models_dir / "hf" / "hub"))
    except Exception:
        return False
    return any(repo.repo_id == repo_id for repo in info.repos)
