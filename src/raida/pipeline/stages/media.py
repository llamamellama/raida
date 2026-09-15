"""Audio extraction for audio and video sources with ffmpeg."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path


class MediaError(RuntimeError):
    pass


async def extract_wav(ffmpeg: str, src: Path, dst: Path) -> None:
    """Decode any audio/video container to 16 kHz mono 16-bit PCM WAV."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip().splitlines()
        raise MediaError(
            f"ffmpeg failed on {src.name}: {detail[-1] if detail else 'unknown error'}"
        )
    size = await asyncio.to_thread(_file_size, dst)
    if size <= 44:
        raise MediaError(f"{src.name} has no decodable audio track")


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate() or 16000
        return frames / rate
