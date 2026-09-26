"""Locale mapping and the yap retry, without macOS or yap."""

from __future__ import annotations

import subprocess

import pytest

from raida.transcribe import apple_speech
from raida.transcribe.languages import apple_locale, base_language


def test_apple_locale_keeps_regional_variants_and_maps_bases() -> None:
    assert apple_locale("zh-TW") == "zh-TW"
    assert apple_locale("zh_tw") == "zh-TW"
    assert apple_locale("zh-HK") == "zh-HK"
    assert apple_locale("zh") == "zh-CN"
    assert apple_locale("en-US") == "en-US"
    assert apple_locale("en") == "en-US"
    assert apple_locale("auto") is None
    assert apple_locale(None) is None
    assert apple_locale("sw") is None
    # Routing and caching still see the base language.
    assert base_language("zh-TW") == "zh"


def test_apple_transcriber_supports_regional_codes() -> None:
    t = apple_speech.AppleSpeechTranscriber()
    assert t.supports_language("zh-TW")
    assert t.supports_language("zh")
    assert not t.supports_language(None)
    assert not t.supports_language("sw")


def test_run_yap_retries_transient_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    outcomes = iter(
        [
            subprocess.CompletedProcess([], 1, "", "Downloading... Error: CancellationError()"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
    )

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return next(outcomes)

    monkeypatch.setattr(apple_speech.subprocess, "run", fake_run)
    monkeypatch.setattr(apple_speech.time, "sleep", lambda s: None)
    proc = apple_speech.run_yap(["yap", "transcribe"])
    assert proc.returncode == 0
    assert len(calls) == 2


def test_run_yap_does_not_retry_other_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess([], 1, "", "Error: File not found")

    monkeypatch.setattr(apple_speech.subprocess, "run", fake_run)
    monkeypatch.setattr(apple_speech.time, "sleep", lambda s: None)
    assert apple_speech.run_yap(["yap"]).returncode == 1
    assert len(calls) == 1


def test_run_yap_gives_up_after_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess([], 1, "", "Error: CancellationError()")

    monkeypatch.setattr(apple_speech.subprocess, "run", fake_run)
    monkeypatch.setattr(apple_speech.time, "sleep", lambda s: None)
    assert apple_speech.run_yap(["yap"], attempts=3).returncode == 1
    assert len(calls) == 3
