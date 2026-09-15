# ADR-0001: Models and the app run natively on macOS; no containers

- Status: accepted
- Date: 2026-09-15

## Context

raida must run fully offline on an Apple Silicon MacBook Pro and needs the GPU for LLM inference
and speech-to-text, and Apple Vision for OCR. Docker Desktop, OrbStack and Colima run Linux
virtual machines. None of them exposes the Apple GPU (Metal) to containers; Ollama's own FAQ
states that GPU acceleration is unavailable for Docker Desktop on macOS. Docker Model Runner
sidesteps this only by running llama.cpp as a native host process, which is not containerization.

## Decision

The model server, the transcription libraries and the raida application itself all run as native
processes on macOS. Installation is a shell script over Homebrew and `uv`; the app binds to
127.0.0.1 and is used from the browser on the same machine.

## Consequences

- Full Metal performance for the LLM and transcription, and Apple Vision for OCR.
- No compose file, no image builds, fewer moving parts.
- Running raida on a Linux host with NVIDIA GPUs would need a separate deployment recipe; that is
  out of scope for v1. The code keeps Apple-only imports lazy so the Linux test suite runs with
  fake backends.
