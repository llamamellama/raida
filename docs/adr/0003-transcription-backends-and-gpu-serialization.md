# ADR-0003: Transcription runs in-process on MLX behind a protocol, one GPU job at a time

- Status: accepted
- Date: 2026-09-15

## Context

Speech-to-text has to be offline, multilingual, keep timestamps, and be fast on hour-long audio.
NVIDIA Parakeet TDT 0.6B v3 (25 European languages, automatic language identification, word
timestamps, no hallucination on silence) is the best accuracy-per-second on the Open ASR
Leaderboard long-form track; Whisper large-v3 covers 99 languages and can identify the language
of a clip. Both have MLX runtimes with Python APIs. Running two transcriptions at once on one
Metal GPU does not increase throughput: kernels serialize, and each extra process loads another
model copy.

## Decision

- A `Transcriber` protocol returns timestamped segments. Backends: `parakeet` (default, via
  parakeet-mlx), `whisper` (fallback and language detector, via mlx-whisper), `apple` (optional,
  Apple's on-device engine through the `yap` CLI on macOS 26+), `fake` (tests).
- Routing: sources default to `auto` language. A short clip goes through the detector; Parakeet
  handles languages it supports, Whisper handles the rest. The user can pin a language per source.
- Transcription runs in the app process (MLX cannot be forked) in a worker thread, guarded by a
  GPU semaphore of size 1. ffmpeg decoding and OCR overlap with it on the CPU.
- Model weights are cached under the app data directory; the process runs with
  `HF_HUB_OFFLINE=1` unless downloads are explicitly allowed.

## Consequences

- Hour-long audio transcribes in tens of seconds to a few minutes with no network access.
- Whisper-path mitigations for hallucination (no conditioning on previous text, no-speech
  filtering, repetition guard) are in code; voice-activity chunking is a follow-up.
- Adding another engine (for example Qwen3-ASR for CJK) is one class plus one registry entry.
