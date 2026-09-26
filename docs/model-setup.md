# Model setup

Everything runs natively on macOS (see `adr/0001-native-macos-hosting-no-containers.md`).
Containers on macOS cannot reach the Apple GPU, so Docker would only give slower CPU inference.

## The LLM

raida talks to a model server on localhost. On Apple Silicon the recommended server is
llama.cpp's `llama-server` (`llm.backend = "llama_server"`, started with `make llm`); Ollama
downloads the weights and can also serve them (`llm.backend = "ollama"`). Set `llm.backend`,
`llm.base_url` and `llm.model`. Why llama-server: see "llama-server" below and ADR-0005.

### Recommended models for a 96-128 GB MacBook Pro

| Role | Ollama tag | Disk | Context | Notes |
| --- | --- | --- | --- | --- |
| Default: fast, strong instruction following | `gpt-oss:120b` | ~65 GB | 131k | Mixture of experts, 5.1B active parameters, so it decodes quickly. Apache-2.0. |
| Quality alternative (128 GB only) | `qwen3.5:122b-a10b` | ~74 GB | 262k | Best quality per GB in this tier. Apache-2.0. |
| Long-context, low memory | `gemma4:31b` (Q8) | ~33 GB | 256k | Sliding-window attention keeps the KV cache small at long context; leaves memory for everything else. Apache-2.0. |

For smaller machines: 48-64 GB -> `gemma4:31b` Q8 or `qwen3.6:35b-a3b`; 24-36 GB ->
`gemma4:26b-a4b` or `qwen3.6:35b-a3b` Q4; 16 GB -> `gemma4:12b` with the synthesis budget
lowered to 16000-32000 (map-reduce will do the heavy lifting).

Prefer `-mlx` tags when Ollama offers one for your model (faster on Apple Silicon), but verify
the context length with `ollama ps` afterwards: some MLX tags currently ignore the configured
limit. If you see a smaller `CONTEXT` than requested, use the GGUF tag.

### Token estimates for non-Latin scripts

raida plans with a character heuristic (`llm.chars_per_token`, default 3.7, which fits English).
Chinese, Japanese and Korean tokenize at close to one token per character, so the estimator
counts those scripts separately. Measured on this project's M2 Max with `qwen3:30b-a3b` on a
Traditional Chinese transcript: 31,259 characters were 22,842 tokens (1.37 characters per
token, 0.83 tokens per CJK character; Simplified text of the same speech is about 0.66 tokens
per character). Four two-hour Mandarin recordings are therefore about 90k to 100k tokens, not
the 35k the old rule reported. Reading that many takes about 14 minutes on this machine (see
"How long reading a prompt takes"), which is why answers read notes. Plan budgets from real
token counts: the "in" figure on each answer is the server's own count.

### How long reading a prompt takes

Attention cost grows with the context already read, so reading time grows faster than prompt
length. llama.cpp build 11146 on the M2 Max with `qwen3:30b-a3b` read 2k new tokens at about
1,100 tok/s from an empty context and at about 300 tok/s with 16k already in it, which works
out to about 30 s for a 16k-token prompt, 100 s for 32k and 14 minutes for 100k. The engine is
not the lever: Ollama 0.30.10 read at about half that speed, batch size changed less than 8%,
and an 8-bit KV cache read prompts as fast but generated 25% slower. What helps is reading less
(notes) and reading once (the prompt cache).

### Context budget

Advertised context windows (128k-256k) are larger than the range where open models keep full
comprehension; published long-context benchmarks show degradation starting between 16k and 64k
tokens. Normal answers stay under `llm.interactive_budget_tokens` (32000). "Read full text"
answers use `llm.synthesis_budget_tokens` (64000) and condense sources above that. The server
must hold `budget + output reserve + overhead` tokens (74240 by default): Ollama is asked for
that `num_ctx`, and `make llm` starts llama-server with 81920 (`RAIDA_LLAMA_CTX`). Raise the
budgets only after `make bench` shows acceptable prefill time at that size and you have checked
answer quality on a real long input.

### Reasoning output

Qwen3 and gpt-oss reason before they answer. Ollama streams that reasoning in a separate
`thinking` field, which raida ignores, so it only delays the first visible token and counts
against `num_predict` (measured on this project's M2 Max with `qwen3:30b-a3b`: a one-word
answer spent a 64-token budget entirely on reasoning). Two knobs:

- `llm.think = false` turns reasoning off for hybrid models that have a non-thinking mode
  (the original Qwen3 2504 tags, Qwen3.5/3.6). Thinking-only tags ignore it: Ollama then skips
  its parser while the model's template still opens a `<think>` block, so the reasoning and a
  stray `</think>` end up in the answer. Ollama's current `qwen3:30b-a3b` is such a tag (the
  weights identify as "Qwen3 30B A3B Thinking 2507"); leave `think` unset for it.
- `llm.think = "low" | "medium" | "high"` sets the effort for gpt-oss, which cannot switch
  reasoning off.

With reasoning on, raise `llm.output_reserve_tokens` (16384 is comfortable) so a long think
cannot truncate the visible answer; `num_ctx` grows by the same amount.

llama-server handles both without settings in raida: `make llm` caps reasoning at 1024 tokens
per answer (`RAIDA_LLAMA_THINK_BUDGET`, then the model is told to write the answer), and for
note taking raida closes the reasoning block of thinking-only templates such as Qwen3 Thinking
2507, so notes are written directly (measured: 850 tokens of notes for a 4.4k-token excerpt in
23 s, instead of a long reasoning phase first).

### Ollama settings

The setup script and Makefile launch Ollama with:

```
OLLAMA_NUM_PARALLEL=1        # memory scales with parallel requests x context
OLLAMA_KEEP_ALIVE=1h         # keep the model loaded between prompts
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0    # halves KV memory with negligible quality loss
OLLAMA_NO_CLOUD=1            # a mistyped model name can never turn into a network call
```

`make ollama` starts a foreground server with exactly these variables.

### GPU memory cap

macOS caps GPU-allocatable memory at roughly two thirds to three quarters of unified memory. On
a 96 GB machine a 65 GB model plus a 74k-token KV cache exceeds the default cap; raise it with

```
sudo sysctl iogpu.wired_limit_mb=86016     # example: 84 GiB on a 96 GB machine; resets on reboot
```

Leave 8-16 GB for macOS and the app. On 128 GB the default cap is usually enough for the
recommended models.

Planning numbers: 4-bit weights take about 0.6 GB per billion parameters; KV cache bytes per
token = 2 x layers x kv_heads x head_dim x bytes_per_value (2 for f16, 1 for q8). Keep
weights + KV + macOS under about 70% of unified memory.

### Verifying

```
ollama ps
NAME             ID            SIZE     PROCESSOR    CONTEXT    UNTIL
gpt-oss:120b     ...           69 GB    100% GPU     74240      59 minutes from now
```

`PROCESSOR` must read `100% GPU`; a CPU split makes prefill many times slower. `raida doctor`
and the health strip in the UI report both values.

### Offline distribution

Ollama stores models under `~/.ollama/models` as content-addressed blobs plus manifests. Pull
once on a networked machine, copy that directory to the offline machine (or point
`OLLAMA_MODELS` at a copy), and Ollama serves them without any network access. Transcription
weights live in `<data_dir>/models/hf/hub` and copy the same way.

Downloads need `ollama.com` and `registry.ollama.ai` for Ollama, and `huggingface.co`,
`cdn-lfs.hf.co` and `*.xethub.hf.co` for Hugging Face. Corporate web filters commonly block
all of these while leaving PyPI and GitHub open; the symptom is a connection reset during the
TLS handshake (`curl: (35) ... Socket is not connected`). Request an exception for those
hosts or use the copy route above.

### llama-server

```
make llm MODEL=qwen3:30b-a3b          # scripts/llama-server.sh --ollama qwen3:30b-a3b
```

The script finds the GGUF file Ollama downloaded for the tag and starts llama-server on it in
place (`--gguf PATH` takes any GGUF file instead), with `--alias` set to the tag, which must
equal `llm.model`. It works when Ollama stored the model as a standard GGUF, verified for
`qwen3:30b-a3b`; a model Ollama keeps in its own layout may not load, and then the publisher's
GGUF with `--gguf` is the way. Settings, each overridable by environment variable:

| Setting | Value | Why |
| --- | --- | --- |
| `-np 3 --kv-unified` | 3 slots, one shared KV pool | an answer, a background note call and a read-ahead can each keep their prompt |
| `-c` (`RAIDA_LLAMA_CTX`) | 81920 | covers "Read full text" at the default budgets |
| KV cache type | f16 | 8-bit halves KV memory but generated 25% slower here (56 vs 73 tok/s at 4k context) |
| `--cache-ram` (`RAIDA_LLAMA_CACHE_MIB`) | 8192 MiB | keeps the prompts of idle sessions, so each session's sources are read once. A session over four two-hour recordings takes about 2.6 GB, so about three fit besides the three slots; raise it to keep more sessions warm |
| `--reasoning-budget` (`RAIDA_LLAMA_THINK_BUDGET`) | 1024 | bounds the silent thinking before an answer (about 25 s at 25k context) |
| `--sleep-idle-seconds` (`RAIDA_LLAMA_IDLE_S`) | 3600 | unloads the model after an hour without requests, reloads on the next |
| `-b 2048 -ub 2048`, `-fa on` | | 5 to 8% faster prompt reading than the defaults |

Memory with `qwen3:30b-a3b`: 18.6 GB of weights, about 8 GB of KV cache at 81920 tokens, and up
to 8 GB of cached prompts, about 35 GB at most, returned after an hour idle.

What raida uses beyond the OpenAI-compatible API: `/apply-template` and `/completion` to write
notes without reasoning, `max_tokens: 0` to read a session's prompt ahead of the question,
`cached_tokens` in usage, `/tokenize`, and `/props` for the doctor check (served alias, context
size, slots).

With Ollama instead, everything works, but Ollama runs one request slot, so every note-taking
call evicts the previous prompt and sessions are not read ahead; the first question in a
session then reads its sources, about 40 s for four two-hour recordings' notes.

## Speech-to-text

Backends (`transcribe.backend`):

| Backend | Model | Languages | Notes |
| --- | --- | --- | --- |
| `parakeet` (default) | `mlx-community/parakeet-tdt-0.6b-v3` via parakeet-mlx (MLX conversion of NVIDIA's weights: the library loads `config.json` and `model.safetensors`, which the `nvidia/` repo does not publish) | 25 European, automatic ID | Best accuracy per second on long-form English; word timestamps; does not hallucinate into silence. CC-BY-4.0. |
| `whisper` | `mlx-community/whisper-large-v3-mlx` via mlx-whisper | 99 | Fallback for other languages and the language detector. MIT. |
| `apple` | Apple SpeechAnalyzer via `yap` | ~30 locales | macOS 26+, `brew install yap`. Fastest, no download, needs an explicit language. |
| `fake` | none | any | Tests and UI development. |

Weights download once into `<data_dir>/models/hf` (`scripts/pull-models.sh`); afterwards the app
sets `HF_HUB_OFFLINE=1`. Set `transcribe.allow_model_download = true` only while fetching.

One transcription runs at a time on the GPU (`workers.gpu = 1`). Two processes on one Metal
device do not go faster; throughput comes from chunked processing inside one warm model.
Expect roughly one to three minutes for an hour of audio with Parakeet on an M3/M4 Max.

Speaker labels are not part of v1. If they become a requirement, `senko` (MIT, CoreML) or
FluidAudio's diarizer run offline on a Mac and can be merged onto the transcript by timestamp.

## OCR

Scanned PDF pages are rendered at 300 DPI and recognized with Apple Vision through `ocrmac`
(no model download, on-device, multilingual via `ocr.languages`). Requires `uv sync --extra mac`.

## Benchmarks

`make bench` appends measurements to `benchmarks.md`. Run it after any change of model, tag or
server, and use the prefill numbers at 32k and 64k tokens to set the synthesis budget.
