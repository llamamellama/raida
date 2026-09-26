"""CLI paths that are cheap to run in-process."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from raida import cli
from raida.models import ComponentStatus, HealthReport


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAIDA_LLM__MODEL", "fake-model")
    monkeypatch.setenv("RAIDA_LLM__BACKEND", "fake")
    monkeypatch.setenv("RAIDA_TRANSCRIBE__BACKEND", "fake")
    monkeypatch.setenv("RAIDA_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RAIDA_LOG_LEVEL", "INFO")


def test_serve_starts_with_a_warning_when_llm_not_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing model is a warning, not a crash: sources can still be processed."""
    _env(monkeypatch, tmp_path)
    ok = ComponentStatus(ok=True, detail="ok")
    report = HealthReport(
        ok=False,
        version="test",
        llm=ComponentStatus(ok=False, detail="model 'x' is not pulled"),
        transcriber=ok,
        ffmpeg=ok,
        ocr=ok,
        pdf_renderer=ok,
        storage=ok,
        data_dir=str(tmp_path),
    )

    async def fake_run_health(config: Any) -> HealthReport:
        return report

    served: dict[str, Any] = {}

    def fake_uvicorn_run(app: Any, **kwargs: Any) -> None:
        served.update(kwargs)

    import uvicorn

    import raida.api.app as app_module
    import raida.doctor as doctor_module

    monkeypatch.setattr(doctor_module, "run_health", fake_run_health)
    monkeypatch.setattr(app_module, "create_app", lambda config, initial_health=None: object())
    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    args = argparse.Namespace(config=None, port=8123, no_open=True)
    assert cli.cmd_serve(args) == 0
    assert served["port"] == 8123
    out = capsys.readouterr()
    assert "llm_not_ready" in out.out
    assert "not pulled" in out.out
