"""Routing decisions of the transcriber registry, without any model."""

from __future__ import annotations

from pathlib import Path

import pytest

from raida.transcribe.base import TranscriberError
from raida.transcribe.registry import TranscriberRegistry
from tests.conftest import make_config


def _apple_registry(tmp_path: Path, **overrides: str) -> TranscriberRegistry:
    config = make_config(
        tmp_path,
        RAIDA_TRANSCRIBE__BACKEND="apple",
        RAIDA_TRANSCRIBE__LANGUAGE_DETECTION="false",
        **overrides,
    )
    return TranscriberRegistry(config)


def test_apple_routes_supported_language_to_itself(tmp_path: Path) -> None:
    assert _apple_registry(tmp_path).route("zh").name == "apple"


def test_auto_language_without_detector_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(TranscriberError, match="Choose the language"):
        _apple_registry(tmp_path).route(None)


def test_unsupported_language_without_whisper_weights_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(TranscriberError, match=r"Whisper fallback .* is not downloaded"):
        _apple_registry(tmp_path).route("sw")


def test_fake_backend_handles_everything(tmp_path: Path) -> None:
    registry = TranscriberRegistry(make_config(tmp_path))
    assert registry.route(None).name == "fake"
    assert registry.route("sw").name == "fake"
