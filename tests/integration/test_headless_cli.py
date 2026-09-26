from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import FIXTURES


def test_cli_doctor_and_process(tmp_path: Path) -> None:
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("RAIDA_")},
        "RAIDA_LLM__MODEL": "fake-model",
        "RAIDA_LLM__BACKEND": "fake",
        "RAIDA_TRANSCRIBE__BACKEND": "fake",
        "RAIDA_OCR__ENABLED": "false",
        "RAIDA_PATHS__DATA_DIR": str(tmp_path / "data"),
        "RAIDA_LOG_LEVEL": "INFO",  # the production default; INFO-level log calls must work
    }
    doctor = subprocess.run(
        [sys.executable, "-m", "raida", "doctor"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "[OK  ] llm" in doctor.stdout
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "raida",
            "process",
            str(FIXTURES / "notes.md"),
            str(FIXTURES / "tone.wav"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Meeting notes" in proc.stdout
    assert "[00:00:00] Fake transcript segment 1." in proc.stdout


def test_cli_fails_fast_without_model(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RAIDA_")}
    env["RAIDA_PATHS__DATA_DIR"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, "-m", "raida", "doctor"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )
    assert r.returncode == 2
    assert "llm.model" in r.stderr
