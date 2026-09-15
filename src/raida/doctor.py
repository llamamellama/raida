"""Environment checks shared by ``raida doctor`` and ``GET /api/health``.

Each check returns a ComponentStatus. Hard failures (things the app cannot work without) are
reported so ``serve`` can refuse to start; soft failures are warnings surfaced in the UI.
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
import shutil
from pathlib import Path

import httpx

from raida import __version__
from raida.config import Config
from raida.models import ComponentStatus, HealthReport

log = logging.getLogger(__name__)

MIN_FREE_DISK_BYTES = 10 * 1024**3


def find_ffmpeg(configured: str | None) -> str | None:
    """Return an ffmpeg executable path: configured, on PATH, or the bundled imageio binary."""
    if configured:
        return configured if Path(configured).exists() else None
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError:
        return None


def check_ffmpeg(config: Config) -> ComponentStatus:
    path = find_ffmpeg(config.transcribe.ffmpeg_path)
    if path is None:
        return ComponentStatus(
            ok=False,
            detail="ffmpeg not found. Install with `brew install ffmpeg` or "
            "`uv sync --extra bundled-ffmpeg`, or set transcribe.ffmpeg_path.",
        )
    return ComponentStatus(ok=True, detail=path, extra={"path": path})


def check_storage(config: Config) -> ComponentStatus:
    try:
        config.ensure_dirs()
        probe = config.paths.data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return ComponentStatus(ok=False, detail=f"data dir not writable: {exc}")
    usage = shutil.disk_usage(config.paths.data_dir)
    ok = usage.free >= MIN_FREE_DISK_BYTES
    return ComponentStatus(
        ok=ok,
        detail=f"{usage.free / 1024**3:.1f} GiB free"
        + ("" if ok else f" (below {MIN_FREE_DISK_BYTES / 1024**3:.0f} GiB minimum)"),
        extra={"free_bytes": usage.free, "total_bytes": usage.total},
    )


def check_ocr(config: Config) -> ComponentStatus:
    if not config.ocr.enabled:
        return ComponentStatus(ok=True, detail="disabled by config")
    if platform.system() != "Darwin":
        return ComponentStatus(
            ok=False, detail="OCR uses Apple Vision (ocrmac) and is only available on macOS."
        )
    try:
        importlib.import_module("ocrmac")
    except ImportError:
        return ComponentStatus(ok=False, detail="ocrmac not installed; run `uv sync --extra mac`.")
    return ComponentStatus(ok=True, detail="Apple Vision via ocrmac")


def check_pdf_renderer(config: Config) -> ComponentStatus:
    from raida.export.pdf import select_pdf_renderer

    try:
        renderer = select_pdf_renderer(config.export.pdf_renderer)
    except RuntimeError as exc:
        return ComponentStatus(ok=False, detail=str(exc))
    fallback = renderer.name == "fpdf2" and config.export.pdf_renderer == "auto"
    detail = renderer.name + (" (WeasyPrint unavailable; using basic fallback)" if fallback else "")
    return ComponentStatus(ok=True, detail=detail, extra={"renderer": renderer.name})


def check_transcriber(config: Config) -> ComponentStatus:
    from raida.transcribe.registry import describe_backend

    return describe_backend(config)


async def check_llm(config: Config) -> ComponentStatus:
    from raida.llm.registry import build_llm_backend

    backend = build_llm_backend(config)
    try:
        return await backend.health()
    except httpx.HTTPError as exc:
        return ComponentStatus(
            ok=False,
            detail=f"LLM server unreachable at {config.llm.base_url}: {exc}. "
            f"Start it (for Ollama: `ollama serve`) and pull `{config.llm.model}`.",
        )
    finally:
        await backend.aclose()


async def run_health(config: Config) -> HealthReport:
    llm = await check_llm(config)
    transcriber = check_transcriber(config)
    ffmpeg = check_ffmpeg(config)
    ocr = check_ocr(config)
    pdf = check_pdf_renderer(config)
    storage = check_storage(config)
    hard_ok = llm.ok and ffmpeg.ok and storage.ok and pdf.ok
    return HealthReport(
        ok=hard_ok and transcriber.ok,
        version=__version__,
        llm=llm,
        transcriber=transcriber,
        ffmpeg=ffmpeg,
        ocr=ocr,
        pdf_renderer=pdf,
        storage=storage,
        data_dir=str(config.paths.data_dir),
    )


def hard_failures(report: HealthReport) -> list[str]:
    """Checks that must pass for ``serve`` to start."""
    failures = []
    if not report.storage.ok:
        failures.append(f"storage: {report.storage.detail}")
    if not report.ffmpeg.ok:
        failures.append(f"ffmpeg: {report.ffmpeg.detail}")
    if not report.pdf_renderer.ok:
        failures.append(f"pdf renderer: {report.pdf_renderer.detail}")
    return failures


def format_report(report: HealthReport) -> str:
    rows = [
        ("llm", report.llm),
        ("transcriber", report.transcriber),
        ("ffmpeg", report.ffmpeg),
        ("ocr", report.ocr),
        ("pdf renderer", report.pdf_renderer),
        ("storage", report.storage),
    ]
    lines = [f"raida {report.version}  data dir: {report.data_dir}"]
    for name, status in rows:
        lines.append(f"  [{'OK  ' if status.ok else 'FAIL'}] {name:13s} {status.detail}")
    lines.append("overall: " + ("OK" if report.ok else "problems found"))
    return "\n".join(lines)


def performance_core_count() -> int:
    """Best-effort count of performance cores (macOS) or a conservative CPU fraction."""
    if platform.system() == "Darwin":
        try:
            import subprocess

            out = subprocess.run(
                ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout.strip()
            if out.isdigit() and int(out) > 0:
                return int(out)
        except (OSError, subprocess.SubprocessError):
            pass
    return max(1, (os.cpu_count() or 2) - 2)
