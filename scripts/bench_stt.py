"""Time each available transcription backend on one audio file.

Run: uv run python scripts/bench_stt.py path/to/audio-or-video [--backends parakeet,whisper]
Appends a markdown table to docs/benchmarks.md.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from raida.config import load_config
from raida.doctor import find_ffmpeg
from raida.model_env import apply_model_env
from raida.pipeline.stages.media import extract_wav, wav_duration_seconds
from raida.transcribe.registry import TranscriberRegistry


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("media")
    parser.add_argument("--backends", default="parakeet,whisper")
    parser.add_argument("--language", default=None)
    args = parser.parse_args()
    config = load_config()
    apply_model_env(config)
    ffmpeg = find_ffmpeg(config.transcribe.ffmpeg_path)
    if ffmpeg is None:
        raise SystemExit("ffmpeg not found")
    wav = config.media_dir / "bench.wav"
    config.ensure_dirs()
    await extract_wav(ffmpeg, Path(args.media), wav)
    duration = wav_duration_seconds(wav)
    registry = TranscriberRegistry(config)
    rows = []
    for name in args.backends.split(","):
        transcriber = registry.get(name)
        t0 = time.perf_counter()
        transcriber.transcribe(wav, language=args.language)  # first call includes model load
        load_and_run = time.perf_counter() - t0
        t1 = time.perf_counter()
        result = transcriber.transcribe(wav, language=args.language)
        warm = time.perf_counter() - t1
        rows.append(
            (
                name,
                result.model,
                round(duration),
                round(load_and_run, 1),
                round(warm, 1),
                round(duration / warm, 1),
                len(result.segments),
            )
        )
        print(rows[-1])
    table = [
        f"\n## Speech-to-text on {Path(args.media).name} ({time.strftime('%Y-%m-%d')})\n",
        "| backend | model | audio (s) | cold run (s) | warm run (s) | real-time factor "
        "| segments |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]}x | {r[6]} |" for r in rows),
    ]
    out = Path("docs/benchmarks.md")
    await asyncio.to_thread(_append, out, "\n".join(table) + "\n")
    print(f"appended to {out}")


def _append(path: Path, text: str) -> None:
    path.write_text((path.read_text() if path.exists() else "") + text)


if __name__ == "__main__":
    asyncio.run(main())
