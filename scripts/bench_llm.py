"""Measure prefill and decode speed of the configured LLM server at several prompt sizes.

Run: uv run python scripts/bench_llm.py [--sizes 8000,32000,64000]
Appends a markdown table to docs/benchmarks.md.
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import subprocess
import time
from pathlib import Path

from raida.config import load_config
from raida.llm.base import GenerationOptions, Usage
from raida.llm.registry import build_llm_backend

FILLER = (
    "The committee reviewed the quarterly figures and noted a modest increase in regional "
    "demand, while cautioning that supply constraints could persist into the next period. "
)


def machine() -> str:
    if platform.system() == "Darwin":
        chip = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
        ).stdout.strip()
        mem_out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
        mem = int(mem_out.stdout) // 1024**3
        return f"{chip}, {mem} GB"
    return platform.platform()


def _append(path: Path, text: str) -> None:
    path.write_text((path.read_text() if path.exists() else "") + text)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="8000,32000,64000")
    parser.add_argument("--output-tokens", type=int, default=256)
    args = parser.parse_args()
    config = load_config()
    llm = build_llm_backend(config)
    rows = []
    try:
        for size in (int(s) for s in args.sizes.split(",")):
            chars = int(size * config.llm.chars_per_token)
            prompt = (FILLER * (chars // len(FILLER) + 1))[:chars]
            messages = [
                {"role": "system", "content": "Summarize the user's text in three sentences."},
                {"role": "user", "content": prompt},
            ]
            options = GenerationOptions(
                num_ctx=size + args.output_tokens + 1024,
                max_tokens=args.output_tokens,
                temperature=0.0,
            )
            usage = Usage()
            t0 = time.perf_counter()
            first = None
            async for _ in llm.stream_chat(messages, options, usage):
                if first is None:
                    first = time.perf_counter()
            t1 = time.perf_counter()
            ttft = (first or t1) - t0
            decode_s = t1 - (first or t1)
            prompt_tokens = usage.prompt_tokens or size
            completion = usage.completion_tokens or args.output_tokens
            prefill_rate = round(prompt_tokens / ttft) if ttft else 0
            decode_rate = round(completion / decode_s, 1) if decode_s else 0
            rows.append(
                (size, prompt_tokens, round(ttft, 1), prefill_rate, completion, decode_rate)
            )
            print(rows[-1])
    finally:
        await llm.aclose()
    header = (
        f"\n## LLM: {config.llm.backend} / {config.llm.model} on {machine()} "
        f"({time.strftime('%Y-%m-%d')})\n"
    )
    table = [
        header,
        "| requested tokens | prompt tokens | time to first token (s) | prefill tok/s "
        "| output tokens | decode tok/s |",
        "| --- | --- | --- | --- | --- | --- |",
        *(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |" for r in rows),
    ]
    out = Path("docs/benchmarks.md")
    await asyncio.to_thread(_append, out, "\n".join(table) + "\n")
    print(f"appended to {out}")


if __name__ == "__main__":
    asyncio.run(main())
