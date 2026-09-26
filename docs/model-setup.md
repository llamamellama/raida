# Model setup

Everything runs natively on macOS (see `adr/0001-native-macos-hosting-no-containers.md`).
Containers on macOS cannot reach the Apple GPU, so Docker would only give slower CPU inference.

## The LLM

raida talks to a model server on localhost. The default is Ollama; llama.cpp's `llama-server`
is the alternative for long-context tuning. Set `llm.backend`, `llm.base_url` and `llm.model`.

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

### Context budget

Advertised context windows (128k-256k) are larger than the range where open models keep full
comprehension; published long-context benchmarks show degradation starting between 16k and 64k
tokens. raida therefore defaults `llm.synthesis_budget_tokens` to 64000 and condenses sources
above that. The server is asked for `num_ctx = budget + output reserve + overhead` (74240 by
default). Raise the budget only after `make bench` shows acceptable prefill time at that size
and you have checked answer quality on a real long input.

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

### Using llama-server instead

```
llama-server -m model.gguf -c 74240 -fa on --port 8080
```

Then set `llm.backend = "openai_compatible"` and `llm.base_url = "http://127.0.0.1:8080"`.
llama-server offers per-slot KV quantization, prompt-cache save/restore and JSON-schema output,
and is reported to hold up better than MLX above ~30k tokens of context. Measure on your machine
with `make bench` before deciding.

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
